# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Background sync of Bifrost's request logs into LLM Usage Log.

Design notes (the "why" behind the shape of this module):

- **A plain self-throttled function on the existing every-minute cron, not
  a bounded loop with a heartbeat/watchdog.** Proxmox's poller needs that
  machinery because it wants ~20s granularity, below Frappe's one-minute
  scheduler floor. Bifrost sync only needs to run every few minutes, so a
  cheap "has enough time passed?" guard at the top of ``sync_bifrost_logs``
  is sufficient — and, critically, it makes ``Bifrost Settings.sync_interval_minutes``
  live-editable from Desk with no code/hooks.py change or restart, matching
  how ``Proxmox Monitor Settings.poll_interval_seconds`` already behaves.
- **Idempotent upsert, not insert-only.** Every ``LLM Usage Log`` document
  name is deterministically ``Bifrost-{external_id}`` (see the doctype's
  ``autoname``), so re-processing the same Bifrost log row — whether from
  the checkpoint overlap, a retried page, or the processing-reconciliation
  pass — always resolves to the same document and just overwrites it.
- **A 5-minute overlap on every window, not an exact boundary.** Bifrost's
  ``timestamp`` reflects when a request *started*, which may only become
  visible via ``/api/logs`` slightly later. Re-querying a small trailing
  window every cycle costs nothing (upsert is idempotent) and closes that
  gap.
- **Pagination stops on an empty page, not just ``total_count``.** Bifrost
  is a live system — new rows can land mid-pull, shifting ``total_count``
  out from under an in-progress loop. ``total_count`` is only used as a
  ``>=`` shortcut once a page also comes back non-empty.
- **``processing`` rows are reconciled separately.** A request logged as
  ``processing`` at sync time later flips to ``success``/``error`` *in
  place* (same Bifrost id) — if that happens after its original timestamp
  has aged out of the checkpoint's overlap window, the main incremental
  pass will never see it again on its own.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import frappe
from frappe.utils import add_to_date, cint, convert_utc_to_system_timezone, get_datetime, now_datetime

from proxmox_monitor.bifrost_client.base import BifrostAPIError, BifrostClient
from frappe.utils.password import get_decrypted_password

DEFAULT_SYNC_INTERVAL_MINUTES = 15
DEFAULT_BACKFILL_DAYS = 30
CHECKPOINT_OVERLAP_MINUTES = 5
PAGE_LIMIT = 1000
MAX_PAGES = 500
PROCESSING_MIN_AGE = timedelta(minutes=2)
PROCESSING_MAX_AGE = timedelta(hours=48)


def sync_bifrost_logs() -> None:
	"""Cron entrypoint — self-throttled, never raises.

	Wired to the existing every-minute cron in hooks.py alongside the
	Proxmox watchdog. Most invocations are a no-op (the throttle guard
	below returns immediately); only one in every ``sync_interval_minutes``
	actually talks to Bifrost.
	"""
	try:
		settings = frappe.get_cached_doc("Bifrost Settings")
		if not settings.enabled:
			return
		if settings.last_synced:
			interval = cint(settings.sync_interval_minutes) or DEFAULT_SYNC_INTERVAL_MINUTES
			elapsed_minutes = (now_datetime() - get_datetime(settings.last_synced)).total_seconds() / 60
			if elapsed_minutes < interval:
				return
		_run_sync(settings)
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title="Bifrost Monitor: sync cycle failed unexpectedly", message=frappe.get_traceback())


def run_sync_now() -> int:
	"""Manual "Sync Now" entrypoint — bypasses the throttle and raises on
	failure so the whitelisted button handler can show it to the user."""
	settings = frappe.get_cached_doc("Bifrost Settings")
	if not settings.enabled:
		raise BifrostAPIError("Bifrost sync is disabled — enable it in Bifrost Settings first.")
	return _run_sync(settings)


def _run_sync(settings) -> int:
	password = get_decrypted_password("Bifrost Settings", "Bifrost Settings", "password", raise_exception=False)
	if not password or not settings.username or not settings.base_url:
		raise BifrostAPIError("Base URL / Admin Username / Admin Password are not fully configured in Bifrost Settings.")

	client = BifrostClient(settings.base_url, settings.username, password, bool(settings.verify_ssl))
	sync_start = now_datetime()
	if settings.last_synced_through:
		checkpoint = get_datetime(settings.last_synced_through)
	else:
		checkpoint = add_to_date(sync_start, days=-(cint(settings.initial_backfill_days) or DEFAULT_BACKFILL_DAYS))
	window_start = add_to_date(checkpoint, minutes=-CHECKPOINT_OVERLAP_MINUTES)

	try:
		synced, last_seen, fully_drained = _pull_and_upsert(client, window_start, sync_start)
		_reconcile_open_processing(client, sync_start)

		new_checkpoint = sync_start if fully_drained else (last_seen or checkpoint)
		frappe.db.set_single_value(
			"Bifrost Settings",
			{
				"last_synced": now_datetime(),
				"last_synced_through": new_checkpoint,
				"last_sync_row_count": synced,
				"last_error": "",
			},
		)
		frappe.db.commit()
		return synced
	except BifrostAPIError as e:
		frappe.db.rollback()
		frappe.db.set_single_value("Bifrost Settings", "last_error", str(e))
		frappe.db.commit()
		raise


def _pull_and_upsert(client: BifrostClient, start_time: datetime, end_time: datetime) -> tuple[int, datetime | None, bool]:
	"""Paginate ``/api/logs`` over [start_time, end_time), upserting every row.

	Returns ``(rows_synced, last_seen_timestamp, fully_drained)``.
	``fully_drained`` is False only if ``MAX_PAGES`` was hit — the caller
	must not advance its checkpoint past ``last_seen_timestamp`` in that
	case, so the next cycle picks up the undrained remainder.
	"""
	offset = 0
	last_seen: datetime | None = None
	synced = 0

	for _ in range(MAX_PAGES):
		page = client.get(
			"/api/logs",
			params={
				"start_time": start_time.isoformat(),
				"end_time": end_time.isoformat(),
				"sort_by": "timestamp",
				"order": "asc",
				"limit": PAGE_LIMIT,
				"offset": offset,
			},
		)
		logs = page.get("logs") or []
		if not logs:
			return synced, last_seen, True

		for row in logs:
			try:
				_upsert_log(row)
				synced += 1
				last_seen = _parse_bifrost_timestamp(row["timestamp"])
			except Exception:
				frappe.log_error(
					title=f"Bifrost Monitor: failed to upsert log {row.get('id')}",
					message=frappe.get_traceback(),
				)
		frappe.db.commit()

		offset += len(logs)
		total_count = (page.get("pagination") or {}).get("total_count") or 0
		if offset >= total_count:
			return synced, last_seen, True

	return synced, last_seen, False


def _reconcile_open_processing(client: BifrostClient, sync_start: datetime) -> None:
	"""Re-pull the time window covering any stuck ``processing`` rows so
	they pick up their final status/cost even if their original timestamp
	has already aged out of the checkpoint's overlap window."""
	cutoff_recent = add_to_date(sync_start, minutes=-int(PROCESSING_MIN_AGE.total_seconds() / 60))
	cutoff_old = add_to_date(sync_start, hours=-int(PROCESSING_MAX_AGE.total_seconds() / 3600))

	rows = frappe.db.sql(
		"""
		select min(request_timestamp)
		from `tabLLM Usage Log`
		where source = 'Bifrost' and status = 'processing'
		and request_timestamp < %s and request_timestamp > %s
		""",
		(cutoff_recent, cutoff_old),
	)
	oldest_open = rows[0][0] if rows else None
	if not oldest_open:
		return
	_pull_and_upsert(client, oldest_open, sync_start)


def _upsert_log(row: dict) -> None:
	docname = f"Bifrost-{row['id']}"
	fields = _map_bifrost_row(row)
	if frappe.db.exists("LLM Usage Log", docname):
		doc = frappe.get_doc("LLM Usage Log", docname)
		doc.update(fields)
		doc.save(ignore_permissions=True)
	else:
		doc = frappe.new_doc("LLM Usage Log")
		doc.update({"source": "Bifrost", "external_id": row["id"], **fields})
		doc.insert(ignore_permissions=True)


def _map_bifrost_row(row: dict) -> dict:
	"""Flatten one Bifrost `/api/logs` row into LLM Usage Log's fields."""
	token_usage = row.get("token_usage") or {}
	prompt_details = token_usage.get("prompt_tokens_details") or {}
	completion_details = token_usage.get("completion_tokens_details") or {}
	cost = token_usage.get("cost") or {}

	return {
		"provider": row.get("provider"),
		"model": row.get("model"),
		"virtual_key_name": row.get("virtual_key_name"),
		"status": row.get("status"),
		"object_type": row.get("object"),
		"is_stream": 1 if row.get("stream") else 0,
		"number_of_retries": row.get("number_of_retries") or 0,
		"request_timestamp": _parse_bifrost_timestamp(row["timestamp"]),
		"created_at": _parse_bifrost_timestamp(row["created_at"]) if row.get("created_at") else None,
		"latency_ms": row.get("latency"),
		"prompt_tokens": token_usage.get("prompt_tokens"),
		"completion_tokens": token_usage.get("completion_tokens"),
		"total_tokens": token_usage.get("total_tokens"),
		"cached_read_tokens": prompt_details.get("cached_read_tokens"),
		"cached_write_tokens": prompt_details.get("cached_write_tokens"),
		"reasoning_tokens": completion_details.get("reasoning_tokens"),
		"audio_tokens": prompt_details.get("audio_tokens"),
		"image_tokens": prompt_details.get("image_tokens"),
		"total_cost": row.get("cost"),
		"input_tokens_cost": cost.get("input_tokens_cost"),
		"output_tokens_cost": cost.get("output_tokens_cost"),
		"reasoning_tokens_cost": cost.get("reasoning_tokens_cost"),
		"raw_log": json.dumps(row, indent=2),
	}


def _parse_bifrost_timestamp(value: str) -> datetime:
	"""Parse a Bifrost RFC3339/ISO8601 timestamp and convert it to Frappe's
	naive-in-system-timezone storage convention (see ``now_datetime``).

	This codebase has already been bitten once by naive local-vs-UTC
	datetime handling (``poller.py``'s ``_detect_log_timezone_offset``, an
	IST host silently shifting backup timestamps by 5.5h) — Bifrost's
	timestamps are UTC (explicit offset in the ISO string), so this always
	converts explicitly rather than assuming the string's naive value is
	already in system time.
	"""
	parsed = datetime.fromisoformat(value)
	if parsed.tzinfo is None:
		parsed = parsed.replace(tzinfo=timezone.utc)
	return convert_utc_to_system_timezone(parsed).replace(tzinfo=None)
