# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Background polling of every enabled Uptime Kuma Instance's /metrics
endpoint, and the >50%-of-last-N-checks alerting rule built on top of it.

Design notes:

- **A plain self-throttled function on the existing every-minute cron**,
  same pattern as ``tasks.bifrost_sync`` — see that module's docstring for
  why this isn't the bounded-loop/heartbeat pattern ``poller.py`` uses.
  At the default 1-minute interval this throttle is a no-op (the cron
  tick and the configured interval coincide), but the field stays
  Desk-editable without a code/hooks.py change if ever slowed down.
- **We build our own heartbeat history** (``Uptime Check Log``) from
  Kuma's official, stable ``/metrics`` endpoint rather than relying on
  Kuma's own retention or its unofficial Socket.IO heartbeat API — see
  ``uptime_kuma_client/socketio_client.py``'s module docstring for the
  full reasoning (that client is reserved for admin-initiated monitor
  management only).
- **Pending/Maintenance readings are excluded from the down-ratio
  calculation entirely** — no ``Uptime Check Log`` row is even written
  for them, so "more than half of the last N checks" only ever considers
  genuine Up/Down results, never "was paused/in a maintenance window."
- **The alert edge-trigger reuses ``poller.py``'s ``_handle_transition``
  directly** rather than re-implementing the same was_critical/is_critical
  dispatch-once-per-incident logic a second time — it's already
  generic (doctype/name/alert_type as plain arguments), so this is a
  straightforward cross-module reuse, not a duplication.
"""

from __future__ import annotations

import frappe
from frappe.utils import cint, get_datetime, now_datetime
from frappe.utils.password import get_decrypted_password

from proxmox_monitor.tasks.poller import _handle_transition
from proxmox_monitor.uptime_kuma_client.base import UptimeKumaAPIError
from proxmox_monitor.uptime_kuma_client.metrics_client import fetch_metrics

DEFAULT_POLL_INTERVAL_MINUTES = 1
DEFAULT_ALERT_WINDOW_CHECKS = 3

# Confirmed against Uptime Kuma's own source (server/prometheus.js):
# "Monitor Status (1 = UP, 0 = DOWN, 2 = PENDING, 3 = MAINTENANCE)".
STATUS_UP = 1
STATUS_DOWN = 0
_STATUS_LABEL = {STATUS_UP: "Up", STATUS_DOWN: "Down", 2: "Pending", 3: "Maintenance"}


def poll_all_instances() -> None:
	"""Cron entrypoint. Never lets one instance's failure block another's
	(mirrors ``poller.py``'s per-server isolation in ``sync_all_servers``)."""
	settings = frappe.get_cached_doc("Uptime Monitor Settings")
	interval_minutes = cint(settings.poll_interval_minutes) or DEFAULT_POLL_INTERVAL_MINUTES
	window = cint(settings.alert_window_checks) or DEFAULT_ALERT_WINDOW_CHECKS

	for instance in frappe.get_all("Uptime Kuma Instance", filters={"enabled": 1}, fields=["name"]):
		try:
			_poll_instance(instance.name, interval_minutes, window)
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"Uptime Monitor: poll failed for {instance.name}", message=frappe.get_traceback())


def _poll_instance(instance_name: str, interval_minutes: int, window: int) -> None:
	doc = frappe.get_doc("Uptime Kuma Instance", instance_name)
	if doc.last_synced:
		elapsed_minutes = (now_datetime() - get_datetime(doc.last_synced)).total_seconds() / 60
		if elapsed_minutes < interval_minutes:
			return

	password = get_decrypted_password("Uptime Kuma Instance", instance_name, "password", raise_exception=False)
	api_key = get_decrypted_password("Uptime Kuma Instance", instance_name, "api_key", raise_exception=False)

	try:
		metrics = fetch_metrics(doc.base_url, doc.username, password, api_key=api_key or None, verify_ssl=bool(doc.verify_ssl))
	except UptimeKumaAPIError as e:
		frappe.db.set_value("Uptime Kuma Instance", instance_name, {"last_synced": now_datetime(), "last_error": str(e)})
		frappe.db.commit()
		return

	frappe.db.set_value("Uptime Kuma Instance", instance_name, {"last_synced": now_datetime(), "last_error": ""})

	sites = frappe.get_all("Uptime Site", filters={"instance": instance_name}, fields=["name", "site_name", "is_critical"])
	for site in sites:
		try:
			_poll_site(site, metrics, window)
		except Exception:
			frappe.log_error(title=f"Uptime Monitor: failed to process site {site.site_name}", message=frappe.get_traceback())
	frappe.db.commit()


def _poll_site(site, metrics: dict, window: int) -> None:
	reading = metrics.get(site.site_name)
	if reading is None or reading.get("status") is None:
		# No current /metrics reading for this site name on this instance —
		# e.g. deleted directly in Kuma out of band, or a brand-new monitor
		# that hasn't completed its first check yet. Nothing to record.
		return

	status = int(reading["status"])
	now = now_datetime()
	current_status = _STATUS_LABEL.get(status, "Pending")
	was_critical = bool(cint(site.is_critical))
	is_critical = was_critical

	if status in (STATUS_UP, STATUS_DOWN):
		frappe.get_doc(
			{
				"doctype": "Uptime Check Log",
				"site": site.name,
				"checked_at": now,
				"is_up": 1 if status == STATUS_UP else 0,
				"raw_status": str(status),
				"response_time_ms": reading.get("response_time_ms"),
			}
		).insert(ignore_permissions=True)
		is_critical = _is_site_critical(site.name, window)
	# Pending/Maintenance: no log row, no alerting decision this cycle —
	# is_critical stays exactly as it was, so _handle_transition below is a
	# guaranteed no-op for this reading.

	frappe.db.set_value(
		"Uptime Site",
		site.name,
		{"current_status": current_status, "last_checked": now, "is_critical": 1 if is_critical else 0},
	)

	_handle_transition(
		was_critical,
		is_critical,
		"Uptime Site",
		site.name,
		"Site Down",
		f"{site.site_name} is down (more than half of the last {window} checks failed).",
		recovery_message=f"{site.site_name} is back up.",
	)


def _is_site_critical(site_name: str, window: int) -> bool:
	"""More than 50% of the last ``window`` counted (Up/Down only) checks
	report Down. A site with fewer than a full window of samples on record
	is never flagged — insufficient data should never false-trigger."""
	rows = frappe.get_all(
		"Uptime Check Log",
		filters={"site": site_name},
		fields=["is_up"],
		order_by="checked_at desc",
		limit_page_length=window,
	)
	if len(rows) < window:
		return False
	down_count = sum(1 for r in rows if not r.is_up)
	return (down_count / len(rows)) > 0.5
