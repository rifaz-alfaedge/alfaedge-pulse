# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""OpenRouter usage and credit sync.

Two independent jobs, both started by one every-minute cron entry
(``sync_openrouter``) that decides for itself whether anything is due. This is
the same pattern as ``bifrost_sync``: Frappe's cron cannot be edited from Desk
and runs in the server's timezone, so the schedule lives in code and settings.

- **Daily usage.** ``GET /activity`` returns daily rows per model/provider endpoint
  for the last 30 *completed* UTC days. It runs once per UTC day, shortly after
  00:35 UTC (OpenRouter finalises a day a little after midnight), and again every
  15 minutes until it succeeds. Every run fetches the whole 30-day window and
  upserts each row by (date, endpoint), so a rerun is harmless, late corrections
  are picked up, and the very first run is the 30-day backfill.
- **Balance.** ``GET /credits`` every ``balance_interval_minutes`` (30), stored as
  a time series of (taken at, purchased, used, remaining), with an alert when the
  remaining credit falls below the configured threshold.

Timestamps follow the rest of Pulse: ``taken_at``, ``last_*`` are naive datetimes in
the site timezone (IST here). Daily usage rows keep OpenRouter's own UTC date.
The management key is read with ``get_decrypted_password`` and is never logged.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, time, timedelta, timezone

import frappe
from frappe.utils import cint, flt, get_datetime, get_system_timezone, getdate, now_datetime
from frappe.utils.password import get_decrypted_password

from alfaedge_pulse.alerts.dispatch import dispatch_alert, dispatch_recovery, has_open_alert, resolve_alert
from alfaedge_pulse.openrouter_client.base import OpenRouterAPIError, OpenRouterClient

SETTINGS = "OpenRouter Settings"
ACTIVITY_JOB_ID = "alfaedge_pulse_openrouter_sync"
ACTIVITY_JOB_TIMEOUT = 1800
BALANCE_JOB_ID = "alfaedge_pulse_openrouter_balance"
BALANCE_JOB_TIMEOUT = 300
#: OpenRouter finalises a UTC day a little after midnight; start looking after this.
ACTIVITY_RUN_AFTER_UTC = time(0, 35)
ACTIVITY_RETRY_MINUTES = 15
DEFAULT_BALANCE_INTERVAL_MINUTES = 30
LOW_BALANCE_ALERT = "Low Credit Balance"
_SAFE_KEY = re.compile(r"[^A-Za-z0-9._-]+")


# ---------------------------------------------------------------------------
# Time helpers (functions, so tests can drive the clock)
# ---------------------------------------------------------------------------


def utcnow() -> datetime:
	return datetime.now(timezone.utc)


def site_naive_to_utc(value) -> datetime | None:
	"""A naive site-timezone datetime (how Pulse stores times) as aware UTC."""
	if not value:
		return None
	from zoneinfo import ZoneInfo

	return get_datetime(value).replace(tzinfo=ZoneInfo(get_system_timezone())).astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------


def activity_due(last_success_utc: datetime | None, last_attempt_utc: datetime | None, now_utc: datetime) -> bool:
	"""Is the daily usage sync due? Never synced: yes (first run backfills).
	Otherwise once per UTC day, after 00:35 UTC, whenever the scheduler gets to it
	(so downtime catches up), and never more often than every 15 minutes."""
	if last_attempt_utc and now_utc - last_attempt_utc < timedelta(minutes=ACTIVITY_RETRY_MINUTES):
		return False
	if last_success_utc is None:
		return True
	return now_utc.date() > last_success_utc.date() and now_utc.timetz().replace(tzinfo=None) >= ACTIVITY_RUN_AFTER_UTC


def balance_due(last_checked_utc: datetime | None, now_utc: datetime, interval_minutes: int) -> bool:
	if last_checked_utc is None:
		return True
	return now_utc - last_checked_utc >= timedelta(minutes=max(interval_minutes, 1))


def _has_key() -> bool:
	return bool(get_decrypted_password(SETTINGS, SETTINGS, "management_key", raise_exception=False))


def sync_openrouter() -> None:
	"""Cron entrypoint (every minute). Self-throttled; never raises."""
	try:
		settings = frappe.get_cached_doc(SETTINGS)
		if not settings.enabled or not _has_key():
			return
		now = utcnow()
		if activity_due(site_naive_to_utc(settings.last_activity_synced), site_naive_to_utc(settings.last_activity_attempt), now):
			enqueue_activity_sync()
		interval = cint(settings.balance_interval_minutes) or DEFAULT_BALANCE_INTERVAL_MINUTES
		if balance_due(site_naive_to_utc(settings.last_balance_at), now, interval):
			enqueue_balance_check()
	except Exception:
		frappe.log_error(title="OpenRouter Monitor: scheduling failed unexpectedly", message=frappe.get_traceback())


def enqueue_activity_sync() -> None:
	"""Queue the daily-usage sync (deduplicated by job id, so the cron and the
	Sync Usage Now button can never run it twice at once)."""
	frappe.enqueue(
		"alfaedge_pulse.tasks.openrouter_sync.run_activity_sync",
		queue="long",
		timeout=ACTIVITY_JOB_TIMEOUT,
		job_id=ACTIVITY_JOB_ID,
		deduplicate=True,
	)


def enqueue_balance_check() -> None:
	frappe.enqueue(
		"alfaedge_pulse.tasks.openrouter_sync.run_balance_check",
		queue="short",
		timeout=BALANCE_JOB_TIMEOUT,
		job_id=BALANCE_JOB_ID,
		deduplicate=True,
	)


def _client(settings=None) -> OpenRouterClient:
	settings = settings or frappe.get_cached_doc(SETTINGS)
	key = get_decrypted_password(SETTINGS, SETTINGS, "management_key", raise_exception=False)
	if not key:
		raise OpenRouterAPIError("No Management API key is saved in OpenRouter Settings.")
	return OpenRouterClient(settings.base_url, key)


# ---------------------------------------------------------------------------
# Daily usage
# ---------------------------------------------------------------------------


def run_activity_sync(client: OpenRouterClient | None = None) -> int:
	"""The job: fetch the activity window and upsert it. Returns the rows stored.
	On failure the error is recorded on the settings, logged, and re-raised."""
	settings = frappe.get_cached_doc(SETTINGS)
	if not settings.enabled:
		raise OpenRouterAPIError("OpenRouter sync is disabled. Enable it in OpenRouter Settings first.")
	frappe.db.set_single_value(SETTINGS, "last_activity_attempt", now_datetime())
	frappe.db.commit()
	try:
		rows = (client or _client(settings)).get_activity()
		stored, skipped, latest = upsert_rows(rows)
	except OpenRouterAPIError as exc:
		frappe.db.rollback()
		frappe.db.set_single_value(SETTINGS, {"last_activity_attempt": now_datetime(), "last_activity_error": str(exc)})
		frappe.db.commit()
		frappe.log_error(title="OpenRouter Monitor: usage sync failed", message=str(exc))
		raise
	frappe.db.set_single_value(
		SETTINGS,
		{
			"last_activity_synced": now_datetime(),
			"last_activity_through": latest,
			"last_activity_row_count": stored,
			"last_activity_error": "" if not skipped else f"{skipped} row(s) skipped because they could not be read (see the Error Log).",
		},
	)
	frappe.db.commit()
	return stored


def upsert_rows(rows: list[dict]) -> tuple[int, int, object]:
	"""Store each row, keyed by (date, endpoint). Returns (stored, skipped, latest day)."""
	stored = skipped = 0
	latest = None
	for row in rows:
		try:
			mapped = map_row(row)
			if mapped is None:
				skipped += 1
				continue
			_upsert_daily(mapped)
			stored += 1
			if latest is None or mapped["date"] > latest:
				latest = mapped["date"]
		except Exception:
			skipped += 1
			frappe.log_error(title="OpenRouter Monitor: could not store an activity row", message=frappe.get_traceback())
	frappe.db.commit()
	return stored, skipped, latest


def _num(value) -> float:
	try:
		return float(value or 0)
	except (TypeError, ValueError):
		return 0.0


def _text(value) -> str:
	return str(value).strip() if value not in (None, "") else ""


def map_row(row: dict) -> dict | None:
	"""One /activity row as a Daily Usage field dict, or None if it has no usable
	date. Every number is coalesced to 0 (the columns are NOT NULL) and unknown
	fields are ignored."""
	raw_date = _text(row.get("date"))
	if not raw_date:
		return None
	try:
		day = getdate(raw_date[:10])
	except Exception:
		return None
	model = _text(row.get("model"))
	permaslug = _text(row.get("model_permaslug"))
	provider = _text(row.get("provider_name"))
	endpoint = _text(row.get("endpoint_id")) or hashlib.sha1(f"{permaslug or model}|{provider}".encode()).hexdigest()[:16]
	return {
		"row_key": f"{day.isoformat()}-{_SAFE_KEY.sub('-', endpoint)[:100]}",
		"date": day,
		"model": model,
		"model_permaslug": permaslug,
		"endpoint_id": endpoint,
		"provider_name": provider,
		"usage_usd": round(_num(row.get("usage")), 6),
		"byok_usage_usd": round(_num(row.get("byok_usage_inference")), 6),
		"requests": int(_num(row.get("requests"))),
		"prompt_tokens": int(_num(row.get("prompt_tokens"))),
		"completion_tokens": int(_num(row.get("completion_tokens"))),
		"reasoning_tokens": int(_num(row.get("reasoning_tokens"))),
	}


def _upsert_daily(fields: dict) -> None:
	key = fields["row_key"]
	if frappe.db.exists("OpenRouter Daily Usage", key):
		frappe.db.set_value("OpenRouter Daily Usage", key, {k: v for k, v in fields.items() if k != "row_key"}, update_modified=True)
		return
	doc = frappe.new_doc("OpenRouter Daily Usage")
	doc.update(fields)
	try:
		doc.insert(ignore_permissions=True)
	except frappe.DuplicateEntryError:
		frappe.db.set_value("OpenRouter Daily Usage", key, {k: v for k, v in fields.items() if k != "row_key"}, update_modified=True)


# ---------------------------------------------------------------------------
# Balance
# ---------------------------------------------------------------------------


def run_balance_check(client: OpenRouterClient | None = None) -> float | None:
	"""The job: read /credits, store a snapshot, run the low-balance alert.
	A failure is recorded and logged but not re-raised: this runs every 30
	minutes, and the dashboard shows the error."""
	settings = frappe.get_cached_doc(SETTINGS)
	if not settings.enabled:
		return None
	try:
		credits = (client or _client(settings)).get_credits()
	except OpenRouterAPIError as exc:
		frappe.db.rollback()
		frappe.db.set_single_value(SETTINGS, {"last_balance_at": now_datetime(), "last_balance_error": str(exc)})
		frappe.db.commit()
		frappe.log_error(title="OpenRouter Monitor: balance check failed", message=str(exc))
		return None
	purchased, used = credits["total_credits"], credits["total_usage"]
	remaining = round(purchased - used, 6)
	now = now_datetime()
	frappe.get_doc(
		{
			"doctype": "OpenRouter Balance Snapshot",
			"taken_at": now,
			"total_purchased": purchased,
			"total_used": used,
			"remaining": remaining,
		}
	).insert(ignore_permissions=True)
	frappe.db.set_single_value(SETTINGS, {"last_balance_at": now, "last_remaining": remaining, "last_balance_error": ""})
	frappe.db.commit()
	check_low_balance(remaining, flt(settings.low_balance_threshold))
	return remaining


def check_low_balance(remaining: float, threshold: float) -> str | None:
	"""Open an alert when the remaining credit is below the threshold, close it
	(with a recovery message) once it is back above. Threshold 0 = off, and closes
	an alert left open from before. Returns "opened", "resolved" or None."""
	is_open = has_open_alert(SETTINGS, SETTINGS, LOW_BALANCE_ALERT)
	low = threshold > 0 and remaining < threshold
	if low and not is_open:
		dispatch_alert(
			LOW_BALANCE_ALERT,
			SETTINGS,
			SETTINGS,
			f"OpenRouter credits are low: ${remaining:,.2f} left, below the ${threshold:,.2f} alert threshold.",
		)
		return "opened"
	if not low and is_open:
		resolve_alert(SETTINGS, SETTINGS, LOW_BALANCE_ALERT)
		dispatch_recovery(
			LOW_BALANCE_ALERT,
			SETTINGS,
			SETTINGS,
			f"OpenRouter credits are back above the alert threshold: ${remaining:,.2f} left.",
		)
		return "resolved"
	return None
