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
``Uptime Site`` sharing that name across every instance, and a
newly added (or newly reachable) instance catches up to whatever the
fleet already has via ``sync_missing_sites_to_instance`` on the poller's
schedule (see ``tasks.uptime_kuma_poller``). Each instance still keeps
its own independent ``current_status``/``is_critical``/check history —
only the site *definition* (name/type/url/interval/active) is shared.

**A disabled instance is never contacted.** Edit/pause/resume/delete on
one of its sites change only Pulse's own ``Uptime Site`` record and say so
in the response's ``notes`` — so a disabled instance is a safe way to keep
Pulse from writing to a Kuma it shouldn't touch (e.g. production's, on a
dev copy of this site).
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, flt, now_datetime
from frappe.utils.password import get_decrypted_password

from alfaedge_pulse.uptime_kuma_client.base import UptimeKumaAPIError
from alfaedge_pulse.uptime_kuma_client.socketio_client import MONITOR_TYPE_FROM_KUMA, UptimeKumaClient

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


def _disabled_instances() -> set[str]:
	return set(frappe.get_all("Uptime Kuma Instance", filters={"enabled": 0}, pluck="name"))


def _local_only_note(instance: str) -> str:
	return f"{instance} is disabled — changed in Pulse only, not on Uptime Kuma"


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

	disabled = _disabled_instances()
	updated = []
	errors = []
	notes = []
	for sibling_name in _sibling_names(original.site_name):
		try:
			site = frappe.get_doc("Uptime Site", sibling_name)
			for field, value in updates.items():
				if value is not None:
					site.set(field, value)

			if site.instance in disabled:
				notes.append(_local_only_note(site.instance))
			else:
				client = _get_client(site.instance)
				try:
					client.edit_monitor(site.kuma_monitor_id, site)
				finally:
					client.disconnect()

			site.save(ignore_permissions=True)
			updated.append(site.name)
			if site_name and site_name != original.site_name:
				# Windows are keyed by site_name, so they follow a rename.
				frappe.db.set_value(
					"Uptime Maintenance Window", {"site_name": original.site_name}, "site_name", site_name
				)
		except Exception as e:
			_log_sibling_failure("edit", sibling_name)
			errors.append(f"{sibling_name}: {e}")

	frappe.db.commit()
	return {"updated": updated, "errors": errors, "notes": notes}


@frappe.whitelist()
def delete_site(name: str) -> dict:
	"""Deletes every sibling of ``name`` (same site_name, across every
	instance), both on Kuma and locally — see the module docstring."""
	frappe.only_for(WRITE_ROLES)
	site_name = frappe.db.get_value("Uptime Site", name, "site_name")
	disabled = _disabled_instances()
	deleted = []
	errors = []
	notes = []
	for sibling_name in _sibling_names(site_name):
		frappe.db.savepoint("uptime_site_delete")
		try:
			sibling = frappe.get_doc("Uptime Site", sibling_name)
			# The site's check history goes with it. force skips the link
			# check so its Proxmox Alert Log rows stay as history.
			frappe.db.delete("Uptime Check Log", {"site": sibling_name})
			frappe.delete_doc("Uptime Site", sibling_name, ignore_permissions=True, force=True)
			# Kuma last: if it fails, the local delete is rolled back below,
			# so Kuma and Pulse never end up out of step.
			if sibling.instance in disabled:
				notes.append(_local_only_note(sibling.instance))
			else:
				client = _get_client(sibling.instance)
				try:
					client.delete_monitor(sibling.kuma_monitor_id)
				finally:
					client.disconnect()
			deleted.append(sibling_name)
		except Exception as e:
			frappe.db.rollback(save_point="uptime_site_delete")
			_log_sibling_failure("delete", sibling_name)
			errors.append(f"{sibling_name}: {e}")
	if not frappe.db.exists("Uptime Site", {"site_name": site_name}):
		frappe.db.delete("Uptime Maintenance Window", {"site_name": site_name})
	frappe.db.commit()
	return {"deleted": deleted, "errors": errors, "notes": notes}


@frappe.whitelist()
def pause_site(name: str) -> dict:
	"""Pauses every sibling of ``name`` (same site_name, across every
	instance) — see the module docstring."""
	frappe.only_for(WRITE_ROLES)
	site_name = frappe.db.get_value("Uptime Site", name, "site_name")
	disabled = _disabled_instances()
	paused = []
	errors = []
	notes = []
	for sibling_name in _sibling_names(site_name):
		try:
			sibling = frappe.get_doc("Uptime Site", sibling_name)
			if sibling.instance in disabled:
				notes.append(_local_only_note(sibling.instance))
			else:
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
	return {"paused": paused, "errors": errors, "notes": notes}


@frappe.whitelist()
def resume_site(name: str) -> dict:
	"""Resumes every sibling of ``name`` (same site_name, across every
	instance) — see the module docstring."""
	frappe.only_for(WRITE_ROLES)
	site_name = frappe.db.get_value("Uptime Site", name, "site_name")
	disabled = _disabled_instances()
	resumed = []
	errors = []
	notes = []
	for sibling_name in _sibling_names(site_name):
		try:
			sibling = frappe.get_doc("Uptime Site", sibling_name)
			if sibling.instance in disabled:
				notes.append(_local_only_note(sibling.instance))
			else:
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
	return {"resumed": resumed, "errors": errors, "notes": notes}


@frappe.whitelist()
def get_uptime_summary(site: str | None = None, days: int = 7) -> dict:
	frappe.only_for(READ_ROLES)
	days = cint(days) or 7
	# Checks in a scheduled downtime window don't count (see maintenance.py).
	conditions = "checked_at >= %(start)s and in_maintenance = 0"
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
		where site = %(site)s and checked_at >= %(start)s and in_maintenance = 0
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


def sync_check_interval_to_all_sites() -> dict:
	"""Pushes Uptime Monitor Settings' heartbeat_interval_seconds onto every
	Uptime Site's own check_interval_seconds — both locally and on Kuma
	itself, via edit_monitor. Deliberately a separate setting from
	poll_interval_seconds: this is how often *Kuma* actually performs its
	underlying check (what Kuma's own UI calls "Heartbeat Interval"), not
	how often *we* poll /metrics for a result — the two don't need to
	agree, and forcing them to made poll_interval_seconds do two
	unrelated jobs at once. Enqueued as a background job (see
	uptime_monitor_settings.py's on_update) since it makes live Socket.IO
	calls; also callable directly for the manual "Sync Check Interval Now"
	button. Only sites already out of sync are touched, so a no-op run
	costs nothing beyond one query.

	One connection is reused for every out-of-sync site *on the same
	instance*, not opened fresh per site — each connection needs its own
	login round-trip, and 14 of those back-to-back against one instance
	was enough to make some of them time out (see socketio_client.py's
	CALL_TIMEOUT), turning what should be one flaky call into several."""
	target = cint(frappe.get_cached_doc("Uptime Monitor Settings").heartbeat_interval_seconds) or 60
	out_of_sync = frappe.get_all(
		"Uptime Site", filters={"check_interval_seconds": ["!=", target]}, fields=["name", "instance", "site_name"]
	)

	# A disabled instance is never contacted (see the module docstring); its
	# sites stay out of sync and catch up once it's enabled again.
	disabled = _disabled_instances()
	by_instance: dict[str, list] = {}
	for row in out_of_sync:
		if row.instance not in disabled:
			by_instance.setdefault(row.instance, []).append(row)

	updated = []
	errors = []
	for instance, rows in by_instance.items():
		try:
			client = _get_client(instance)
		except Exception:
			frappe.log_error(
				title=f"Uptime Monitor: failed to connect to {instance} for check interval sync",
				message=frappe.get_traceback(),
			)
			errors.extend(row.name for row in rows)
			continue

		try:
			for row in rows:
				try:
					site = frappe.get_doc("Uptime Site", row.name)
					site.check_interval_seconds = target
					client.edit_monitor(site.kuma_monitor_id, site)
					frappe.db.set_value("Uptime Site", site.name, "check_interval_seconds", target)
					frappe.db.commit()
					updated.append(site.name)
				except Exception:
					frappe.log_error(
						title=f"Uptime Monitor: failed to sync check interval for '{row.site_name}' on {instance}",
						message=frappe.get_traceback(),
					)
					errors.append(row.name)
		finally:
			client.disconnect()

	return {"updated": updated, "errors": errors}


@frappe.whitelist()
def sync_check_interval_now() -> dict:
	"""Manual trigger (Uptime Monitor Settings' "Sync Check Interval Now"
	button) — enqueues the same job on_update fires automatically, so a
	slow multi-site/multi-instance run never blocks the Desk save."""
	frappe.only_for(WRITE_ROLES)
	frappe.enqueue(
		"alfaedge_pulse.uptime_monitor.api.sync_check_interval_to_all_sites",
		queue="long",
		job_id="alfaedge_pulse_sync_check_interval",
		deduplicate=True,
	)
	return {"ok": True, "message": frappe._("Sync started in the background — refresh in a moment to see results.")}


# ---------------------------------------------------------------------------
# Scheduled downtime windows — see uptime_monitor/maintenance.py
# ---------------------------------------------------------------------------


def _window_row(w, now) -> dict:
	from alfaedge_pulse.uptime_monitor import maintenance

	row = {k: w.get(k) for k in maintenance.WINDOW_FIELDS}
	for key in ("start_time", "end_time", "start_datetime", "end_datetime"):
		if row[key] is not None:
			row[key] = str(row[key])
	row["label"] = maintenance.describe(w)
	row["active"] = bool(cint(w.enabled)) and maintenance.active_window([w], now) is not None
	return row


@frappe.whitelist()
def get_maintenance_windows(site_name: str) -> list[dict]:
	frappe.only_for(READ_ROLES)
	from alfaedge_pulse.uptime_monitor import maintenance

	now = now_datetime()
	rows = frappe.get_all(
		"Uptime Maintenance Window", filters={"site_name": site_name}, fields=maintenance.WINDOW_FIELDS, order_by="creation asc"
	)
	return [_window_row(w, now) for w in rows]


@frappe.whitelist(methods=["POST"])
def save_maintenance_window(values) -> dict:
	"""Create or update one window. ``values`` is the doc's fields, with
	``name`` set to update an existing one."""
	frappe.only_for(WRITE_ROLES)
	from alfaedge_pulse.uptime_monitor import maintenance

	values = frappe.parse_json(values) or {}
	allowed = set(maintenance.WINDOW_FIELDS) - {"name"}
	data = {k: v for k, v in values.items() if k in allowed}
	if values.get("name"):
		doc = frappe.get_doc("Uptime Maintenance Window", values["name"])
		doc.update(data)
		doc.save()
	else:
		doc = frappe.get_doc({"doctype": "Uptime Maintenance Window", **data}).insert()
	frappe.db.commit()
	return _window_row(doc, now_datetime())


@frappe.whitelist(methods=["POST"])
def delete_maintenance_window(name: str) -> dict:
	frappe.only_for(WRITE_ROLES)
	frappe.delete_doc("Uptime Maintenance Window", name)
	frappe.db.commit()
	return {"deleted": name}


# ---------------------------------------------------------------------------
# Uptime tab overview — one row per site_name
# ---------------------------------------------------------------------------

#: Availability strip resolution — the range is split into this many cells.
STRIP_BUCKETS = 28


def _group_status(siblings: list, in_window: bool) -> str:
	if in_window:
		return "Maintenance"
	active = [s for s in siblings if cint(s.is_active)]
	if not active:
		return "Paused"
	if any(cint(s.is_critical) for s in siblings):
		return "Down"
	down = [s for s in active if s.current_status == "Down"]
	if not down:
		return "Up"
	return "Down" if len(down) == len(active) else "Partial"


@frappe.whitelist()
def get_site_overview(days: int = 7) -> dict:
	"""Everything the Uptime tab shows, in one call: per site (grouped by
	site_name across instances) its status, per-instance readings, uptime %
	and failed checks (scheduled downtime excluded), an availability strip,
	and its maintenance windows; plus each Kuma instance's health and the
	range's Site Down incidents."""
	frappe.only_for(READ_ROLES)
	from alfaedge_pulse.uptime_monitor import maintenance

	days = cint(days) or 7
	now = now_datetime()
	start = add_to_date(now, days=-days)
	bucket_seconds = max(1, int((now - start).total_seconds() // STRIP_BUCKETS))

	sites = frappe.get_all(
		"Uptime Site",
		fields=["name", "site_name", "instance", "monitor_type", "url", "hostname", "port",
			"is_active", "current_status", "is_critical", "last_checked", "check_interval_seconds"],
		order_by="site_name asc, instance asc",
	)
	stats = {
		r.site: r
		for r in frappe.db.sql(
			"""
			select site,
				sum(in_maintenance = 0) as checks,
				sum(in_maintenance = 0 and is_up = 1) as up_checks,
				avg(case when in_maintenance = 0 and is_up = 1 and response_time_ms >= 0 then response_time_ms end) as avg_ms
			from `tabUptime Check Log`
			where checked_at >= %(start)s
			group by site
			""",
			{"start": start},
			as_dict=True,
		)
	}
	cells: dict[str, dict[int, dict]] = {}
	for r in frappe.db.sql(
		"""
		select site, floor(timestampdiff(second, %(start)s, checked_at) / %(size)s) as b,
			sum(in_maintenance = 0 and is_up = 1) as up, sum(in_maintenance = 0 and is_up = 0) as down,
			sum(in_maintenance) as maint
		from `tabUptime Check Log`
		where checked_at >= %(start)s
		group by site, b
		""",
		{"start": start, "size": bucket_seconds},
		as_dict=True,
	):
		cells.setdefault(r.site, {})[min(cint(r.b), STRIP_BUCKETS - 1)] = r

	windows = maintenance.windows_by_site()
	instances = {
		i.name: i
		for i in frappe.get_all(
			"Uptime Kuma Instance", fields=["name", "instance_name", "enabled", "last_synced", "last_error"]
		)
	}
	latest_check: dict[str, object] = {}
	for s in sites:
		if s.last_checked and (s.instance not in latest_check or s.last_checked > latest_check[s.instance]):
			latest_check[s.instance] = s.last_checked
	poll_seconds = cint(frappe.get_cached_doc("Uptime Monitor Settings").poll_interval_seconds) or 60
	stale_after = max(poll_seconds * 5, 600)

	def stale(instance: str) -> bool:
		last = latest_check.get(instance)
		return not last or (now - frappe.utils.get_datetime(last)).total_seconds() > stale_after

	groups: dict[str, list] = {}
	for s in sites:
		groups.setdefault(s.site_name, []).append(s)

	out = []
	for site_name, siblings in groups.items():
		site_windows = windows.get(site_name, [])
		in_window = maintenance.active_window(site_windows, now) is not None
		first = siblings[0]
		checks = sum(cint(stats[s.name].checks) for s in siblings if s.name in stats)
		up_checks = sum(cint(stats[s.name].up_checks) for s in siblings if s.name in stats)

		strip = []
		window_spans = maintenance.intervals(site_windows, start, now)
		for b in range(STRIP_BUCKETS):
			ups = sum(cint(cells.get(s.name, {}).get(b, {}).get("up")) for s in siblings)
			downs = sum(cint(cells.get(s.name, {}).get(b, {}).get("down")) for s in siblings)
			maint = sum(cint(cells.get(s.name, {}).get(b, {}).get("maint")) for s in siblings)
			b_start = add_to_date(start, seconds=b * bucket_seconds)
			b_end = add_to_date(b_start, seconds=bucket_seconds)
			if ups or downs:
				state = "down" if downs > ups else "degraded" if downs else "up"
			elif maint or any(ws < b_end and we > b_start for ws, we in window_spans):
				state = "maintenance"
			else:
				state = "none"
			strip.append(state)

		nxt = maintenance.next_window(site_windows, now)
		out.append(
			{
				"site_name": site_name,
				"name": first.name,
				"monitor_type": first.monitor_type,
				"target": first.url or (f"{first.hostname}:{first.port}" if first.port else first.hostname),
				"status": _group_status(siblings, in_window),
				"is_active": any(cint(s.is_active) for s in siblings),
				"uptime_percent": flt(up_checks / checks * 100, 2) if checks else None,
				"checks": checks,
				"failed_checks": checks - up_checks,
				"avg_response_ms": flt(
					sum(flt(stats[s.name].avg_ms) * cint(stats[s.name].up_checks) for s in siblings if s.name in stats and stats[s.name].avg_ms is not None)
					/ up_checks,
					1,
				)
				if up_checks
				else None,
				"instances": [
					{
						"name": s.name,
						"instance": s.instance,
						"instance_name": instances[s.instance].instance_name if s.instance in instances else s.instance,
						"status": s.current_status,
						"is_active": cint(s.is_active),
						"last_checked": s.last_checked,
						"avg_response_ms": flt(stats[s.name].avg_ms, 1) if s.name in stats and stats[s.name].avg_ms is not None else None,
						"uptime_percent": flt(cint(stats[s.name].up_checks) / cint(stats[s.name].checks) * 100, 2)
						if s.name in stats and cint(stats[s.name].checks)
						else None,
						"stale": stale(s.instance),
					}
					for s in siblings
				],
				"strip": strip,
				"in_maintenance": in_window,
				"windows": [_window_row(w, now) for w in site_windows],
				"next_window": {"start": nxt[0], "end": nxt[1]} if nxt else None,
			}
		)

	site_by_doc = {s.name: s.site_name for s in sites}
	incidents = [
		dict(r, site_name=site_by_doc.get(r.reference_name, r.reference_name))
		for r in frappe.get_all(
			"Proxmox Alert Log",
			filters={"reference_doctype": "Uptime Site", "alert_type": "Site Down", "sent_at": [">=", start]},
			fields=["name", "reference_name", "message", "sent_at", "resolved", "channels_sent"],
			order_by="sent_at desc",
			limit_page_length=200,
		)
	]

	user = frappe.session.user
	return {
		"can_edit": user == "Administrator" or bool(set(frappe.get_roles(user)) & set(WRITE_ROLES)),
		"sites": out,
		"instances": [
			{
				"name": i.name,
				"instance_name": i.instance_name,
				"enabled": cint(i.enabled),
				"last_synced": i.last_synced,
				"last_error": i.last_error,
				"last_check": latest_check.get(i.name),
				"stale": stale(i.name),
				"site_count": sum(1 for s in sites if s.instance == i.name),
			}
			for i in instances.values()
		],
		"incidents": incidents,
		"range": {"start": start, "end": now, "bucket_seconds": bucket_seconds},
	}
