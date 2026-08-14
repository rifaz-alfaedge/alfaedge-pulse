# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""The one endpoint reached by code running on remote, comparatively
less-trusted guest machines — kept in its own small file so the
security-relevant surface stays trivially auditable in one read, rather
than mixed into a larger api.py-style module.

Auth: every ``Monitored Host`` shares one low-privilege Frappe User (see
``monitored_host.agent_user_email``), authenticated via Frappe core's own
``Authorization: token <api_key>:<api_secret>`` mechanism
(``Frappe-Authorization-Source: Monitored Host`` header) — by the time
``push_status``'s body runs, core has already rejected the request if the
pair didn't match an enabled Monitored Host. Since every host resolves to
the same session user, ``_identify_monitored_host`` re-parses the header
itself to recover *which* host authenticated (core doesn't stash that
anywhere) — extracting, not re-verifying, the already-proven key.

Every alert this module can raise (Service Down, Worker Degraded, Failed
Job Threshold) reuses ``_handle_transition``/``dispatch_alert``/
``dispatch_recovery`` from ``alerts/dispatch.py`` and ``tasks/poller.py``
verbatim — no parallel alerting mechanism. Scheduler Stalled and Host
Unreachable are evaluated by the heartbeat watchdog instead (see
``tasks/host_health_watchdog.py``), not here — both are staleness checks
that need to keep re-evaluating even when the agent stops pushing
entirely, which only a periodic, push-independent check can do.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json

import frappe
from frappe.utils import cint, convert_utc_to_system_timezone, get_datetime, now_datetime

from proxmox_monitor.host_health.doctype.monitored_host.monitored_host import get_host_role
from proxmox_monitor.tasks.poller import _handle_transition

DEFAULT_CONFIRMATION_CHECKS = 3
DEFAULT_ORPHAN_WORKER_CRITICAL_THRESHOLD = 3
DEFAULT_FAILED_JOB_CRITICAL_THRESHOLD = 20


def _parse_remote_timestamp(value):
	"""Any timestamp value coming from the agent's payload (RQ's own
	``job.ended_at`` is UTC-aware, stringified as e.g.
	``2026-08-06 17:26:32.788998+00:00``) has to be stripped down to a
	naive local datetime before it's stored — MariaDB's DATETIME columns
	reject a value with a UTC offset attached outright (confirmed:
	``Incorrect datetime value ... for column`` on insert), and every other
	datetime field in this app is naive-local, matching
	``frappe.utils.now_datetime()``'s own convention."""
	if not value:
		return None
	dt = get_datetime(value)
	if dt.tzinfo is not None:
		dt = convert_utc_to_system_timezone(dt).replace(tzinfo=None)
	return dt


def _identify_monitored_host() -> str:
	"""Re-derives which Monitored Host authenticated this request, from the
	same Authorization header core already validated — see this module's
	docstring for why re-verifying the secret here isn't necessary."""
	header = frappe.get_request_header("Authorization", "")
	parts = header.split(" ", 1)
	if len(parts) != 2:
		frappe.throw("Missing or malformed Authorization header", frappe.AuthenticationError)
	scheme, token = parts[0].lower(), parts[1]
	try:
		if scheme == "basic":
			api_key, _secret = frappe.safe_decode(base64.b64decode(token)).split(":", 1)
		elif scheme == "token":
			api_key, _secret = token.split(":", 1)
		else:
			frappe.throw("Unsupported Authorization scheme", frappe.AuthenticationError)
	except (ValueError, binascii.Error):
		frappe.throw("Malformed Authorization header", frappe.AuthenticationError)

	host = frappe.db.get_value("Monitored Host", {"api_key": api_key, "enabled": 1}, "name")
	if not host:
		frappe.throw("Unknown or disabled agent key", frappe.AuthenticationError)
	return host


@frappe.whitelist(methods=["POST"])
def push_status(
	reported_at: str, services: list | None = None, benches: list | None = None, sites: list | None = None
) -> dict:
	host_name = _identify_monitored_host()
	warnings: list[str] = []

	try:
		get_datetime(reported_at)
	except Exception:
		frappe.throw("reported_at is not a parseable timestamp", frappe.ValidationError)

	settings = frappe.get_cached_doc("Host Monitor Settings")
	checks = cint(settings.confirmation_checks) or DEFAULT_CONFIRMATION_CHECKS
	orphan_critical_threshold = cint(settings.orphan_worker_critical_threshold) or DEFAULT_ORPHAN_WORKER_CRITICAL_THRESHOLD
	failed_job_critical_threshold = cint(settings.failed_job_critical_threshold) or DEFAULT_FAILED_JOB_CRITICAL_THRESHOLD
	role = get_host_role(host_name)
	notify_global = role == "Production"

	now = now_datetime()
	# Heartbeat/last_seen always uses the server's own clock, never the
	# remote payload's — avoids false Host Unreachable positives from
	# clock drift across a fleet of guests the server doesn't control.
	frappe.db.set_value("Monitored Host", host_name, {"last_seen": now, "is_online": 1})

	services_recorded = 0
	if services is not None:
		for row in services:
			try:
				_upsert_service(host_name, row, checks, notify_global)
				services_recorded += 1
			except Exception:
				frappe.log_error(
					title=f"Host Health: malformed service row from {host_name}", message=frappe.get_traceback()
				)
				warnings.append(f"skipped malformed service row: {row!r}")

	benches_recorded = 0
	if benches is not None:
		bench_results = []
		for row in benches:
			try:
				bench_results.append(_process_bench(host_name, row, orphan_critical_threshold, failed_job_critical_threshold))
				benches_recorded += 1
			except Exception:
				frappe.log_error(
					title=f"Host Health: malformed bench row from {host_name}", message=frappe.get_traceback()
				)
				warnings.append(f"skipped malformed bench row: {row!r}")

		worker_health_critical = any(r["orphan_streak"] >= checks for r in bench_results)
		failed_job_critical = any(r["failed_job_streak"] >= checks for r in bench_results)
		was = frappe.db.get_value("Monitored Host", host_name, ["worker_health_critical", "failed_job_critical"], as_dict=True)
		frappe.db.set_value(
			"Monitored Host",
			host_name,
			{"worker_health_critical": worker_health_critical, "failed_job_critical": failed_job_critical},
		)
		_handle_transition(
			bool(cint(was.worker_health_critical)),
			worker_health_critical,
			"Monitored Host",
			host_name,
			"Worker Degraded",
			f"{host_name}: orphaned RQ worker count has stayed at/above the critical threshold.",
			recovery_message=f"{host_name}: orphan worker count back to normal.",
			notify_global=notify_global,
		)
		_handle_transition(
			bool(cint(was.failed_job_critical)),
			failed_job_critical,
			"Monitored Host",
			host_name,
			"Failed Job Threshold",
			f"{host_name}: failed job count has stayed at/above the critical threshold.",
			recovery_message=f"{host_name}: failed job count back under threshold.",
			notify_global=notify_global,
		)

		scheduler_runs = [_parse_remote_timestamp(r["scheduler_last_run"]) for r in bench_results if r.get("scheduler_last_run")]
		scheduler_runs = [d for d in scheduler_runs if d]
		if scheduler_runs:
			frappe.db.set_value("Monitored Host", host_name, "scheduler_last_run", max(scheduler_runs))

	sites_recorded = 0
	if sites is not None:
		sites_recorded = _replace_hosted_sites(host_name, sites, warnings)

	frappe.db.commit()
	return {
		"status": "ok",
		"monitored_host": host_name,
		"services_recorded": services_recorded,
		"benches_recorded": benches_recorded,
		"sites_recorded": sites_recorded,
		"warnings": warnings,
	}


def _upsert_service(host_name: str, row: dict, checks: int, notify_global: bool) -> None:
	unit_name = row.get("unit_name")
	if not unit_name:
		raise ValueError("service row missing unit_name")
	service_name = row.get("service_name") or unit_name
	current_state = row.get("current_state") or "unknown"
	if current_state not in ("active", "inactive", "failed", "activating", "deactivating", "unknown"):
		current_state = "unknown"
	now = now_datetime()

	existing = frappe.db.get_value(
		"Service Status Log",
		{"monitored_host": host_name, "service_name": service_name},
		["name", "current_state", "down_streak", "is_down"],
		as_dict=True,
	)

	down_streak = (existing.down_streak if existing else 0) or 0
	down_streak = down_streak + 1 if current_state != "active" else 0
	was_down = bool(cint(existing.is_down)) if existing else False
	is_down = down_streak >= checks

	fields = {
		"monitored_host": host_name,
		"service_name": service_name,
		"unit_name": unit_name,
		"current_state": current_state,
		"is_down": 1 if is_down else 0,
		"down_streak": down_streak,
		"last_checked": now,
	}
	if not existing or existing.current_state != current_state:
		fields["last_state_change"] = now

	if existing:
		frappe.db.set_value("Service Status Log", existing.name, fields, update_modified=True)
		log_name = existing.name
	else:
		doc = frappe.new_doc("Service Status Log")
		doc.update(fields)
		try:
			doc.insert(ignore_permissions=True)
			log_name = doc.name
		except frappe.DuplicateEntryError:
			# Lost a race to another concurrent push for this same row —
			# fall back to updating it, same pattern as LLM Usage Log's
			# _upsert_log.
			log_name = frappe.db.get_value("Service Status Log", {"monitored_host": host_name, "service_name": service_name}, "name")
			frappe.db.set_value("Service Status Log", log_name, fields, update_modified=True)

	_handle_transition(
		was_down,
		is_down,
		"Service Status Log",
		log_name,
		"Service Down",
		f"{host_name}: service '{service_name}' is down ({current_state}).",
		recovery_message=f"{host_name}: service '{service_name}' is back up.",
		notify_global=notify_global,
	)


def _process_bench(host_name: str, row: dict, orphan_critical_threshold: int, failed_job_critical_threshold: int) -> dict:
	bench_name = row.get("bench_name")
	if not bench_name:
		raise ValueError("bench row missing bench_name")

	prior = frappe.get_all(
		"Frappe Worker Health Log",
		filters={"monitored_host": host_name, "bench_name": bench_name},
		fields=["orphan_critical_streak", "failed_job_critical_streak"],
		order_by="timestamp desc",
		limit_page_length=1,
	)
	prior_orphan_streak = cint(prior[0].orphan_critical_streak) if prior else 0
	prior_failed_streak = cint(prior[0].failed_job_critical_streak) if prior else 0

	orphan_count = cint(row.get("orphan_worker_count"))
	failed_jobs = row.get("failed_jobs") or []
	failed_job_count = cint(row.get("failed_job_count")) if row.get("failed_job_count") is not None else len(failed_jobs)

	orphan_streak = prior_orphan_streak + 1 if orphan_count >= orphan_critical_threshold else 0
	failed_streak = prior_failed_streak + 1 if failed_job_count >= failed_job_critical_threshold else 0

	doc = frappe.new_doc("Frappe Worker Health Log")
	doc.update(
		{
			"monitored_host": host_name,
			"bench_name": bench_name,
			"timestamp": now_datetime(),
			"registered_workers": cint(row.get("registered_workers")),
			"live_worker_count": cint(row.get("live_worker_count")),
			"orphan_worker_count": orphan_count,
			"orphan_critical_streak": orphan_streak,
			"live_worker_pids": json.dumps(row.get("live_worker_pids") or []),
			"queue_depths": json.dumps(row.get("queue_depths") or {}),
			"failed_job_count": failed_job_count,
			"failed_job_critical_streak": failed_streak,
		}
	)
	doc.insert(ignore_permissions=True)

	seen_ids = set()
	for job in failed_jobs:
		job_id = _upsert_failed_job(host_name, bench_name, job)
		if job_id:
			seen_ids.add(job_id)
	_resolve_missing_failed_jobs(host_name, bench_name, seen_ids)

	return {
		"bench_name": bench_name,
		"orphan_streak": orphan_streak,
		"failed_job_streak": failed_streak,
		"scheduler_last_run": row.get("scheduler_last_run"),
	}


def _parse_exc_type(exc_info: str | None) -> str:
	"""Best-effort exception class name from a raw traceback string — the
	last non-empty line is normally ``SomeException: message`` (Python's own
	``traceback.format_exception`` convention). "Unknown" if that line
	doesn't look like one (empty exc_info, or a format we don't recognize)
	rather than guessing wrong."""
	for line in reversed((exc_info or "").splitlines()):
		line = line.strip()
		if not line:
			continue
		name = line.split(":", 1)[0].strip()
		if name and " " not in name:
			return name
		return "Unknown"
	return "Unknown"


def _failure_signature(exc_type: str, job_name: str | None) -> str:
	"""Root-cause grouping key — same exception type from the same enqueued
	method collapses into one signature, across hosts and over time. Not a
	security boundary, just a grouping key — 16 hex chars is plenty."""
	return hashlib.sha256(f"{exc_type}::{job_name or 'unknown'}".encode()).hexdigest()[:16]


def _upsert_failed_job(host_name: str, bench_name: str, job: dict) -> str | None:
	rq_job_id = job.get("rq_job_id")
	if not rq_job_id:
		return None
	now = now_datetime()
	job_name = job.get("job_name")
	exc_type = _parse_exc_type(job.get("exc_info"))
	fields = {
		"monitored_host": host_name,
		"bench_name": bench_name,
		"queue_name": job.get("queue_name"),
		"job_name": job_name,
		"failed_at": _parse_remote_timestamp(job.get("failed_at")),
		"last_seen": now,
		"resolved": 0,
		"resolved_at": None,
		"exc_info": job.get("exc_info"),
		"exc_type": exc_type,
		"failure_signature": _failure_signature(exc_type, job_name),
	}
	existing = frappe.db.get_value("Frappe Failed Job Log", {"rq_job_id": rq_job_id}, "name")
	if existing:
		frappe.db.set_value("Frappe Failed Job Log", existing, fields, update_modified=True)
		return rq_job_id

	doc = frappe.new_doc("Frappe Failed Job Log")
	doc.update({"rq_job_id": rq_job_id, "first_seen": now, **fields})
	try:
		doc.insert(ignore_permissions=True)
	except frappe.DuplicateEntryError:
		# Lost a race to create this exact row to another process — same
		# fallback as LLM Usage Log's _upsert_log.
		frappe.db.set_value("Frappe Failed Job Log", {"rq_job_id": rq_job_id}, fields, update_modified=True)
	return rq_job_id


def _resolve_missing_failed_jobs(host_name: str, bench_name: str, seen_ids: set) -> None:
	"""Jobs that were open last push but aren't in this push's failed_jobs
	list anymore — requeued, manually cleared, or expired out of RQ's
	FailedJobRegistry. Marked resolved, not deleted, so there's still a
	report of what failed over time (see the doctype's own docstring)."""
	open_rows = frappe.get_all(
		"Frappe Failed Job Log",
		filters={"monitored_host": host_name, "bench_name": bench_name, "resolved": 0},
		fields=["name", "rq_job_id"],
	)
	now = now_datetime()
	for row in open_rows:
		if row.rq_job_id not in seen_ids:
			frappe.db.set_value("Frappe Failed Job Log", row.name, {"resolved": 1, "resolved_at": now})


def _replace_hosted_sites(host_name: str, sites: list, warnings: list) -> int:
	"""Point-in-time inventory snapshot, not an incremental metric — the
	whole table is replaced wholesale on every push, unlike services/
	benches which only touch rows actually present in the payload."""
	valid_rows = []
	for row in sites:
		site_name = row.get("site_name")
		if not site_name:
			warnings.append(f"skipped site row missing site_name: {row!r}")
			continue
		valid_rows.append(
			{"bench_name": row.get("bench_name"), "site_name": site_name, "site_url": row.get("site_url") or None}
		)

	doc = frappe.get_doc("Monitored Host", host_name)
	doc.set("hosted_sites", [])
	for row in valid_rows:
		doc.append("hosted_sites", row)
	doc.save(ignore_permissions=True)
	return len(valid_rows)
