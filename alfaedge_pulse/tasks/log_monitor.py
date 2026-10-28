# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Recurring-error alerting over the ClickHouse error logs.

Every ``check_interval_minutes`` (self-throttled on the existing every-
minute cron, same reasoning as ``bifrost_sync.sync_bifrost_logs`` — keeps
the interval live-editable from Desk with no hooks.py change), count Error
rows per signature (see ``log_monitor/queries.py`` for what makes two
errors "the same") over the trailing ``window_minutes``:

- **count >= occurrence_threshold** → the signature's ``Recurring Error``
  is upserted and marked critical; on the False->True edge (or when an
  open incident is missing, see ``has_open_alert``) a ``Recurring Error``
  alert is recorded via ``dispatch_alert``. That alert type is in
  ``BATCHED_RESOURCE_ALERT_TYPES``, so delivery happens through
  ``alerts/resource_batch.py``'s 2-minute batch — several errors spiking
  together (e.g. one bad deploy) arrive as one message, not a burst.
- **A critical signature back under the threshold** for the window →
  resolved, with a recovery message through the same batch.
- Occurrences at or before a signature's manual resolution (Resolve on
  the Logs tab / task board, see ``log_monitor.api.resolve_error``) don't
  count, so a resolved error only alerts again once it repeats afterwards.

Never raises into the scheduler: failures land on ClickHouse Log
Settings' ``last_error``.
"""

from __future__ import annotations

import frappe
from frappe.utils import add_to_date, cint, get_datetime, now_datetime

from alfaedge_pulse.alerts.dispatch import dispatch_alert, dispatch_recovery, has_open_alert, resolve_alert
from alfaedge_pulse.log_monitor.connection import SETTINGS_DOCTYPE, get_client, get_settings, get_site_aliases, normalize_site
from alfaedge_pulse.log_monitor.queries import build_filtered, ts_out

ALERT_TYPE = "Recurring Error"
REFERENCE_DOCTYPE = "Recurring Error"
_LAST_RUN_CACHE_KEY = "alfaedge_pulse:log_monitor_last_check"
_MAX_GROUPS = 500


def check_recurring_errors() -> None:
	"""Cron entrypoint — self-throttled, never raises."""
	try:
		settings = get_settings()
		if not cint(settings.enabled) or not cint(settings.enable_recurring_alerts) or not settings.host:
			return
		last_run = frappe.cache().get_value(_LAST_RUN_CACHE_KEY)
		interval = max(cint(settings.check_interval_minutes), 1)
		now = now_datetime()
		if last_run and get_datetime(last_run) > add_to_date(now, minutes=-interval, seconds=10):
			return
		frappe.cache().set_value(_LAST_RUN_CACHE_KEY, str(now))
		run_check(settings, now)
		frappe.db.set_single_value(SETTINGS_DOCTYPE, {"last_checked": now, "last_error": None})
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title="alfaEdge Pulse: recurring error check failed", message=frappe.get_traceback())
		frappe.db.set_single_value(
			SETTINGS_DOCTYPE, {"last_checked": now_datetime(), "last_error": frappe.get_traceback()[-2000:]}
		)
	frappe.db.commit()


def run_check(settings=None, now=None) -> dict:
	"""One pass. Returns {"breaching": n, "alerted": n, "recovered": n} —
	handy from ``bench execute`` when tuning thresholds."""
	settings = settings or get_settings()
	now = now or now_datetime()
	threshold = max(cint(settings.occurrence_threshold), 1)
	window = max(cint(settings.window_minutes), 1)
	start = add_to_date(now, minutes=-window).strftime("%Y-%m-%d %H:%M:%S")
	end = add_to_date(now, seconds=1).strftime("%Y-%m-%d %H:%M:%S")

	from_sql, params = build_filtered(start, end, log_types=["Error"], exclude_resolved=True)
	params.update({"threshold": threshold, "max_groups": _MAX_GROUPS})
	rows = get_client().query(
		f"""SELECT lm_sig AS signature, any(lm_site) AS site_url, any(lm_title) AS title, count() AS occurrences,
			{ts_out('min(lm_ts)')} AS first_seen, {ts_out('max(lm_ts)')} AS last_seen, argMax(lm_id, lm_ts) AS latest_log_id
			{from_sql} GROUP BY lm_sig HAVING occurrences >= {{threshold:UInt32}}
			ORDER BY occurrences DESC LIMIT {{max_groups:UInt32}}""",
		params,
	)

	host_by_site = _monitored_hosts_by_site()
	breaching = {r["signature"]: r for r in rows}
	alerted = recovered = 0

	for sig, row in breaching.items():
		count = cint(row["occurrences"])
		name = frappe.db.get_value(REFERENCE_DOCTYPE, {"signature": sig})
		doc = frappe.get_doc(REFERENCE_DOCTYPE, name) if name else frappe.new_doc(REFERENCE_DOCTYPE)
		was_critical = bool(doc.is_critical)
		doc.update({
			"signature": sig,
			"title": row["title"] or "(no message)",
			"site_url": row["site_url"],
			"monitored_host": host_by_site.get(normalize_site(row["site_url"])),
			"window_count": count,
			"peak_window_count": max(cint(doc.peak_window_count), count),
			"last_seen": row["last_seen"],
			"latest_log_id": row["latest_log_id"],
			"is_critical": 1,
		})
		if not doc.first_seen:
			doc.first_seen = row["first_seen"]
		needs_alert = not was_critical or (doc.name and not has_open_alert(REFERENCE_DOCTYPE, doc.name, ALERT_TYPE))
		if needs_alert:
			doc.alert_count = cint(doc.alert_count) + 1
			doc.last_alerted = now
		doc.save(ignore_permissions=True)
		if needs_alert:
			dispatch_alert(
				ALERT_TYPE, REFERENCE_DOCTYPE, doc.name,
				f"{doc.title} — {count}x in the last {window} min on {doc.site_url} (last at {row['last_seen']})",
			)
			alerted += 1

	for name, sig, title, site_url in frappe.get_all(
		REFERENCE_DOCTYPE, filters={"is_critical": 1, "ignored": 0}, fields=["name", "signature", "title", "site_url"], as_list=True
	):
		if sig in breaching:
			continue
		frappe.db.set_value(
			REFERENCE_DOCTYPE, name, {"is_critical": 0, "window_count": 0, "last_recovered": now}
		)
		if has_open_alert(REFERENCE_DOCTYPE, name, ALERT_TYPE):
			resolve_alert(REFERENCE_DOCTYPE, name, ALERT_TYPE)
			dispatch_recovery(
				ALERT_TYPE, REFERENCE_DOCTYPE, name,
				f"Back to normal: {title} on {site_url} — under {threshold}x in the last {window} min",
			)
		recovered += 1

	return {"breaching": len(breaching), "alerted": alerted, "recovered": recovered}


def _monitored_hosts_by_site() -> dict[str, str]:
	"""Map each Monitored Host's reported sites (URL and site name) to the
	host, so a Recurring Error links back to it."""
	mapping = {}
	for row in frappe.get_all("Monitored Host Site", fields=["parent", "site_url", "site_name"]):
		for value in (row.site_url, row.site_name):
			if value:
				mapping.setdefault(normalize_site(value), row.parent)
	# An aliased site (e.g. "lulu" shown as "lulu.alfaedge.org") still
	# belongs to whichever host reports either name.
	for source, target in get_site_aliases().items():
		host = mapping.get(source) or mapping.get(normalize_site(target))
		if host:
			mapping.setdefault(normalize_site(target), host)
	return mapping
