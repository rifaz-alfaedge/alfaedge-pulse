# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Dashboard-facing reads for Host Health that the generic doctype-list API
can't serve directly.

``Monitored Host Site`` is a child table (``istable: 1``) — Frappe's own
permission engine always resolves a child doctype's permission check
against its *parent* doctype's context (see
``frappe.permissions.has_child_permission``: "This doctype is a child
table, permissions will be checked on parent"), completely ignoring
whatever the child doctype's own ``permissions`` block says. The generic
list API (``frappe.client.get_list``, what the frontend's
``useFrappeGetDocList`` calls under the hood) has no parent to check
against when listing every row fleet-wide, so it always denies non-
Administrator users outright — confirmed live: ``Insufficient Permission
for Monitored Host Site`` for a real System Manager account, even with
explicit read permission rows added directly to the child doctype (which
turned out to be silently unused dead weight — reverted).

This wrapper checks the one permission that actually matters (read on
``Monitored Host``, the real parent) explicitly, then reads every row with
``ignore_permissions=True`` — the same shape as any other cross-cutting
read elsewhere in this app (e.g. ``api.get_recent_backup_logs``).
"""

from __future__ import annotations

import frappe
from frappe.utils import cint


@frappe.whitelist()
def get_hosted_sites() -> list[dict]:
	frappe.has_permission("Monitored Host", "read", throw=True)
	return frappe.get_all(
		"Monitored Host Site",
		fields=["name", "parent", "bench_name", "site_name", "site_url"],
		limit_page_length=0,
		ignore_permissions=True,
	)


def _parse_exc_message(exc_info: str | None) -> str:
	"""Companion to ingest.py's ``_parse_exc_type`` — same last-line
	convention (``ExceptionType: message``), just returning the other half.
	Empty string, not a guess, if that line doesn't look like one."""
	for line in reversed((exc_info or "").splitlines()):
		line = line.strip()
		if not line:
			continue
		exc_type, _, message = line.partition(":")
		if message and " " not in exc_type.strip():
			return message.strip()
		return ""
	return ""


def _grouped_failed_jobs(monitored_host: str, resolved: int, fields: list[str]) -> list[list[dict]]:
	"""Shared fetch+group step for both endpoints below — one row per
	individual RQ job occurrence, grouped by ``failure_signature`` (root
	cause, see ``host_health/ingest.py``), largest group first. Rows within
	a group come back newest-first, so `group[0]` is always that group's
	most recent occurrence — used as the representative exc_type/job_name/
	message without a second pass to find it."""
	rows = frappe.get_all(
		"Frappe Failed Job Log",
		filters={"monitored_host": monitored_host, "resolved": cint(resolved)},
		fields=[*fields, "failure_signature"],
		order_by="last_seen desc",
		limit_page_length=0,
		ignore_permissions=True,
	)
	groups: dict[str, list[dict]] = {}
	for row in rows:
		groups.setdefault(row["failure_signature"], []).append(row)
	return sorted(groups.values(), key=lambda occurrences: len(occurrences), reverse=True)


@frappe.whitelist()
def get_failed_job_groups(monitored_host: str, resolved: int = 0) -> list[dict]:
	"""Lightweight, human-readable root-cause summary for one host's detail
	dialog — grouped and parsed the same way as ``get_failed_job_log_text``
	below, but as small JSON (no raw tracebacks) meant to render straight in
	the dashboard UI, for a reader who wants the gist without downloading a
	file. `first_seen`/`last_seen` come back as raw datetimes, formatted
	client-side (`timeAgo`) same as every other timestamp in this app,
	rather than baking a server-side date format into the response."""
	frappe.has_permission("Frappe Failed Job Log", "read", throw=True)
	ordered = _grouped_failed_jobs(
		monitored_host, resolved, ["job_name", "exc_type", "exc_info", "first_seen", "last_seen"]
	)
	return [
		{
			"exc_type": occurrences[0]["exc_type"] or "Unknown error",
			"job_name": occurrences[0]["job_name"] or "unknown method",
			"message": _parse_exc_message(occurrences[0]["exc_info"]),
			"occurrence_count": len(occurrences),
			"first_seen": min(o["first_seen"] for o in occurrences if o["first_seen"]),
			"last_seen": occurrences[0]["last_seen"],
		}
		for occurrences in ordered
	]


def _fetch_active_jobs(monitored_host: str) -> list[dict]:
	"""Every job currently executing on this host, longest-running first —
	same sort as the dashboard's own Active Jobs section, so the worst
	offender always leads here too."""
	return frappe.get_all(
		"Frappe Active Job",
		filters={"monitored_host": monitored_host},
		fields=["bench_name", "queue_name", "job_name", "worker_pid", "started_at", "elapsed_seconds", "is_long_running"],
		order_by="elapsed_seconds desc",
		limit_page_length=0,
		ignore_permissions=True,
	)


def _format_duration(total_seconds: int) -> str:
	"""Largest one or two sensible units, e.g. "45m 12s" or "1h 3m" —
	mirrors the frontend's own formatUptime so a job's elapsed time reads
	the same whether you're looking at the dashboard or this download."""
	total_seconds = max(0, int(total_seconds))
	hours, remainder = divmod(total_seconds, 3600)
	minutes, seconds = divmod(remainder, 60)
	if hours:
		return f"{hours}h {minutes}m"
	if minutes:
		return f"{minutes}m {seconds}s"
	return f"{seconds}s"


@frappe.whitelist()
def get_failed_job_log_text(monitored_host: str, resolved: int = 0) -> str:
	"""Plain-text, downloadable health log for one host's detail dialog.
	Three parts: a plain-English failed-job summary (what broke, how
	often, in words a non-Python-reader can act on) up top, currently-
	running jobs next (the same "eagle eye" data the dashboard's own
	Active Jobs section shows — included here too since a debugging
	snapshot without what's running *right now* is an incomplete one),
	then every failed occurrence's full traceback below for whoever
	actually needs to debug it — rather than either dumping 30+ raw
	tracebacks on the dashboard itself or leading with a wall of stack
	traces here. Root causes grouped by ``failure_signature`` (see
	``host_health/ingest.py``). Text formatting happens here rather than
	in the frontend so there's one place that defines what the log looks
	like, whether it's opened from the dashboard or (later) any other
	client."""
	frappe.has_permission("Frappe Failed Job Log", "read", throw=True)
	ordered = _grouped_failed_jobs(
		monitored_host, resolved, ["bench_name", "queue_name", "job_name", "exc_type", "exc_info", "failed_at", "first_seen", "last_seen"]
	)
	rows = [o for occurrences in ordered for o in occurrences]
	active_jobs = _fetch_active_jobs(monitored_host)
	long_running_count = sum(1 for j in active_jobs if j["is_long_running"])

	hostname = frappe.db.get_value("Monitored Host", monitored_host, "hostname") or monitored_host
	lines = [
		"alfaEdge Pulse — Host Health Log",
		f"Host: {hostname}",
		f"Generated: {frappe.utils.pretty_date(frappe.utils.now_datetime())}",
		"",
		f"{len(rows)} open failed job{'s' if len(rows) != 1 else ''}, "
		f"{len(ordered)} distinct root cause{'s' if len(ordered) != 1 else ''}"
		f" · {len(active_jobs)} job{'s' if len(active_jobs) != 1 else ''} currently running"
		f" ({long_running_count} long-running)",
		"",
		"ACTIVE JOBS",
		"-----------",
	]
	if not active_jobs:
		lines.append("(none currently running)")
	for job in active_jobs:
		flag = " [LONG RUNNING]" if job["is_long_running"] else ""
		lines.append(f"{job['job_name'] or 'unknown method'}{flag}")
		lines.append(
			f"   {job['bench_name']} · {job['queue_name'] or '—'} queue · worker PID {job['worker_pid'] or '—'}"
			f" · running {_format_duration(job['elapsed_seconds'])}"
			f" (since {str(job['started_at'])[:19] if job['started_at'] else 'unknown'})"
		)
		lines.append("")

	lines += [
		"",
		"FAILED JOBS SUMMARY",
		"--------------------",
	]
	for i, occurrences in enumerate(ordered, start=1):
		latest = occurrences[0]
		earliest_first_seen = min(o["first_seen"] for o in occurrences if o["first_seen"])
		message = _parse_exc_message(latest["exc_info"])
		lines.append(f"{i}. {latest['exc_type'] or 'Unknown error'} in {latest['job_name'] or 'unknown method'}")
		lines.append(
			f"   {len(occurrences)} occurrence{'s' if len(occurrences) != 1 else ''}"
			f" · first seen {frappe.utils.pretty_date(earliest_first_seen)}"
			f" · last seen {frappe.utils.pretty_date(latest['last_seen'])}"
		)
		if message:
			lines.append(f"   “{message}”")
		lines.append("")

	lines += ["", "FULL TRACEBACKS", "---------------"]
	for i, occurrences in enumerate(ordered, start=1):
		latest = occurrences[0]
		lines += ["", f"[{i}] {latest['exc_type'] or 'Unknown error'} in {latest['job_name'] or 'unknown method'}"]
		for j, occurrence in enumerate(occurrences, start=1):
			lines += [
				"",
				f"  Occurrence {j} — {occurrence['bench_name']} · {occurrence['queue_name'] or '—'} · failed {occurrence['failed_at']}",
				occurrence["exc_info"] or "  (no traceback captured)",
			]

	return "\n".join(lines)
