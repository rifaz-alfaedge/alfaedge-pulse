# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Daily discovery of which site URLs live on which VM/CT.

Three sources, merged into one ``Instance Site`` row per domain:

1. **NPM** — every enabled Nginx Proxy Manager (one per Proxmox host)
   lists proxy hosts: domain(s) → forward host:port. The forward host is
   matched to a Proxmox Guest by IP (guests on the NPM's own Proxmox host
   first), then by name.
2. **Direct DNS** — a Cloudflare A/AAAA record pointing straight at a
   guest's own IP (instances with a public IP, no NPM in front).
3. **Pulse agent** — sites the Host Health agent reports per bench
   (Monitored Host Site), mapped through Monitored Host.proxmox_guest.

**Same domain in several NPMs** (e.g. a site moved between hosts and the
old proxy host was never deleted): the NPM whose ``public_ips`` contains
the domain's Cloudflare target wins. With no match the domain is marked
Ambiguous rather than guessed.

**Outages never wipe data**: rows owned by an NPM whose fetch failed this
run are kept as they are, and when Cloudflare is enabled but failed, no
row is deleted at all — only a fully successful view of a source can
remove what that source used to report.

Runs once a day after Cloudflare Settings' ``sync_hour`` (self-throttled
on the every-minute cron, like ``tasks/bifrost_sync.py``), or on demand
via "Sync now" — both enqueue the same deduped job.
"""

from __future__ import annotations

import ipaddress
from collections import defaultdict
from dataclasses import dataclass, field

import frappe
from frappe.utils import cint, get_datetime, now_datetime
from frappe.utils.password import get_decrypted_password

from alfaedge_pulse.log_monitor.connection import build_base_url, normalize_site
from alfaedge_pulse.site_discovery.cloudflare_client import CloudflareAPIError, CloudflareClient
from alfaedge_pulse.site_discovery.npm_client import NPMAPIError, NPMClient

SYNC_JOB_ID = "alfaedge_pulse_site_sync"
SYNC_JOB_TIMEOUT = 1800
CF_SETTINGS = "Cloudflare Settings"
MAX_CNAME_HOPS = 5


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------


def sync_sites() -> None:
	"""Cron entrypoint (every minute): enqueue the daily sync once it's due."""
	try:
		if not _anything_configured():
			return
		settings = frappe.get_cached_doc(CF_SETTINGS)
		now = now_datetime()
		hour = min(max(cint(settings.sync_hour), 0), 23)
		last = get_datetime(settings.last_synced) if settings.last_synced else None
		if now.hour < hour or (last and last.date() >= now.date()):
			return
		enqueue_sync()
	except Exception:
		frappe.log_error(title="alfaEdge Pulse: site sync scheduling failed", message=frappe.get_traceback())


def enqueue_sync() -> None:
	from alfaedge_pulse.tasks.jobs import clear_stale_job

	clear_stale_job(SYNC_JOB_ID, SYNC_JOB_TIMEOUT + 300, "site sync")
	frappe.enqueue(
		"alfaedge_pulse.site_discovery.sync.run_sync",
		queue="long",
		timeout=SYNC_JOB_TIMEOUT,
		job_id=SYNC_JOB_ID,
		deduplicate=True,
	)


def _anything_configured() -> bool:
	return bool(
		cint(frappe.db.get_single_value(CF_SETTINGS, "enabled"))
		or frappe.db.count("NPM Instance", {"enabled": 1})
	)


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass
class NPMResult:
	name: str
	server: str
	public_ips: set[str]
	ok: bool = False
	error: str = ""
	hosts: list[dict] = field(default_factory=list)


@dataclass
class CFResult:
	enabled: bool
	ok: bool = False
	error: str = ""
	records: dict[str, list[dict]] = field(default_factory=dict)  # name -> records
	zone_count: int = 0


def parse_ips(value: str | None) -> set[str]:
	return {v.strip() for v in (value or "").replace("\n", ",").split(",") if v.strip()}


def npm_effective_url(doc) -> str:
	if (doc.get("url") or "").strip():
		return doc.url.strip().rstrip("/")
	hostname = frappe.db.get_value("Proxmox Server", doc.proxmox_server, "hostname") if doc.proxmox_server else None
	if not hostname:
		return ""
	return build_base_url(doc.protocol or "http", hostname, cint(doc.port) or 81, "").rstrip("/")


def npm_client_for(doc, overrides: dict | None = None) -> NPMClient:
	"""Client for one NPM Instance doc; ``overrides`` (unsaved dialog values,
	blank secret = keep stored) lets Test run before Save."""
	overrides = overrides or {}
	secrets = {}
	for f in ("email", "password"):
		value = (overrides.get(f) or "").strip()
		if not value and doc.name and not doc.is_new():
			value = (get_decrypted_password("NPM Instance", doc.name, f, raise_exception=False) or "").strip()
		secrets[f] = value
	url = npm_effective_url(doc)
	if not url:
		raise NPMAPIError("No URL — set one, or pick a Proxmox host that has a hostname")
	return NPMClient(url, secrets["email"], secrets["password"], cint(doc.verify_ssl))


def cloudflare_client(token_override: str | None = None) -> CloudflareClient:
	token = (token_override or "").strip() or (
		get_decrypted_password(CF_SETTINGS, CF_SETTINGS, "api_token", raise_exception=False) or ""
	).strip()
	return CloudflareClient(token)


def fetch_npms() -> list[NPMResult]:
	results = []
	for name in frappe.get_all("NPM Instance", filters={"enabled": 1}, pluck="name"):
		doc = frappe.get_doc("NPM Instance", name)
		r = NPMResult(name=name, server=doc.proxmox_server, public_ips=parse_ips(doc.public_ips))
		try:
			r.hosts = npm_client_for(doc).list_proxy_hosts()
			r.ok = True
		except (NPMAPIError, frappe.ValidationError) as e:
			r.error = str(e)
		results.append(r)
	return results


def fetch_cloudflare() -> CFResult:
	settings = frappe.get_doc(CF_SETTINGS)
	result = CFResult(enabled=bool(cint(settings.enabled)))
	if not result.enabled:
		return result
	try:
		client = cloudflare_client()
		zones = client.list_zones([z for z in (settings.zones or "").split(",")])
		result.zone_count = len(zones)
		for zone in zones:
			for rec in client.list_records(zone["id"]):
				result.records.setdefault(rec["name"], []).append(rec)
		result.ok = True
	except (CloudflareAPIError, frappe.ValidationError) as e:
		result.error = str(e)
	return result


def load_guests() -> list[dict]:
	return frappe.get_all(
		"Proxmox Guest", fields=["name", "server", "guest_name", "ip_address", "public_ip"]
	)


def load_agent_sites() -> list[dict]:
	"""``[{domain, guest}]`` from the Pulse agent's per-bench site lists."""
	guest_by_host = dict(frappe.get_all("Monitored Host", fields=["name", "proxmox_guest"], as_list=True))
	out = []
	for row in frappe.get_all("Monitored Host Site", fields=["parent", "site_url", "site_name"]):
		guest = guest_by_host.get(row.parent)
		domain = normalize_site(row.site_url or row.site_name)
		# Frappe site names like "lulu" aren't resolvable domains — skip them.
		if guest and domain and "." in domain:
			out.append({"domain": domain, "guest": guest})
	return out


# ---------------------------------------------------------------------------
# Resolution (pure — no database access, so it can be tested in isolation)
# ---------------------------------------------------------------------------


def _is_ip(value: str) -> bool:
	try:
		ipaddress.ip_address(value)
		return True
	except ValueError:
		return False


def cf_lookup(records: dict[str, list[dict]], domain: str) -> tuple[str | None, bool, bool]:
	"""Follow Cloudflare records for ``domain`` to a final target.

	Returns ``(target, proxied, found)``: target is an IP when the chain ends
	at an A/AAAA record inside Cloudflare, else the last CNAME target
	(pointing outside Cloudflare). Wildcards (``*.example.com``) apply when
	no exact record exists.
	"""
	name, proxied, found = domain, False, False
	for hop in range(MAX_CNAME_HOPS + 1):
		recs = records.get(name)
		if not recs:
			parent = name.split(".", 1)[1] if "." in name else ""
			recs = records.get(f"*.{parent}") if parent else None
		if not recs:
			return (name if found else None), proxied, found
		found = True
		if hop == 0:
			proxied = any(r["proxied"] for r in recs)
		addresses = [r for r in recs if r["type"] in ("A", "AAAA")]
		if addresses:
			return addresses[0]["content"], proxied, True
		name = recs[0]["content"]
	return name, proxied, found


def _guest_for_target(target: str, server: str | None, guests: list[dict]) -> str | None:
	"""Match an NPM forward host (IP or hostname) to a Proxmox Guest."""
	target = (target or "").strip().lower()
	if not target:
		return None
	by_ip = [g for g in guests if target in {(g.ip_address or "").strip(), (g.public_ip or "").strip()}]
	if by_ip:
		same_host = [g for g in by_ip if g.server == server]
		return (same_host or by_ip)[0].name
	if not _is_ip(target):
		short = target.split(".")[0]
		by_name = [g for g in guests if (g.guest_name or "").lower() in (target, short)]
		if by_name:
			same_host = [g for g in by_name if g.server == server]
			return (same_host or by_name)[0].name
	return None


def resolve(
	npms: list[NPMResult], cf: CFResult, guests: list[dict], agent_sites: list[dict]
) -> dict[str, dict]:
	"""Merge every source into ``{domain: row-fields}``."""
	rows: dict[str, dict] = {}
	cf_checked = cf.enabled and cf.ok
	npm_ips = {n.name: n.public_ips for n in npms}

	# 1. NPM candidates per domain (one per NPM — a domain listed twice in
	#    the same NPM is still one candidate).
	candidates: dict[str, dict[str, dict]] = defaultdict(dict)
	for n in npms:
		if not n.ok:
			continue
		for h in n.hosts:
			for d in h["domains"]:
				d = normalize_site(d)
				if d and n.name not in candidates[d]:
					candidates[d][n.name] = {"npm": n, **h}

	for domain, by_npm in candidates.items():
		target, proxied, in_cf = cf_lookup(cf.records, domain) if cf_checked else (None, False, False)
		names = sorted(by_npm)
		chosen, status, note = None, "Active", ""

		if len(names) == 1:
			chosen = by_npm[names[0]]
			ips = npm_ips.get(names[0]) or set()
			if cf_checked and not in_cf:
				status, note = "Not in Cloudflare", "No Cloudflare DNS record for this domain."
			elif cf_checked and ips and target not in ips:
				status, note = "DNS Mismatch", f"Cloudflare points to {target}, not this NPM ({', '.join(sorted(ips))})."
			elif cf_checked and not ips:
				note = f"Set {names[0]}'s public IP to verify DNS (Cloudflare → {target})."
		else:
			matches = [nm for nm in names if target and target in (npm_ips.get(nm) or set())]
			listing = " + ".join(names)
			if len(matches) == 1:
				chosen = by_npm[matches[0]]
				note = f"In {listing}; Cloudflare → {target} = {matches[0]}."
			else:
				status = "Ambiguous"
				why = "Cloudflare not checked" if not cf_checked else (
					"no Cloudflare record" if not in_cf else f"Cloudflare → {target} matches none of their public IPs"
				)
				note = f"In {listing}; {why}."

		row = {
			"domain": domain,
			"source": "NPM",
			"status": status,
			"note": note,
			"cloudflare_target": target or "",
			"cloudflare_proxied": 1 if proxied else 0,
			"proxmox_guest": None,
			"npm_instance": None,
			"proxmox_server": None,
			"forward_target": "",
			"also_seen_in": "",
		}
		if chosen:
			n = chosen["npm"]
			port = f":{chosen['forward_port']}" if chosen.get("forward_port") else ""
			row.update(
				npm_instance=n.name,
				proxmox_server=n.server,
				forward_target=f"{chosen['forward_host']}{port}",
				proxmox_guest=_guest_for_target(chosen["forward_host"], n.server, guests),
			)
			if not row["proxmox_guest"] and status == "Active":
				row["status"] = "Unmatched"
				row["note"] = (row["note"] + " " if row["note"] else "") + (
					f"Forwards to {chosen['forward_host']}, which isn't a known VM/CT IP or name."
				)
		rows[domain] = row

	# 2. Direct DNS: Cloudflare A/AAAA pointing straight at a guest's own IP.
	if cf_checked:
		guest_by_ip: dict[str, str] = {}
		for g in guests:
			for ip in ((g.ip_address or "").strip(), (g.public_ip or "").strip()):
				if ip:
					guest_by_ip.setdefault(ip, g.name)
		for name, recs in cf.records.items():
			if name.startswith("*.") or name in rows:
				continue
			for r in recs:
				if r["type"] in ("A", "AAAA") and r["content"] in guest_by_ip:
					rows[name] = {
						"domain": name, "source": "Direct DNS", "status": "Active", "note": "",
						"cloudflare_target": r["content"], "cloudflare_proxied": 1 if r["proxied"] else 0,
						"proxmox_guest": guest_by_ip[r["content"]], "npm_instance": None,
						"proxmox_server": None, "forward_target": "", "also_seen_in": "",
					}
					break

	# 3. Pulse agent sites.
	for s in agent_sites:
		domain = s["domain"]
		if domain in rows:
			existing = rows[domain]
			seen = {x for x in existing["also_seen_in"].split(", ") if x}
			seen.add("Pulse agent")
			existing["also_seen_in"] = ", ".join(sorted(seen))
			if not existing["proxmox_guest"] and existing["status"] == "Unmatched":
				existing.update(proxmox_guest=s["guest"], status="Active", note=existing["note"] + " Matched via the Pulse agent.")
			continue
		target, proxied, in_cf = cf_lookup(cf.records, domain) if cf_checked else (None, False, False)
		rows[domain] = {
			"domain": domain, "source": "Pulse Agent",
			"status": "Not in Cloudflare" if cf_checked and not in_cf else "Active",
			"note": "Reported by the Pulse agent on this instance.",
			"cloudflare_target": target or "", "cloudflare_proxied": 1 if proxied else 0,
			"proxmox_guest": s["guest"], "npm_instance": None, "proxmox_server": None,
			"forward_target": "", "also_seen_in": "",
		}
	return rows


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


def run_sync() -> dict:
	"""One full sync. Returns a summary (handy from ``bench execute``)."""
	now = now_datetime()
	npms = fetch_npms()
	cf = fetch_cloudflare()
	rows = resolve(npms, cf, load_guests(), load_agent_sites())

	existing = {r.name: r for r in frappe.get_all("Instance Site", fields=["name", "npm_instance", "source"])}
	failed_npms = {n.name for n in npms if not n.ok}
	cf_failed = cf.enabled and not cf.ok
	for domain, fields in rows.items():
		fields["last_seen"] = now
		if domain in existing:
			# A partial view must not overwrite good data: a row owned by an
			# NPM that failed this run keeps its owner (rather than being
			# re-attributed to whichever other NPM still lists the domain),
			# and with Cloudflare down existing rows keep their DNS details.
			if cf_failed or existing[domain].npm_instance in failed_npms:
				frappe.db.set_value("Instance Site", domain, "last_seen", now, update_modified=False)
				continue
			frappe.db.set_value("Instance Site", domain, fields, update_modified=True)
		else:
			frappe.get_doc({"doctype": "Instance Site", **fields}).insert(ignore_permissions=True)

	deleted = 0
	if not cf_failed:
		for name, row in existing.items():
			if name in rows or (row.npm_instance and row.npm_instance in failed_npms):
				continue
			frappe.delete_doc("Instance Site", name, ignore_permissions=True, force=True)
			deleted += 1

	for n in npms:
		frappe.db.set_value(
			"NPM Instance", n.name,
			{"last_synced": now, "last_error": n.error[:1000] if n.error else None,
			 **({"proxy_host_count": len(n.hosts)} if n.ok else {})},
			update_modified=False,
		)
	cf_status = {"last_synced": now, "site_count": len(rows), "last_error": cf.error[:1000] if cf.error else None}
	if cf.ok:
		cf_status.update(zone_count=cf.zone_count, record_count=sum(len(v) for v in cf.records.values()))
	frappe.db.set_single_value(CF_SETTINGS, cf_status)
	frappe.db.commit()

	return {
		"sites": len(rows),
		"deleted": deleted,
		"npm_ok": [n.name for n in npms if n.ok],
		"npm_failed": {n.name: n.error for n in npms if not n.ok},
		"cloudflare": "ok" if cf.ok else ("disabled" if not cf.enabled else cf.error),
	}
