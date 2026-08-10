# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Whitelisted endpoints for the Uptime dashboard tab.

The write endpoints (add/edit/delete/pause/resume) are Manager-only —
same tier as the credential-bearing ``Uptime Kuma Instance`` doctype —
since each one makes a live Socket.IO call against the target Kuma
instance, not just a local database write. Read endpoints follow the
same three-role pattern as ``Uptime Site``/``Uptime Check Log`` itself.
"""

from __future__ import annotations

import frappe
from frappe.utils import add_to_date, cint, flt, now_datetime
from frappe.utils.password import get_decrypted_password

from proxmox_monitor.uptime_kuma_client.base import UptimeKumaAPIError
from proxmox_monitor.uptime_kuma_client.socketio_client import UptimeKumaClient

WRITE_ROLES = ("System Manager", "Proxmox Monitor Manager")
READ_ROLES = ("System Manager", "Proxmox Monitor Manager", "Proxmox Monitor Viewer")


def _get_client(instance_name: str) -> UptimeKumaClient:
	instance = frappe.get_doc("Uptime Kuma Instance", instance_name)
	password = get_decrypted_password("Uptime Kuma Instance", instance_name, "password", raise_exception=False)
	return UptimeKumaClient(instance.base_url, instance.username, password, verify_ssl=bool(instance.verify_ssl))


@frappe.whitelist()
def add_site(
	instance: str,
	site_name: str,
	monitor_type: str,
	url: str | None = None,
	hostname: str | None = None,
	port: int | None = None,
	check_interval_seconds: int = 60,
	is_active: int = 1,
) -> dict:
	"""Create the Kuma monitor first, then the local doc — if the local
	doc then fails to save, best-effort delete the just-created Kuma
	monitor rather than leave an orphan Kuma has but we don't know about."""
	frappe.only_for(WRITE_ROLES)

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
	site.validate()  # surface bad input (missing url/hostname for the type, etc.) before touching Kuma at all

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

	frappe.db.commit()
	return site.as_dict()


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
	frappe.only_for(WRITE_ROLES)
	site = frappe.get_doc("Uptime Site", name)
	for field, value in {
		"site_name": site_name,
		"url": url,
		"hostname": hostname,
		"port": cint(port) if port is not None else None,
		"check_interval_seconds": cint(check_interval_seconds) if check_interval_seconds is not None else None,
		"is_active": cint(is_active) if is_active is not None else None,
	}.items():
		if value is not None:
			site.set(field, value)
	site.validate()

	client = _get_client(site.instance)
	try:
		client.edit_monitor(site.kuma_monitor_id, site)
	finally:
		client.disconnect()

	site.save(ignore_permissions=True)
	frappe.db.commit()
	return site.as_dict()


@frappe.whitelist()
def delete_site(name: str) -> None:
	frappe.only_for(WRITE_ROLES)
	site = frappe.get_doc("Uptime Site", name)
	client = _get_client(site.instance)
	try:
		client.delete_monitor(site.kuma_monitor_id)
	finally:
		client.disconnect()
	frappe.delete_doc("Uptime Site", name, ignore_permissions=True)
	frappe.db.commit()


@frappe.whitelist()
def pause_site(name: str) -> None:
	frappe.only_for(WRITE_ROLES)
	site = frappe.get_doc("Uptime Site", name)
	client = _get_client(site.instance)
	try:
		client.pause_monitor(site.kuma_monitor_id)
	finally:
		client.disconnect()
	frappe.db.set_value("Uptime Site", name, "is_active", 0)
	frappe.db.commit()


@frappe.whitelist()
def resume_site(name: str) -> None:
	frappe.only_for(WRITE_ROLES)
	site = frappe.get_doc("Uptime Site", name)
	client = _get_client(site.instance)
	try:
		client.resume_monitor(site.kuma_monitor_id)
	finally:
		client.disconnect()
	frappe.db.set_value("Uptime Site", name, "is_active", 1)
	frappe.db.commit()


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
