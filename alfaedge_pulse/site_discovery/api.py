# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Whitelisted endpoints for site discovery.

Viewers get the site list (count column, search, popup). Managing the NPM
and Cloudflare credentials is Proxmox Monitor Manager only, and secrets
are write-only: responses carry ``has_<secret>`` flags, never values; a
blank secret on save keeps the stored one, ``clear_<secret>`` removes it
(same contract as ``log_monitor.api.save_log_settings``).
"""

from __future__ import annotations

import frappe
from frappe.utils import cint
from frappe.utils.password import get_decrypted_password

from alfaedge_pulse.site_discovery.cloudflare_client import CloudflareAPIError
from alfaedge_pulse.site_discovery.npm_client import NPMAPIError
from alfaedge_pulse.site_discovery.sync import (
	CF_SETTINGS,
	cloudflare_client,
	enqueue_sync,
	npm_client_for,
	npm_effective_url,
)

VIEW_ROLES = ("System Manager", "Proxmox Monitor Manager", "Proxmox Monitor Viewer")
MANAGER_ROLES = ("Proxmox Monitor Manager",)

NPM_FIELDS = ("instance_name", "proxmox_server", "enabled", "public_ips", "url", "protocol", "port", "verify_ssl")
NPM_SECRETS = ("email", "password")
CF_FIELDS = ("enabled", "zones", "sync_hour")
SITE_FIELDS = [
	"name", "domain", "proxmox_guest", "source", "status", "npm_instance", "proxmox_server",
	"forward_target", "cloudflare_target", "cloudflare_proxied", "note", "also_seen_in", "last_seen",
]
PROBLEM_STATUSES = ("Ambiguous", "DNS Mismatch", "Not in Cloudflare", "Unmatched")


def _values(values) -> dict:
	return frappe.parse_json(values) if isinstance(values, str) else dict(values or {})


# ---------------------------------------------------------------------------
# Viewing
# ---------------------------------------------------------------------------


@frappe.whitelist()
def get_site_index() -> dict:
	"""``{"sites": {guest: [domain, ...]}, "can_manage": bool}`` — for the
	Sites count column, search, and whether to show the Site sources button."""
	frappe.only_for(VIEW_ROLES)
	index: dict[str, list[str]] = {}
	for r in frappe.get_all(
		"Instance Site", filters={"proxmox_guest": ["is", "set"], "status": "Active"}, fields=["proxmox_guest", "domain"], order_by="domain asc"
	):
		index.setdefault(r.proxmox_guest, []).append(r.domain)
	return {"sites": index, "can_manage": bool(set(frappe.get_roles()) & set(MANAGER_ROLES))}


@frappe.whitelist()
def get_instance_sites(guest: str | None = None, server: str | None = None) -> list[dict]:
	"""Full rows for one VM/CT's popup (``guest``), or the sites served
	through one Proxmox host's NPM (``server``).

	Verified (Active) sites only: an NPM entry Cloudflare doesn't actually
	send traffic to is a leftover, not a live site — those are listed under
	Site sources → Needs attention instead."""
	frappe.only_for(VIEW_ROLES)
	if guest:
		filters = {"proxmox_guest": guest, "status": "Active"}
	elif server:
		filters = {"proxmox_server": server, "status": "Active"}
	else:
		frappe.throw(frappe._("Pass guest or server"))
	return frappe.get_all("Instance Site", filters=filters, fields=SITE_FIELDS, order_by="domain asc")


# ---------------------------------------------------------------------------
# Managing sources (Proxmox Monitor Manager only)
# ---------------------------------------------------------------------------


def _npm_payload(doc) -> dict:
	data = {f: doc.get(f) for f in NPM_FIELDS}
	data.update({
		"name": doc.name,
		"effective_url": npm_effective_url(doc),
		"last_synced": doc.last_synced,
		"last_error": doc.last_error,
		"proxy_host_count": doc.proxy_host_count,
	})
	for f in NPM_SECRETS:
		data[f"has_{f}"] = bool(get_decrypted_password("NPM Instance", doc.name, f, raise_exception=False))
	return data


@frappe.whitelist()
def get_site_sources() -> dict:
	frappe.only_for(MANAGER_ROLES)
	cf = frappe.get_doc(CF_SETTINGS)
	return {
		"cloudflare": {
			**{f: cf.get(f) for f in CF_FIELDS},
			"has_api_token": bool(get_decrypted_password(CF_SETTINGS, CF_SETTINGS, "api_token", raise_exception=False)),
			"last_synced": cf.last_synced,
			"last_error": cf.last_error,
			"zone_count": cf.zone_count,
			"record_count": cf.record_count,
			"site_count": cf.site_count,
		},
		"npms": [
			_npm_payload(frappe.get_doc("NPM Instance", n))
			for n in frappe.get_all("NPM Instance", pluck="name", order_by="instance_name asc")
		],
		"problems": frappe.get_all(
			"Instance Site", filters={"status": ["in", PROBLEM_STATUSES]}, fields=SITE_FIELDS, order_by="status asc, domain asc"
		),
	}


def _apply_secrets(doc, values: dict, fields) -> None:
	for f in fields:
		if cint(values.get(f"clear_{f}")):
			doc.set(f, "")
		elif (values.get(f) or "").strip():
			doc.set(f, values[f].strip())


@frappe.whitelist(methods=["POST"])
def save_npm_instance(values, name: str | None = None) -> dict:
	frappe.only_for(MANAGER_ROLES)
	values = _values(values)
	doc = frappe.get_doc("NPM Instance", name) if name else frappe.new_doc("NPM Instance")
	if name and values.get("instance_name") and values["instance_name"] != doc.instance_name:
		frappe.throw(frappe._("Renaming an NPM instance isn't supported — delete it and add it again"))
	for f in NPM_FIELDS:
		if f in values:
			doc.set(f, values[f])
	_apply_secrets(doc, values, NPM_SECRETS)
	doc.save(ignore_permissions=True)
	return _npm_payload(doc)


@frappe.whitelist(methods=["POST"])
def delete_npm_instance(name: str) -> dict:
	frappe.only_for(MANAGER_ROLES)
	# Its sites stay until the next sync drops them (or re-attributes them to
	# another NPM) — deleting a source never deletes data on its own.
	frappe.db.set_value("Instance Site", {"npm_instance": name}, "npm_instance", None)
	frappe.delete_doc("NPM Instance", name, ignore_permissions=True)
	return {"ok": True}


@frappe.whitelist(methods=["POST"])
def save_cloudflare_settings(values) -> dict:
	frappe.only_for(MANAGER_ROLES)
	values = _values(values)
	doc = frappe.get_doc(CF_SETTINGS)
	for f in CF_FIELDS:
		if f in values:
			doc.set(f, values[f])
	_apply_secrets(doc, values, ("api_token",))
	doc.save(ignore_permissions=True)
	return get_site_sources()["cloudflare"]


def run_npm_test(doc, overrides: dict | None = None) -> dict:
	try:
		client = npm_client_for(doc, overrides)
		hosts = client.list_proxy_hosts()
	except (NPMAPIError, frappe.ValidationError) as e:
		return {"ok": False, "error": str(e)}
	domains = sum(len(h["domains"]) for h in hosts)
	return {
		"ok": True,
		"message": frappe._("Connected to {0} — {1} enabled proxy hosts, {2} domains").format(
			client.base_url, len(hosts), domains
		),
	}


def run_cloudflare_test(token_override: str | None = None, zones: str | None = None) -> dict:
	try:
		client = cloudflare_client(token_override)
		if zones is None:
			zones = frappe.db.get_single_value(CF_SETTINGS, "zones") or ""
		found = client.list_zones(zones.split(","))
	except (CloudflareAPIError, frappe.ValidationError) as e:
		return {"ok": False, "error": str(e)}
	names = ", ".join(z.get("name", "") for z in found[:8]) + (" …" if len(found) > 8 else "")
	return {"ok": True, "message": frappe._("Connected — {0} zone(s): {1}").format(len(found), names or "none")}


@frappe.whitelist(methods=["POST"])
def test_npm(values, name: str | None = None) -> dict:
	"""Test saved settings overlaid with unsaved dialog values."""
	frappe.only_for(MANAGER_ROLES)
	values = _values(values)
	doc = frappe.get_doc("NPM Instance", name) if name else frappe.new_doc("NPM Instance")
	for f in NPM_FIELDS:
		if f in values:
			doc.set(f, values[f])
	return run_npm_test(doc, {f: values.get(f) for f in NPM_SECRETS})


@frappe.whitelist(methods=["POST"])
def test_cloudflare(values=None) -> dict:
	frappe.only_for(MANAGER_ROLES)
	values = _values(values)
	return run_cloudflare_test(values.get("api_token"), values.get("zones"))


@frappe.whitelist(methods=["POST"])
def sync_now() -> dict:
	frappe.only_for(MANAGER_ROLES)
	enqueue_sync()
	return {"ok": True}
