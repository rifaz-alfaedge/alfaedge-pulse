# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Whitelisted endpoints for the Uptime dashboard tab.

The write endpoints (add/edit/delete/pause/resume) are Manager-only —
same tier as the credential-bearing ``Uptime Kuma Instance`` doctype —
since each one makes a live Socket.IO call against the target Kuma
instance, not just a local database write. Read endpoints follow the
same three-role pattern as ``Uptime Site``/``Uptime Check Log`` itself.

**Instances are kept in sync by ``site_name``**, deliberately: multiple
Kuma instances exist precisely to monitor the same sites from different
locations (redundancy against one location's own network issues), so
"add a site" / edit / pause / resume / delete all fan out to every
``Uptime Site`` sharing that name across every enabled instance, and a
newly added (or newly reachable) instance catches up to whatever the
fleet already has via ``sync_missing_sites_to_instance`` on the poller's
schedule (see ``tasks.uptime_kuma_poller``). Each instance still keeps
its own independent ``current_status``/``is_critical``/check history —
only the site *definition* (name/type/url/interval/active) is shared.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, flt, now_datetime
from frappe.utils.password import get_decrypted_password

from proxmox_monitor.uptime_kuma_client.base import UptimeKumaAPIError
from proxmox_monitor.uptime_kuma_client.socketio_client import MONITOR_TYPE_FROM_KUMA, UptimeKumaClient

WRITE_ROLES = ("System Manager", "Proxmox Monitor Manager")
READ_ROLES = ("System Manager", "Proxmox Monitor Manager", "Proxmox Monitor Viewer")


def _get_client(instance_name: str) -> UptimeKumaClient:
	instance = frappe.get_doc("Uptime Kuma Instance", instance_name)
	password = get_decrypted_password("Uptime Kuma Instance", instance_name, "password", raise_exception=False)
	return UptimeKumaClient(instance.base_url, instance.username, password, verify_ssl=bool(instance.verify_ssl))


def _create_site_on_instance(
	instance: str,
	site_name: str,
	monitor_type: str,
	url: str | None,
	hostname: str | None,
	port: int | None,
	check_interval_seconds: int,
	is_active: int,
):
	"""Create the Kuma monitor first, then the local doc — if the local
	doc then fails to save, best-effort delete the just-created Kuma
	monitor rather than leave an orphan Kuma has but we don't know about."""
	site = frappe.new_doc("Uptime Site")
	site.update(
		{
			"instance": instance,
			"site_name": site_name,
			"monitor_type": monitor_type,
			"url": url,
			"hostname": hostname,
			"port": cint(port) if port else None,
			"check_interval_seconds": cint(check_interval_seconds) or 60,
			"is_active": cint(is_active),
		}
	)

	client = _get_client(instance)
	try:
		monitor_id = client.add_monitor(site)
	finally:
		client.disconnect()

	site.kuma_monitor_id = monitor_id
	try:
		site.insert(ignore_permissions=True)
	except Exception:
		client = _get_client(instance)
		try:
			client.delete_monitor(monitor_id)
		except UptimeKumaAPIError:
			frappe.log_error(
				title="Uptime Monitor: orphaned Kuma monitor after local save failure",
				message=f"instance={instance} monitor_id={monitor_id} — {frappe.get_traceback()}",
			)
		finally:
			client.disconnect()
		raise
	return site


@frappe.whitelist()
def add_site(
	site_name: str,
	monitor_type: str,
	url: str | None = None,
	hostname: str | None = None,
	port: int | None = None,
	check_interval_seconds: int = 60,
	is_active: int = 1,
) -> dict:
	"""Adds this site to every enabled Uptime Kuma Instance — see the
	module docstring for why. One instance failing to create it doesn't
	block the others; failures are reported back rather than raised, so a
	partial fan-out doesn't look like total failure in the UI."""
	frappe.only_for(WRITE_ROLES)
	instances = frappe.get_all("Uptime Kuma Instance", filters={"enabled": 1}, pluck="name")
	if not instances:
		frappe.throw(_("No enabled Uptime Kuma Instance to add this site to."))

	created = []
	errors = []
	for instance in instances:
		try:
			site = _create_site_on_instance(
				instance, site_name, monitor_type, url, hostname, port, check_interval_seconds, is_active
			)
			created.append(site.name)
		except Exception as e:
			frappe.log_error(title=f"Uptime Monitor: failed to add site on {instance}", message=frappe.get_traceback())
			errors.append(f"{instance}: {e}")

	if not created:
		frappe.db.rollback()
		frappe.throw(_("Failed to add this site to any instance: {0}").format("; ".join(errors)))

	frappe.db.commit()
	return {"created": created, "errors": errors}


@frappe.whitelist()
def list_importable_monitors(instance: str) -> list[dict]:
	"""Kuma monitors on this instance that don't already have a matching
	local Uptime Site — for the "Import Existing Monitors" picker. Covers
	monitors created directly in Kuma (before this app knew about that
	instance, or just never added here) rather than through ``add_site``.
	Monitor types this app doesn't model (dns/docker/group/mqtt/etc.) are
	left out, since there'd be nowhere to put their config locally."""
	frappe.only_for(READ_ROLES)
	client = _get_client(instance)
	try:
		monitors = client.list_monitors()
	finally:
		client.disconnect()

	already_imported = {
		cint(v) for v in frappe.get_all("Uptime Site", filters={"instance": instance}, pluck="kuma_monitor_id")
	}

	result = []
	for kuma_id, m in monitors.items():
		kuma_id = cint(kuma_id)
		if kuma_id in already_imported:
			continue
		monitor_type = MONITOR_TYPE_FROM_KUMA.get(m.get("type"))
		if not monitor_type:
			continue
		result.append(
			{
				"kuma_monitor_id": kuma_id,
				"site_name": m.get("name"),
				"monitor_type": monitor_type,
				"url": m.get("url"),
				"hostname": m.get("hostname"),
				"port": m.get("port"),
				"check_interval_seconds": cint(m.get("interval")) or 60,
				"is_active": 1 if m.get("active") else 0,
			}
		)
	return result


def _import_monitors_from_map(instance: str, monitors: dict, kuma_ids: list[int]) -> dict:
	"""Shared by the user-initiated ``import_monitors`` (a chosen subset)
	and the scheduled ``auto_import_new_monitors`` (every monitor found) —
	both just differ in which IDs they pass in."""
	created = []
	skipped = []
	for raw_id in kuma_ids:
		kuma_id = cint(raw_id)
		m = monitors.get(str(kuma_id))
		if not m:
			skipped.append(f"Monitor {kuma_id}: no longer exists on Kuma")
			continue
		monitor_type = MONITOR_TYPE_FROM_KUMA.get(m.get("type"))
		if not monitor_type:
			skipped.append(f"{m.get('name')}: unsupported monitor type '{m.get('type')}'")
			continue
		if frappe.db.exists("Uptime Site", {"instance": instance, "kuma_monitor_id": kuma_id}):
			skipped.append(f"{m.get('name')}: already imported")
			continue

		site = frappe.new_doc("Uptime Site")
		site.update(
			{
				"instance": instance,
				"site_name": m.get("name"),
				"kuma_monitor_id": kuma_id,
				"monitor_type": monitor_type,
				"url": m.get("url"),
				"hostname": m.get("hostname"),
				"port": cint(m.get("port")) if m.get("port") else None,
				"check_interval_seconds": cint(m.get("interval")) or 60,
				"is_active": 1 if m.get("active") else 0,
			}
		)
		site.insert(ignore_permissions=True)
		created.append(site.name)

	return {"created": created, "skipped": skipped}


@frappe.whitelist()
def import_monitors(instance: str, kuma_monitor_ids: list[int]) -> dict:
	"""Creates local Uptime Site docs mirroring existing Kuma monitors —
	unlike ``add_site``, nothing is created on Kuma's side since these
	monitors already exist there."""
	frappe.only_for(WRITE_ROLES)
	client = _get_client(instance)
	try:
		monitors = client.list_monitors()
	finally:
		client.disconnect()

	result = _import_monitors_from_map(instance, monitors, kuma_monitor_ids)
	frappe.db.commit()
	return result


def auto_import_new_monitors(instance: str) -> dict:
	"""Called only from ``tasks.uptime_kuma_poller`` on its self-throttled
	schedule — not whitelisted, no user selection: every currently
	unimported HTTP(s)/TCP/Ping monitor on this instance is imported, so
	sites added directly in Kuma show up here without anyone visiting the
	"Import Existing Monitors" dialog."""
	client = _get_client(instance)
	try:
		monitors = client.list_monitors()
	finally:
		client.disconnect()

	result = _import_monitors_from_map(instance, monitors, [cint(k) for k in monitors])
	frappe.db.commit()
	return result


def sync_missing_sites_to_instance(instance: str) -> dict:
	"""Called only from ``tasks.uptime_kuma_poller``, right after
	``auto_import_new_monitors`` above — the other half of "keep instances
	in sync": creates a Kuma monitor + local doc here for every site_name
	already tracked on some *other* enabled instance but missing from this
	one. This is what makes a newly added (or newly reachable) instance
	catch up to the rest of the fleet on its own, without anyone needing
	to re-add every site by hand.

	The oldest existing row for a given site_name is used as the template
	(name/type/url/hostname/port/interval/active) — if that config has
	since drifted between instances (e.g. someone edited it directly in
	Kuma on just one of them), this doesn't attempt to reconcile that; it
	only fills in what's missing outright."""
	existing_here = set(frappe.get_all("Uptime Site", filters={"instance": instance}, pluck="site_name"))

	other_enabled_instances = frappe.get_all(
		"Uptime Kuma Instance", filters={"enabled": 1, "name": ["!=", instance]}, pluck="name"
	)
	if not other_enabled_instances:
		return {"created": [], "errors": []}

	other_rows = frappe.get_all(
		"Uptime Site",
		filters={"instance": ["in", other_enabled_instances]},
		fields=["site_name", "monitor_type", "url", "hostname", "port", "check_interval_seconds", "is_active", "creation"],
		order_by="creation asc",
	)
	templates: dict[str, dict] = {}
	for row in other_rows:
		templates.setdefault(row.site_name, row)

	created = []
	errors = []
	for site_name, template in templates.items():
		if site_name in existing_here:
			continue
		try:
			site = _create_site_on_instance(
				instance,
				site_name,
				template.monitor_type,
				template.url,
				template.hostname,
				template.port,
				template.check_interval_seconds,
				template.is_active,
			)
			created.append(site.name)
		except Exception as e:
			frappe.log_error(
				title=f"Uptime Monitor: failed to sync '{site_name}' onto {instance}", message=frappe.get_traceback()
			)
			errors.append(f"{site_name}: {e}")

	frappe.db.commit()
	return {"created": created, "errors": errors}


def _sibling_names(site_name: str) -> list[str]:
	"""Every ``Uptime Site`` docname sharing this site_name, across every
	instance — the "group" that edit/delete/pause/resume fan out to."""
	return frappe.get_all("Uptime Site", filters={"site_name": site_name}, pluck="name")


def _log_sibling_failure(action: str, sibling_name: str) -> None:
	"""A per-sibling failure inside a fan-out is reported back in the
	response's ``errors`` list for the UI, but that toast can vanish
	(e.g. the very card that failed to delete disappears once its
	successful siblings do) — logging it too keeps a paper trail."""
	frappe.log_error(
		title=f"Uptime Monitor: failed to {action} sibling {sibling_name}", message=frappe.get_traceback()
	)


@frappe.whitelist()
def edit_site(
	name: str,
	site_name: str | None = None,
	url: str | None = None,
	hostname: str | None = None,
	port: int | None = None,
	check_interval_seconds: int | None = None,
	is_active: int | None = None,
) -> dict:
	"""Applies the edit to every sibling of ``name`` (same site_name,
	across every instance) — see the module docstring for why."""
	frappe.only_for(WRITE_ROLES)
	original = frappe.get_doc("Uptime Site", name)
	updates = {
		"site_name": site_name,
		"url": url,
		"hostname": hostname,
		"port": cint(port) if port is not None else None,
		"check_interval_seconds": cint(check_interval_seconds) if check_interval_seconds is not None else None,
		"is_active": cint(is_active) if is_active is not None else None,
	}

	updated = []
	errors = []
	for sibling_name in _sibling_names(original.site_name):
		try:
			site = frappe.get_doc("Uptime Site", sibling_name)
			for field, value in updates.items():
				if value is not None:
					site.set(field, value)

			client = _get_client(site.instance)
			try:
				client.edit_monitor(site.kuma_monitor_id, site)
			finally:
				client.disconnect()

			site.save(ignore_permissions=True)
			updated.append(site.name)
		except Exception as e:
			_log_sibling_failure("edit", sibling_name)
			errors.append(f"{sibling_name}: {e}")

	frappe.db.commit()
	return {"updated": updated, "errors": errors}


@frappe.whitelist()
def delete_site(name: str) -> dict:
	"""Deletes every sibling of ``name`` (same site_name, across every
	instance), both on Kuma and locally — see the module docstring."""
	frappe.only_for(WRITE_ROLES)
	site_name = frappe.db.get_value("Uptime Site", name, "site_name")
	deleted = []
	errors = []
	for sibling_name in _sibling_names(site_name):
		try:
			sibling = frappe.get_doc("Uptime Site", sibling_name)
			client = _get_client(sibling.instance)
			try:
				client.delete_monitor(sibling.kuma_monitor_id)
			finally:
				client.disconnect()
			frappe.delete_doc("Uptime Site", sibling_name, ignore_permissions=True)
			deleted.append(sibling_name)
		except Exception as e:
			_log_sibling_failure("delete", sibling_name)
			errors.append(f"{sibling_name}: {e}")
	frappe.db.commit()
	return {"deleted": deleted, "errors": errors}


@frappe.whitelist()
def pause_site(name: str) -> dict:
	"""Pauses every sibling of ``name`` (same site_name, across every
	instance) — see the module docstring."""
	frappe.only_for(WRITE_ROLES)
	site_name = frappe.db.get_value("Uptime Site", name, "site_name")
	paused = []
	errors = []
	for sibling_name in _sibling_names(site_name):
		try:
			sibling = frappe.get_doc("Uptime Site", sibling_name)
			client = _get_client(sibling.instance)
			try:
				client.pause_monitor(sibling.kuma_monitor_id)
			finally:
				client.disconnect()
			frappe.db.set_value("Uptime Site", sibling_name, "is_active", 0)
			paused.append(sibling_name)
		except Exception as e:
			_log_sibling_failure("pause", sibling_name)
			errors.append(f"{sibling_name}: {e}")
	frappe.db.commit()
	return {"paused": paused, "errors": errors}


@frappe.whitelist()
def resume_site(name: str) -> dict:
	"""Resumes every sibling of ``name`` (same site_name, across every
	instance) — see the module docstring."""
	frappe.only_for(WRITE_ROLES)
	site_name = frappe.db.get_value("Uptime Site", name, "site_name")
	resumed = []
	errors = []
	for sibling_name in _sibling_names(site_name):
		try:
			sibling = frappe.get_doc("Uptime Site", sibling_name)
			client = _get_client(sibling.instance)
			try:
				client.resume_monitor(sibling.kuma_monitor_id)
			finally:
				client.disconnect()
			frappe.db.set_value("Uptime Site", sibling_name, "is_active", 1)
			resumed.append(sibling_name)
		except Exception as e:
			_log_sibling_failure("resume", sibling_name)
			errors.append(f"{sibling_name}: {e}")
	frappe.db.commit()
	return {"resumed": resumed, "errors": errors}


@frappe.whitelist()
def get_uptime_summary(site: str | None = None, days: int = 7) -> dict:
	frappe.only_for(READ_ROLES)
	days = cint(days) or 7
	conditions = "checked_at >= %(start)s"
	values: dict = {"start": add_to_date(now_datetime(), days=-days)}
	if site:
		conditions += " and site = %(site)s"
		values["site"] = site

	row = frappe.db.sql(
		f"""
		select
			count(*) as total_checks,
			coalesce(sum(is_up), 0) as up_checks,
			coalesce(avg(response_time_ms), 0) as avg_response_time
		from `tabUptime Check Log`
		where {conditions}
		""",
		values,
		as_dict=True,
	)[0]
	total = cint(row.total_checks)
	return {
		"total_checks": total,
		"uptime_percent": flt((cint(row.up_checks) / total) * 100, 2) if total else None,
		"avg_response_time_ms": flt(row.avg_response_time, 1),
	}


@frappe.whitelist()
def get_uptime_history(site: str, days: int = 7) -> list[dict]:
	frappe.only_for(READ_ROLES)
	days = cint(days) or 7
	rows = frappe.db.sql(
		"""
		select
			date(checked_at) as bucket,
			count(*) as total_checks,
			coalesce(sum(is_up), 0) as up_checks,
			coalesce(avg(response_time_ms), 0) as avg_response_time
		from `tabUptime Check Log`
		where site = %(site)s and checked_at >= %(start)s
		group by bucket
		order by bucket asc
		""",
		{"site": site, "start": add_to_date(now_datetime(), days=-days)},
		as_dict=True,
	)
	return [
		{
			"bucket": str(r.bucket),
			"uptime_percent": flt((cint(r.up_checks) / cint(r.total_checks)) * 100, 2) if r.total_checks else None,
			"avg_response_time_ms": flt(r.avg_response_time, 1),
		}
		for r in rows
	]
