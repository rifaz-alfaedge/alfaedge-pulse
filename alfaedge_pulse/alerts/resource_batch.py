# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Short-window batching for Host Health's resource-threshold alert types
(High Load Average, High Swap Usage — see alerts/dispatch.py's
BATCHED_RESOURCE_ALERT_TYPES), so several hosts crossing their threshold
around the same time (e.g. several guests on one loaded Proxmox host) land
as one combined WhatsApp/email message instead of one per host.

Unlike alerts/digest.py's three fixed daily summaries (built for
informational alert types that can comfortably wait hours), this runs
every 2 minutes (see hooks.py's "*/2 * * * *" cron entry) — still
near-real-time, just deduped against exactly the clustering pattern that
motivated this module.

Reuses Proxmox Alert Log as the source of truth the same way
alerts/digest.py does, but windows on "since this job's own last run"
(tracked via a Redis cache key) rather than fixed hours-of-day, since this
cron fires continuously rather than at fixed points in the day.
"""

from __future__ import annotations

import frappe
from frappe.utils import add_to_date, now_datetime

from alfaedge_pulse.alerts.dispatch import (
	BATCHED_RESOURCE_ALERT_TYPES,
	_get_matching_subscriptions,
	_resolve_whatsapp_template,
	_send_whatsapp,
	has_open_alert,
)
from alfaedge_pulse.host_health.doctype.monitored_host.monitored_host import get_host_role

_LAST_RUN_CACHE_KEY = "alfaedge_pulse:resource_alert_batch_last_run"
#: How far back to look on the very first run, or after a gap (e.g. the
#: scheduler was down) — caps how much old backlog gets swept into one
#: message rather than growing unbounded.
_MAX_LOOKBACK_MINUTES = 10


def _parse_recipient_list(value: str | None) -> list[str]:
	return [v.strip() for v in (value or "").split(",") if v.strip()]


def send_resource_alert_batch() -> None:
	window_end = now_datetime()
	last_run = frappe.cache().get_value(_LAST_RUN_CACHE_KEY)
	window_start = (
		frappe.utils.get_datetime(last_run) if last_run else add_to_date(window_end, minutes=-_MAX_LOOKBACK_MINUTES)
	)
	# Advance the marker up front — a failed send below must never cause the
	# same window to be reprocessed (and re-sent) on the next tick.
	frappe.cache().set_value(_LAST_RUN_CACHE_KEY, str(window_end))

	rows = frappe.get_all(
		"Proxmox Alert Log",
		filters={
			"alert_type": ["in", list(BATCHED_RESOURCE_ALERT_TYPES)],
			"sent_at": ["between", [window_start, window_end]],
		},
		fields=["name", "alert_type", "reference_doctype", "reference_name", "message", "sent_at"],
		order_by="sent_at asc",
	)
	if not rows:
		return

	groups: dict[tuple, list[dict]] = {}
	for row in rows:
		key = (row.alert_type, row.reference_doctype, row.reference_name)
		groups.setdefault(key, []).append(row)

	# Mirrors ingest.py's own notify_global = role == "Production" gate at
	# the point each alert was raised — recomputed here (not persisted on
	# Proxmox Alert Log) since these two alert types always reference a
	# Monitored Host, whose role is a cheap lookup either way.
	section_by_key: dict[tuple, str] = {}
	global_eligible: set[tuple] = set()
	for key, occurrences in groups.items():
		alert_type, reference_doctype, reference_name = key
		# still_open reflects the *current* state, independent of which
		# message (alert or recovery) happens to be newest in this window —
		# a window that saw both an alert and its recovery must not read as
		# "currently critical" just because occurrences[-1] is chronologically
		# last; the explicit status label below is what actually disambiguates.
		still_open = has_open_alert(reference_doctype, reference_name, alert_type)
		status_label = "still open" if still_open else "resolved"
		count_suffix = f" ({len(occurrences)}x, {status_label})" if len(occurrences) > 1 else f" ({status_label})"
		section_by_key[key] = f"{alert_type} — {reference_name}{count_suffix}: {occurrences[-1].message}"
		if reference_doctype == "Monitored Host" and get_host_role(reference_name) == "Production":
			global_eligible.add(key)

	settings = frappe.get_cached_doc("Proxmox Monitor Settings")
	global_emails = _parse_recipient_list(settings.email_recipients) if settings.enable_email_alerts else []
	global_email_keys = {e.lower() for e in global_emails}
	global_numbers = _parse_recipient_list(settings.whatsapp_recipients) if settings.enable_whatsapp_alerts else []
	global_number_keys = set(global_numbers)

	# Only tracked for subscribers *not* already covered by the global list
	# above — no point sending an already-global recipient a second, more
	# narrowly-scoped copy of the same window. Subscriptions receive their
	# own host's alerts regardless of that host's role (matches dispatch_alert's
	# own notify_global semantics: it only ever gates the *global* list).
	subscriber_email_keys: dict[str, set[tuple]] = {}
	subscriber_number_keys: dict[str, set[tuple]] = {}
	for key in groups:
		_, reference_doctype, reference_name = key
		for s in _get_matching_subscriptions(reference_doctype, reference_name):
			if s.enable_email and s.email and s.email.lower() not in global_email_keys:
				subscriber_email_keys.setdefault(s.email, set()).add(key)
			if s.enable_whatsapp and s.whatsapp_number and s.whatsapp_number not in global_number_keys:
				subscriber_number_keys.setdefault(s.whatsapp_number, set()).add(key)

	def _ordered_sections(keys) -> str:
		return "\n".join(section_by_key[k] for k in groups if k in keys)  # preserve chronological first-seen order

	# Per-key (not batch-wide) delivery bookkeeping — a batch commonly mixes
	# a globally-eligible host with a subscriber-only one, and each can go
	# out through a different channel combination, so a single shared
	# "channels sent" string for every row in the batch would misreport
	# delivery for whichever rows weren't actually part of every send.
	channels_by_key: dict[tuple, set[str]] = {}

	def _mark(keys, channel: str) -> None:
		for k in keys:
			channels_by_key.setdefault(k, set()).add(channel)

	def _send_email(recipients: list[str], keys: set) -> None:
		body = f"{len(keys)} resource alert(s) in the last few minutes:\n\n{_ordered_sections(keys)}"
		try:
			frappe.sendmail(
				recipients=recipients, subject="alfaEdge Pulse — Resource Alert Summary", message=body, now=True
			)
			_mark(keys, "Email")
		except Exception:
			frappe.log_error(title="alfaEdge Pulse: resource alert batch email failed", message=frappe.get_traceback())

	if global_emails and global_eligible:
		_send_email(global_emails, global_eligible)
	for email, keys in subscriber_email_keys.items():
		_send_email([email], keys)

	template_name = _resolve_whatsapp_template(settings.whatsapp_template) if settings.whatsapp_template else None
	if template_name:
		if global_numbers and global_eligible:
			if _send_whatsapp(global_numbers, template_name, _ordered_sections(global_eligible)):
				_mark(global_eligible, "WhatsApp")
		for number, keys in subscriber_number_keys.items():
			if _send_whatsapp([number], template_name, _ordered_sections(keys)):
				_mark(keys, "WhatsApp")

	for row in rows:
		key = (row.alert_type, row.reference_doctype, row.reference_name)
		channels = channels_by_key.get(key)
		if channels:
			frappe.db.set_value("Proxmox Alert Log", row.name, "channels_sent", ", ".join(sorted(channels)))
	frappe.db.commit()
