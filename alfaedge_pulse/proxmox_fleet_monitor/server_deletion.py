# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Delete a disabled Proxmox Server and everything linked to it — after
archiving it.

Only a **disabled** server can be deleted, only by Proxmox Monitor
Manager, and always as a background job: a host with a few dozen VMs/CTs
drags along tens of thousands of backup, alert and metric-history rows.

What goes with the server:

- its VMs/CTs, datastores, backup logs, discovered sites (Instance Site)
  and its NPM Instance;
- every alert raised on any of those;
- Host Health (Monitored Host) records for its VMs/CTs, with their
  services, jobs, mounts and history — deleting a Monitored Host also
  revokes its agent key;
- encrypted secrets stored for the server, its NPM and its Monitored
  Hosts.

Recurring Errors that pointed at a deleted Monitored Host are kept, just
unlinked.

Before anything is deleted, a ``Proxmox Server Archive`` record is written
with a gzipped JSON attachment: full copies of every deleted record
(secrets excluded — Password fields only ever hold masks), and for bulky
metric history (``HISTORY_TABLES``) just counts and first/last dates. If
anything fails, the whole deletion is rolled back and the archive is
marked Failed with the error.
"""

from __future__ import annotations

import gzip
import json

import frappe
from frappe.utils import now_datetime

MANAGER_ROLES = ("Proxmox Monitor Manager",)
JOB_TIMEOUT = 3600
CHUNK = 500

#: Metric history: archived as counts + date range, not row by row.
HISTORY_TABLES = {
	"Proxmox Host Metric Log",
	"Proxmox Guest Metric Log",
	"Frappe Worker Health Log",
	"Frappe Failed Job Log",
	"Resource Metric Log",
	"Resource Disk Sample",
}


# ---------------------------------------------------------------------------
# Request (web) → background job
# ---------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def request_server_deletion(server: str, confirm_name: str) -> dict:
	"""Queue deletion of a disabled server. ``confirm_name`` must equal the
	server's name, as typed by the user."""
	frappe.only_for(MANAGER_ROLES)
	doc = frappe.get_doc("Proxmox Server", server)
	if doc.enabled:
		frappe.throw(frappe._("Disable {0} first — only a disabled server can be deleted.").format(doc.server_name))
	if (confirm_name or "").strip() != doc.server_name:
		frappe.throw(frappe._("Type the server name exactly ({0}) to confirm.").format(doc.server_name))
	if frappe.db.exists("Proxmox Server Archive", {"server_doc": server, "status": ["in", ("Queued", "Running")]}):
		frappe.throw(frappe._("{0} is already being deleted.").format(doc.server_name))

	archive = frappe.get_doc(
		{
			"doctype": "Proxmox Server Archive",
			"server_name": doc.server_name,
			"server_doc": doc.name,
			"hostname": doc.hostname,
			"role": doc.role,
			"server_type": doc.server_type,
			"requested_by": frappe.session.user,
			"requested_at": now_datetime(),
			"status": "Queued",
		}
	).insert(ignore_permissions=True)
	frappe.db.commit()

	frappe.enqueue(
		"alfaedge_pulse.proxmox_fleet_monitor.server_deletion.run_server_deletion",
		queue="long",
		timeout=JOB_TIMEOUT,
		job_id=f"alfaedge_pulse_delete_server::{server}",
		deduplicate=True,
		archive_name=archive.name,
	)
	return {"archive": archive.name, "message": frappe._("Deletion of {0} started in the background.").format(doc.server_name)}


@frappe.whitelist()
def get_server_deletions() -> dict:
	"""Disabled servers (deletable) and recent deletions — for the
	dashboard's managers-only panel."""
	frappe.only_for(MANAGER_ROLES)
	return {
		"disabled": frappe.get_all(
			"Proxmox Server",
			filters={"enabled": 0},
			fields=["name", "server_name", "hostname", "role", "server_type", "status", "last_synced"],
			order_by="server_name asc",
		),
		"archives": frappe.get_all(
			"Proxmox Server Archive",
			fields=["name", "server_name", "status", "requested_by", "requested_at", "completed_at", "archive_file", "error"],
			order_by="requested_at desc",
			limit=10,
		),
	}


# ---------------------------------------------------------------------------
# The job
# ---------------------------------------------------------------------------


def run_server_deletion(archive_name: str) -> None:
	archive = frappe.get_doc("Proxmox Server Archive", archive_name)
	archive.db_set("status", "Running", commit=True)
	try:
		plan = _collect(archive.server_doc)
		payload, summary = _build_archive(archive, plan)
		_attach_archive(archive, payload)
		_delete(plan)
		archive.db_set({"status": "Completed", "completed_at": now_datetime(), "summary": json.dumps(summary, indent=1), "error": None})
		frappe.db.commit()
	except Exception:
		frappe.db.rollback()
		frappe.get_doc("Proxmox Server Archive", archive_name).db_set(
			{"status": "Failed", "completed_at": now_datetime(), "error": frappe.get_traceback()[-4000:]}
		)
		frappe.db.commit()
		frappe.log_error(title=f"alfaEdge Pulse: deleting server {archive.server_name} failed", message=frappe.get_traceback())


def _names(doctype: str, filters) -> list[str]:
	return frappe.get_all(doctype, filters=filters, pluck="name")


def _union(doctype: str, *filter_sets) -> list[str]:
	names: set[str] = set()
	for f in filter_sets:
		if f is not None:
			names |= set(_names(doctype, f))
	return sorted(names)


def _in(values: list[str]):
	"""An ``in`` filter, or None when there's nothing to match (skipped by _union)."""
	return ["in", values] if values else None


def _collect(server: str) -> dict[str, list[str]]:
	"""Every record to archive and delete, as ``{doctype: [names]}``."""
	guests = _names("Proxmox Guest", {"server": server})
	datastores = _names("Proxmox Datastore", {"server": server})
	hosts = _names("Monitored Host", {"proxmox_guest": ["in", guests]}) if guests else []
	services = _names("Service Status Log", {"monitored_host": ["in", hosts]}) if hosts else []

	plan: dict[str, list[str]] = {
		"Proxmox Server": [server],
		"Proxmox Guest": guests,
		"Proxmox Datastore": datastores,
		"Proxmox Backup Log": _union(
			"Proxmox Backup Log", {"server": server}, {"guest": _in(guests)} if guests else None
		),
		"Instance Site": _union(
			"Instance Site", {"proxmox_server": server}, {"proxmox_guest": _in(guests)} if guests else None
		),
		"NPM Instance": _names("NPM Instance", {"proxmox_server": server}),
		"Proxmox Host Metric Log": _names("Proxmox Host Metric Log", {"server": server}),
		"Proxmox Guest Metric Log": _names("Proxmox Guest Metric Log", {"server": server}),
		"Monitored Host": hosts,
	}
	for dt in ("Service Status Log", "Frappe Worker Health Log", "Frappe Failed Job Log", "Frappe Active Job",
			"Resource Mount", "Resource Metric Log", "Resource Disk Sample"):
		plan[dt] = _names(dt, {"monitored_host": ["in", hosts]}) if hosts else []

	alert_refs = [server, *guests, *datastores, *hosts, *services]
	plan["Proxmox Alert Log"] = _names("Proxmox Alert Log", {"reference_name": ["in", alert_refs]})
	plan["Recurring Error (unlinked)"] = (
		_names("Recurring Error", {"monitored_host": ["in", hosts]}) if hosts else []
	)
	return plan


def _rows(doctype: str, names: list[str]) -> list[dict]:
	rows = []
	for i in range(0, len(names), CHUNK):
		rows += frappe.get_all(doctype, filters={"name": ["in", names[i : i + CHUNK]]}, fields=["*"])
	return rows


def _build_archive(archive, plan: dict[str, list[str]]) -> tuple[dict, dict]:
	records: dict[str, list] = {}
	history: dict[str, dict] = {}
	summary: dict[str, int] = {}
	for doctype, names in plan.items():
		summary[doctype] = len(names)
		if not names:
			continue
		if doctype == "Recurring Error (unlinked)":
			records[doctype] = names
		elif doctype in HISTORY_TABLES:
			first, last = frappe.db.sql(
				f"select min(creation), max(creation) from `tab{doctype}` where name in %(n)s", {"n": tuple(names)}
			)[0]
			history[doctype] = {"count": len(names), "first": str(first), "last": str(last)}
		else:
			rows = _rows(doctype, names)
			if doctype == "Monitored Host":
				# Child tables, so the archive shows each host's services and sites.
				for row in rows:
					for child in ("Expected Service", "Monitored Host Site"):
						row[frappe.scrub(child)] = frappe.get_all(
							child, filters={"parent": row["name"], "parenttype": "Monitored Host"}, fields=["*"]
						)
			records[doctype] = rows

	payload = {
		"archive": archive.name,
		"server": archive.server_name,
		"server_doc": archive.server_doc,
		"hostname": archive.hostname,
		"requested_by": archive.requested_by,
		"requested_at": str(archive.requested_at),
		"archived_at": str(now_datetime()),
		"note": "Secrets (API tokens, passwords, agent keys) are never included.",
		"records": records,
		"history_summary": history,
	}
	return payload, summary


def _attach_archive(archive, payload: dict) -> None:
	content = gzip.compress(frappe.as_json(payload).encode())
	stamp = now_datetime().strftime("%Y%m%d-%H%M%S")
	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"{frappe.scrub(archive.server_name)}-archive-{stamp}.json.gz",
			"attached_to_doctype": "Proxmox Server Archive",
			"attached_to_name": archive.name,
			"attached_to_field": "archive_file",
			"is_private": 1,
			"content": content,
		}
	).insert(ignore_permissions=True)
	archive.db_set("archive_file", file_doc.file_url)


def _bulk_delete(doctype: str, names: list[str]) -> None:
	for i in range(0, len(names), CHUNK):
		frappe.db.delete(doctype, {"name": ["in", names[i : i + CHUNK]]})


def _delete(plan: dict[str, list[str]]) -> None:
	"""Raw deletes in dependency order (children/logs first). Link checks
	are deliberately bypassed: everything that links to these records is
	itself in the plan."""
	hosts = plan["Monitored Host"]
	if plan["Recurring Error (unlinked)"]:
		frappe.db.set_value("Recurring Error", {"name": ["in", plan["Recurring Error (unlinked)"]]}, "monitored_host", None)

	for doctype in (
		"Proxmox Alert Log", "Service Status Log", "Frappe Worker Health Log", "Frappe Failed Job Log", "Frappe Active Job",
		"Resource Mount", "Resource Metric Log", "Resource Disk Sample",
		"Proxmox Host Metric Log", "Proxmox Guest Metric Log", "Proxmox Backup Log", "Instance Site",
	):
		_bulk_delete(doctype, plan[doctype])

	if hosts:
		for child in ("Expected Service", "Monitored Host Site"):
			frappe.db.delete(child, {"parent": ["in", hosts], "parenttype": "Monitored Host"})
	for doctype in ("Monitored Host", "NPM Instance", "Proxmox Datastore", "Proxmox Guest", "Proxmox Server"):
		_bulk_delete(doctype, plan[doctype])
		# Encrypted secrets stored for these records (server API token,
		# NPM login, Monitored Host agent secret).
		if plan[doctype]:
			frappe.db.delete("__Auth", {"doctype": doctype, "name": ["in", plan[doctype]]})
