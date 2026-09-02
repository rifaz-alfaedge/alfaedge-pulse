# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Three scheduled digests for Host Health's own alert types (see
``alerts/dispatch.py``'s ``DIGEST_ONLY_ALERT_TYPES``) — Service Down,
Worker Degraded, Failed Job Threshold, Long Running Job, Scheduler
Stalled, Host Unreachable. Every other alert type (Uptime, resource
usage, backups, server-offline) is untouched by this module and keeps
sending immediately through ``dispatch.py`` as before.

Wired to three fixed times in hooks.py's ``scheduler_events`` cron
(07:00 / 13:00 / 20:00) — the hour boundaries below must match those
entries, since each digest's window is derived from fixed hours-of-day,
not "time since this function last ran":

- 07:00 — the previous full calendar day (00:00-24:00 yesterday), a
  complete recap for whoever doesn't watch the midday/evening ones.
- 13:00 — today, 00:00 to 13:00.
- 20:00 — today, 13:00 to 20:00. The small 20:00-24:00 gap isn't lost —
  it simply rolls into the next morning's full-day recap.

Reuses ``Proxmox Alert Log`` (written unconditionally by
``dispatch_alert``/``dispatch_recovery`` regardless of whether a channel
actually sent) as the source of truth — no separate digest-tracking table
or "already included" flag needed, since each digest's window is a fixed,
non-overlapping slice of time queried directly.
"""

from __future__ import annotations

import frappe
from frappe.utils import add_to_date, now_datetime

from alfaedge_pulse.alerts.dispatch import (
	DIGEST_ONLY_ALERT_TYPES,
	_get_matching_subscriptions,
	_resolve_whatsapp_template,
	_send_whatsapp,
	has_open_alert,
)

#: Midday / evening cutoff hours — must match the "0 13 * * *" / "0 20 * * *"
#: cron entries in hooks.py.
MIDDAY_HOUR = 13
EVENING_HOUR = 20


def _day_start(dt=None):
	dt = dt or now_datetime()
	return frappe.utils.get_datetime(f"{dt.date()} 00:00:00")


def send_health_digest_morning() -> None:
	today_start = _day_start()
	_send_health_digest(add_to_date(today_start, days=-1), today_start, "Previous Day Summary")


def send_health_digest_midday() -> None:
	today_start = _day_start()
	_send_health_digest(today_start, add_to_date(today_start, hours=MIDDAY_HOUR), "Midday Summary")


def send_health_digest_evening() -> None:
	today_start = _day_start()
	_send_health_digest(
		add_to_date(today_start, hours=MIDDAY_HOUR), add_to_date(today_start, hours=EVENING_HOUR), "Evening Summary"
	)


def _resolve_host_label(reference_doctype: str, reference_name: str) -> str:
	"""Service Down references a Service Status Log row (a hash docname,
	not human-readable on its own); every other digest-only alert type
	references Monitored Host directly, whose own docname
	(``format:{hostname}_{proxmox_guest}``) is already readable — see
	monitored_host.json's autoname."""
	if reference_doctype == "Service Status Log":
		row = frappe.db.get_value(
			"Service Status Log", reference_name, ["monitored_host", "service_name"], as_dict=True
		)
		if not row:
			return reference_name
		return f"{row.monitored_host} ({row.service_name})"
	return reference_name


def _parse_recipient_list(value: str | None) -> list[str]:
	return [v.strip() for v in (value or "").split(",") if v.strip()]


def _send_health_digest(window_start, window_end, label: str) -> None:
	"""Groups this window's digest-only Proxmox Alert Log rows by
	(alert_type, host), then sends two shapes of message: the *global*
	recipient list (Proxmox Monitor Settings) gets everything, same as it
	always would for a fleet-wide watcher — but a per-guest Alert
	Subscription only ever gets the groups for the host(s) they actually
	subscribed to, each as its own separate send. A single combined digest
	naively sent to everyone (subscribers included) would leak every other
	host's health details to a subscriber who only ever asked to watch one
	— something a per-incident dispatch_alert would never have done.
	"""
	rows = frappe.get_all(
		"Proxmox Alert Log",
		filters={"alert_type": ["in", list(DIGEST_ONLY_ALERT_TYPES)], "sent_at": ["between", [window_start, window_end]]},
		fields=["alert_type", "reference_doctype", "reference_name", "message", "sent_at"],
		order_by="sent_at asc",
	)
	if not rows:
		return  # nothing happened in this window — no empty digest sent

	groups: dict[tuple, list[dict]] = {}
	for row in rows:
		key = (row.alert_type, row.reference_doctype, row.reference_name)
		groups.setdefault(key, []).append(row)

	settings = frappe.get_cached_doc("Proxmox Monitor Settings")
	global_emails = _parse_recipient_list(settings.email_recipients) if settings.enable_email_alerts else []
	global_email_keys = {e.lower() for e in global_emails}
	global_numbers = _parse_recipient_list(settings.whatsapp_recipients) if settings.enable_whatsapp_alerts else []
	global_number_keys = set(global_numbers)

	section_by_key: dict[tuple, str] = {}
	# Only tracked for subscribers *not* already covered by the global list
	# above — no point sending an already-global recipient a second, more
	# narrowly-scoped copy of the same window.
	subscriber_email_keys: dict[str, set[tuple]] = {}
	subscriber_number_keys: dict[str, set[tuple]] = {}

	for key, occurrences in groups.items():
		alert_type, reference_doctype, reference_name = key
		host_label = _resolve_host_label(reference_doctype, reference_name)
		still_open = has_open_alert(reference_doctype, reference_name, alert_type)
		section_by_key[key] = (
			f"{alert_type} — {host_label}: {len(occurrences)} occurrence{'s' if len(occurrences) != 1 else ''}"
			f" ({'still open' if still_open else 'resolved'})\n"
			f"   latest: {occurrences[-1].message}"
		)

		for s in _get_matching_subscriptions(reference_doctype, reference_name):
			if s.enable_email and s.email and s.email.lower() not in global_email_keys:
				subscriber_email_keys.setdefault(s.email, set()).add(key)
			if s.enable_whatsapp and s.whatsapp_number and s.whatsapp_number not in global_number_keys:
				subscriber_number_keys.setdefault(s.whatsapp_number, set()).add(key)

	def _issue_count(keys) -> int:
		return sum(len(groups[k]) for k in keys)

	def _host_count(keys) -> int:
		return len({(k[1], k[2]) for k in keys})

	def _send_email(recipients: list[str], keys: set) -> None:
		ordered = [k for k in groups if k in keys]  # preserve chronological first-seen order
		body = (
			f"{label}\n{_issue_count(keys)} health issue(s) across {_host_count(keys)} host(s):\n\n"
			+ "\n\n".join(section_by_key[k] for k in ordered)
		)
		try:
			frappe.sendmail(
				recipients=recipients, subject=f"alfaEdge Pulse — Host Health {label}", message=body, now=True
			)
		except Exception:
			frappe.log_error(title="alfaEdge Pulse: health digest email failed", message=frappe.get_traceback())

	all_keys = set(groups.keys())
	if global_emails:
		_send_email(global_emails, all_keys)
	for email, keys in subscriber_email_keys.items():
		_send_email([email], keys)

	template_name = (
		_resolve_whatsapp_template(settings.whatsapp_health_digest_template)
		if settings.whatsapp_health_digest_template
		else None
	)
	if template_name:
		if global_numbers:
			summary = f"{label}: {_issue_count(all_keys)} health issue(s) across {_host_count(all_keys)} host(s)."
			_send_whatsapp(global_numbers, template_name, summary)
		for number, keys in subscriber_number_keys.items():
			summary = f"{label}: {_issue_count(keys)} health issue(s) across {_host_count(keys)} host(s)."
			_send_whatsapp([number], template_name, summary)
