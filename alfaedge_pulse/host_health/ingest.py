# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""The two endpoints reached by code running on remote, comparatively
less-trusted guest machines — kept in their own small file so the
security-relevant surface stays trivially auditable in one read, rather
than mixed into a larger api.py-style module.

Auth: every ``Monitored Host`` shares one low-privilege Frappe User (see
``monitored_host.agent_user_email``), authenticated via Frappe core's own
``Authorization: token <api_key>:<api_secret>`` mechanism
(``Frappe-Authorization-Source: Monitored Host`` header) — by the time
either endpoint's body runs, core has already rejected the request if the
pair didn't match an enabled Monitored Host. Since every host resolves to
the same session user, ``_identify_monitored_host`` re-parses the header
itself to recover *which* host authenticated (core doesn't stash that
anywhere) — extracting, not re-verifying, the already-proven key.

Every alert either endpoint can raise (Service Down, Worker Degraded,
Failed Job Threshold, High Load Average, High Swap Usage, and disk/inode
Critical Resource / Resource Warning on a Resource Mount) reuses
``_handle_transition``/``dispatch_alert``/``dispatch_recovery`` from
``alerts/dispatch.py`` and ``tasks/poller.py`` verbatim — no parallel
alerting mechanism. Scheduler Stalled and Host Unreachable are evaluated
by the heartbeat watchdog instead (see ``tasks/host_health_watchdog.py``),
not here — both are staleness checks that need to keep re-evaluating even
when the agent stops pushing entirely, which only a periodic,
push-independent check can do. ``push_resource_metrics`` deliberately
never touches ``Monitored Host.last_seen``/``is_online`` — that stays
owned by ``push_status`` alone, since the resource agent is designed as an
add-on to an already-running Host Health agent on the same host, sharing
its Monitored Host identity rather than getting its own heartbeat.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json

import frappe
from frappe.utils import cint, convert_utc_to_system_timezone, flt, get_datetime, now_datetime

from alfaedge_pulse.host_health.doctype.monitored_host.monitored_host import get_host_role
from alfaedge_pulse.tasks.poller import Thresholds, _handle_transition, _track_severity

DEFAULT_CONFIRMATION_CHECKS = 3
DEFAULT_ORPHAN_WORKER_CRITICAL_THRESHOLD = 3
DEFAULT_FAILED_JOB_CRITICAL_THRESHOLD = 20
DEFAULT_LONG_RUNNING_JOB_CRITICAL_THRESHOLD_SECONDS = 1800
DEFAULT_LOAD_WARNING_PERCENT = 100
DEFAULT_LOAD_CRITICAL_PERCENT = 200
DEFAULT_SWAP_WARNING_PERCENT = 50
DEFAULT_SWAP_CRITICAL_PERCENT = 90
DEFAULT_DISK_WARNING_PERCENT = 80
DEFAULT_DISK_CRITICAL_PERCENT = 90


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
	long_running_critical_threshold = (
		cint(settings.long_running_job_critical_threshold_seconds) or DEFAULT_LONG_RUNNING_JOB_CRITICAL_THRESHOLD_SECONDS
	)
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
				bench_results.append(
					_process_bench(
						host_name, row, orphan_critical_threshold, failed_job_critical_threshold, long_running_critical_threshold
					)
				)
				benches_recorded += 1
			except Exception:
				frappe.log_error(
					title=f"Host Health: malformed bench row from {host_name}", message=frappe.get_traceback()
				)
				warnings.append(f"skipped malformed bench row: {row!r}")

		worker_health_critical = any(r["orphan_streak"] >= checks for r in bench_results)
		failed_job_critical = any(r["failed_job_streak"] >= checks for r in bench_results)
		long_running_job_critical = any(r["long_running_streak"] >= checks for r in bench_results)
		was = frappe.db.get_value(
			"Monitored Host",
			host_name,
			["worker_health_critical", "failed_job_critical", "long_running_job_critical"],
			as_dict=True,
		)
		frappe.db.set_value(
			"Monitored Host",
			host_name,
			{
				"worker_health_critical": worker_health_critical,
				"failed_job_critical": failed_job_critical,
				"long_running_job_critical": long_running_job_critical,
			},
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
		_handle_transition(
			bool(cint(was.long_running_job_critical)),
			long_running_job_critical,
			"Monitored Host",
			host_name,
			"Long Running Job",
			f"{host_name}: a job has stayed running at/beyond the long-running threshold.",
			recovery_message=f"{host_name}: no more long-running jobs.",
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


@frappe.whitelist(methods=["POST"])
def push_resource_metrics(
	reported_at: str, load: dict | None = None, swap: dict | None = None, disks: list | None = None
) -> dict:
	host_name = _identify_monitored_host()
	warnings: list[str] = []

	try:
		get_datetime(reported_at)
	except Exception:
		frappe.throw("reported_at is not a parseable timestamp", frappe.ValidationError)

	settings = frappe.get_cached_doc("Resource Monitor Settings")
	checks = cint(settings.confirmation_checks) or DEFAULT_CONFIRMATION_CHECKS
	load_thresholds = Thresholds(
		warning_percent=cint(settings.load_warning_percent) or DEFAULT_LOAD_WARNING_PERCENT,
		critical_percent=cint(settings.load_critical_percent) or DEFAULT_LOAD_CRITICAL_PERCENT,
		confirmation_checks=checks,
	)
	swap_thresholds = Thresholds(
		warning_percent=cint(settings.swap_warning_percent) or DEFAULT_SWAP_WARNING_PERCENT,
		critical_percent=cint(settings.swap_critical_percent) or DEFAULT_SWAP_CRITICAL_PERCENT,
		confirmation_checks=checks,
	)
	disk_thresholds = Thresholds(
		warning_percent=cint(settings.disk_warning_percent) or DEFAULT_DISK_WARNING_PERCENT,
		critical_percent=cint(settings.disk_critical_percent) or DEFAULT_DISK_CRITICAL_PERCENT,
		confirmation_checks=checks,
	)
	role = get_host_role(host_name)
	notify_global = role == "Production"
	now = now_datetime()

	metrics_recorded = 0
	if load is not None and swap is not None:
		try:
			was = frappe.db.get_value(
				"Monitored Host", host_name, ["load_average_critical", "swap_critical"], as_dict=True
			)
			result = _upsert_resource_metrics(host_name, load, swap, load_thresholds, swap_thresholds, now)
			frappe.db.set_value(
				"Monitored Host",
				host_name,
				{
					"load_average_critical": result["is_load_critical"],
					"swap_critical": result["is_swap_critical"],
				},
			)
			_handle_transition(
				bool(cint(was.load_average_critical)),
				result["is_load_critical"],
				"Monitored Host",
				host_name,
				"High Load Average",
				f"{host_name}: load average has stayed at/above the critical threshold.",
				recovery_message=f"{host_name}: load average back to normal.",
				notify_global=notify_global,
			)
			_handle_transition(
				bool(cint(was.swap_critical)),
				result["is_swap_critical"],
				"Monitored Host",
				host_name,
				"High Swap Usage",
				f"{host_name}: swap usage has stayed at/above the critical threshold.",
				recovery_message=f"{host_name}: swap usage back to normal.",
				notify_global=notify_global,
			)
			metrics_recorded = 1
		except Exception:
			frappe.log_error(
				title=f"Host Health: malformed load/swap payload from {host_name}", message=frappe.get_traceback()
			)
			warnings.append("skipped malformed load/swap payload")

	disks_recorded = 0
	if disks is not None:
		seen_mount_points = set()
		for row in disks:
			try:
				mount_point = _upsert_resource_mount(host_name, row, disk_thresholds, now, notify_global)
				seen_mount_points.add(mount_point)
				disks_recorded += 1
			except Exception:
				frappe.log_error(
					title=f"Host Health: malformed disk row from {host_name}", message=frappe.get_traceback()
				)
				warnings.append(f"skipped malformed disk row: {row!r}")
		# Only prune stale mounts once at least one real mount was actually
		# seen this push — an empty `disks` list is ambiguous (resource_agent.py
		# sends [] both for a genuine collection failure, e.g. /proc/mounts
		# unreadable, and would send it for a real zero-mount host, which in
		# practice never happens). Treating an all-empty push as "delete
		# everything" would wipe every Resource Mount's live state (and reset
		# its alert streak) on a single transient read failure — see the
		# stale-mount-point filter below, which would otherwise match every
		# real mount_point once seen_mount_points is empty.
		if seen_mount_points:
			_delete_stale_resource_mounts(host_name, seen_mount_points)

	frappe.db.commit()
	return {
		"status": "ok",
		"monitored_host": host_name,
		"metrics_recorded": metrics_recorded,
		"disks_recorded": disks_recorded,
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


def _process_bench(
	host_name: str,
	row: dict,
	orphan_critical_threshold: int,
	failed_job_critical_threshold: int,
	long_running_critical_threshold_seconds: int,
) -> dict:
	bench_name = row.get("bench_name")
	if not bench_name:
		raise ValueError("bench row missing bench_name")

	prior = frappe.get_all(
		"Frappe Worker Health Log",
		filters={"monitored_host": host_name, "bench_name": bench_name},
		fields=["orphan_critical_streak", "failed_job_critical_streak", "long_running_job_critical_streak"],
		order_by="timestamp desc",
		limit_page_length=1,
	)
	prior_orphan_streak = cint(prior[0].orphan_critical_streak) if prior else 0
	prior_failed_streak = cint(prior[0].failed_job_critical_streak) if prior else 0
	prior_long_running_streak = cint(prior[0].long_running_job_critical_streak) if prior else 0

	orphan_count = cint(row.get("orphan_worker_count"))
	failed_jobs = row.get("failed_jobs") or []
	failed_job_count = cint(row.get("failed_job_count")) if row.get("failed_job_count") is not None else len(failed_jobs)
	active_jobs = row.get("active_jobs") or []

	orphan_streak = prior_orphan_streak + 1 if orphan_count >= orphan_critical_threshold else 0
	failed_streak = prior_failed_streak + 1 if failed_job_count >= failed_job_critical_threshold else 0

	now = now_datetime()
	seen_active_ids = set()
	long_running_job_count = 0
	for job in active_jobs:
		job_id, is_long_running = _upsert_active_job(host_name, bench_name, job, long_running_critical_threshold_seconds, now)
		if job_id:
			seen_active_ids.add(job_id)
			if is_long_running:
				long_running_job_count += 1
	_delete_stale_active_jobs(host_name, bench_name, seen_active_ids)

	long_running_streak = prior_long_running_streak + 1 if long_running_job_count > 0 else 0

	doc = frappe.new_doc("Frappe Worker Health Log")
	doc.update(
		{
			"monitored_host": host_name,
			"bench_name": bench_name,
			"timestamp": now,
			"registered_workers": cint(row.get("registered_workers")),
			"live_worker_count": cint(row.get("live_worker_count")),
			"orphan_worker_count": orphan_count,
			"orphan_critical_streak": orphan_streak,
			"live_worker_pids": json.dumps(row.get("live_worker_pids") or []),
			"queue_depths": json.dumps(row.get("queue_depths") or {}),
			"failed_job_count": failed_job_count,
			"failed_job_critical_streak": failed_streak,
			"active_job_count": len(active_jobs),
			"long_running_job_count": long_running_job_count,
			"long_running_job_critical_streak": long_running_streak,
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
		"long_running_streak": long_running_streak,
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


def _upsert_active_job(
	host_name: str, bench_name: str, job: dict, long_running_critical_threshold_seconds: int, now
) -> tuple[str | None, bool]:
	"""Returns (rq_job_id, is_long_running) — None job_id if the row was
	malformed and skipped. Unlike failed jobs, elapsed time is computed
	here (not just stored raw) so both the streak logic right above and
	the dashboard get a single already-computed number instead of
	re-deriving it from two timestamps."""
	rq_job_id = job.get("rq_job_id")
	if not rq_job_id:
		return None, False
	started_at = _parse_remote_timestamp(job.get("started_at"))
	elapsed_seconds = int((now - started_at).total_seconds()) if started_at else 0
	is_long_running = elapsed_seconds >= long_running_critical_threshold_seconds

	fields = {
		"monitored_host": host_name,
		"bench_name": bench_name,
		"queue_name": job.get("queue_name"),
		"job_name": job.get("job_name"),
		"worker_pid": job.get("worker_pid"),
		"started_at": started_at,
		"last_seen": now,
		"elapsed_seconds": elapsed_seconds,
		"is_long_running": 1 if is_long_running else 0,
	}
	existing = frappe.db.get_value("Frappe Active Job", {"rq_job_id": rq_job_id}, "name")
	if existing:
		frappe.db.set_value("Frappe Active Job", existing, fields, update_modified=True)
		return rq_job_id, is_long_running

	doc = frappe.new_doc("Frappe Active Job")
	doc.update({"rq_job_id": rq_job_id, **fields})
	try:
		doc.insert(ignore_permissions=True)
	except frappe.DuplicateEntryError:
		# Lost a race to create this exact row to another process — same
		# fallback as Frappe Failed Job Log's _upsert_failed_job.
		frappe.db.set_value("Frappe Active Job", {"rq_job_id": rq_job_id}, fields, update_modified=True)
	return rq_job_id, is_long_running


def _delete_stale_active_jobs(host_name: str, bench_name: str, seen_ids: set) -> None:
	"""Jobs that were active last push but aren't in this push's active_jobs
	list anymore — finished normally, in the ordinary case. Deleted
	outright, not tombstoned: see Frappe Active Job's own docstring for why
	this is a live snapshot, not a history log, unlike failed jobs."""
	open_rows = frappe.get_all(
		"Frappe Active Job",
		filters={"monitored_host": host_name, "bench_name": bench_name},
		fields=["name", "rq_job_id"],
	)
	for row in open_rows:
		if row.rq_job_id not in seen_ids:
			frappe.delete_doc("Frappe Active Job", row.name, ignore_permissions=True, delete_permanently=True)


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


def _upsert_resource_metrics(
	host_name: str, load: dict, swap: dict, load_thresholds: Thresholds, swap_thresholds: Thresholds, now
) -> dict:
	"""Inserts one Resource Metric Log row, computing load/swap streaks
	from the prior row for this host — same prior-row-lookup pattern
	_process_bench already uses for Frappe Worker Health Log's streaks,
	since there's no persistent doc to mutate in-memory on an append-only
	table. Load and swap are independent metrics with different scales
	(load can exceed 100%, swap can't), so each gets its own lightweight
	``frappe._dict`` holder run through ``_track_severity`` separately —
	satisfies that function's documented contract ("any doc-like object
	with in-memory streak attrs") without editing poller.py.

	Returns ``{"is_load_critical": bool, "is_swap_critical": bool}`` for
	the caller to feed into ``_handle_transition``.
	"""
	prior = frappe.get_all(
		"Resource Metric Log",
		filters={"monitored_host": host_name},
		fields=["load_warning_streak", "load_critical_streak", "swap_warning_streak", "swap_critical_streak"],
		order_by="collected_at desc",
		limit_page_length=1,
	)
	load_holder = frappe._dict(
		warning_streak=cint(prior[0].load_warning_streak) if prior else 0,
		critical_streak=cint(prior[0].load_critical_streak) if prior else 0,
	)
	swap_holder = frappe._dict(
		warning_streak=cint(prior[0].swap_warning_streak) if prior else 0,
		critical_streak=cint(prior[0].swap_critical_streak) if prior else 0,
	)

	cpu_core_count = cint(load.get("cpu_core_count")) or 1
	load_avg_1min = flt(load.get("load_avg_1min"))
	load_normalized_percent = round((load_avg_1min / cpu_core_count) * 100, 1)
	is_load_critical, _is_load_warning = _track_severity(load_holder, load_normalized_percent, load_thresholds)

	swap_total_gb = flt(swap.get("total_gb")) if swap.get("total_gb") is not None else None
	swap_used_gb = flt(swap.get("used_gb")) if swap_total_gb else None
	swap_usage_percent = round((swap_used_gb / swap_total_gb) * 100, 1) if swap_total_gb else None
	if swap_usage_percent is not None:
		is_swap_critical, _is_swap_warning = _track_severity(swap_holder, swap_usage_percent, swap_thresholds)
	else:
		# No swap configured on this guest — never evaluated, same
		# null-not-zero convention as Proxmox Server.swap_usage. Streaks
		# stay at whatever they last were rather than resetting, in case
		# swap gets configured later and this was just a one-off gap.
		is_swap_critical = False

	doc = frappe.new_doc("Resource Metric Log")
	doc.update(
		{
			"monitored_host": host_name,
			"collected_at": now,
			"cpu_core_count": cpu_core_count,
			"load_avg_1min": load_avg_1min,
			"load_avg_5min": flt(load.get("load_avg_5min")),
			"load_avg_15min": flt(load.get("load_avg_15min")),
			"load_normalized_percent": load_normalized_percent,
			"load_warning_streak": load_holder.warning_streak,
			"load_critical_streak": load_holder.critical_streak,
			"swap_used_gb": swap_used_gb,
			"swap_total_gb": swap_total_gb,
			"swap_usage_percent": swap_usage_percent,
			"swap_warning_streak": swap_holder.warning_streak,
			"swap_critical_streak": swap_holder.critical_streak,
		}
	)
	doc.insert(ignore_permissions=True)

	return {"is_load_critical": is_load_critical, "is_swap_critical": is_swap_critical}


def _upsert_resource_mount(host_name: str, row: dict, thresholds: Thresholds, now, notify_global: bool) -> str:
	"""Upserts one Resource Mount by (monitored_host, mount_point) — same
	shape as _upsert_datastore, adapted for a hash-autoname doctype (no
	deterministic docname to build, unlike Proxmox Datastore's
	``{server}-{name}`` — same reason Service Status Log's _upsert_service
	looks its row up by filter too). Also inserts the corresponding
	Resource Disk Sample history row. Returns the mount_point, so the
	caller can track which mounts were seen this push (see
	_delete_stale_resource_mounts).
	"""
	mount_point = row.get("mount_point")
	if not mount_point:
		raise ValueError("disk row missing mount_point")

	used_bytes = flt(row.get("used_bytes"))
	total_bytes = flt(row.get("total_bytes"))
	usage_percent = round((used_bytes / total_bytes) * 100, 1) if total_bytes else 0

	inodes_used = row.get("inodes_used")
	inodes_total = row.get("inodes_total")
	inodes_total_int = cint(inodes_total)
	inode_usage_percent = round((cint(inodes_used) / inodes_total_int) * 100, 1) if inodes_total_int else None

	existing_name = frappe.db.get_value(
		"Resource Mount", {"monitored_host": host_name, "mount_point": mount_point}, "name"
	)
	if existing_name:
		doc = frappe.get_doc("Resource Mount", existing_name)
	else:
		doc = frappe.new_doc("Resource Mount")
		doc.monitored_host = host_name
		doc.mount_point = mount_point

	old_is_critical = cint(doc.is_critical)
	old_is_warning = cint(doc.is_warning)

	# Governs disk-space and inode fullness with a single threshold pair
	# via max() — same approach _apply_host_status uses to share one
	# CPU/RAM pair (see Resource Monitor Settings' disk_critical_percent).
	readings = [usage_percent] + ([inode_usage_percent] if inode_usage_percent is not None else [])
	is_critical, is_warning = _track_severity(doc, max(readings), thresholds)

	doc.fstype = row.get("fstype")
	doc.used_gb = round(used_bytes / 1e9, 2)
	doc.total_gb = round(total_bytes / 1e9, 2)
	doc.usage_percent = usage_percent
	doc.inodes_used = cint(inodes_used) if inodes_used is not None else None
	doc.inodes_total = cint(inodes_total) if inodes_total is not None else None
	doc.inode_usage_percent = inode_usage_percent
	doc.is_critical = 1 if is_critical else 0
	doc.is_warning = 1 if is_warning else 0
	doc.last_synced = now
	doc.save(ignore_permissions=True)

	frappe.get_doc(
		{
			"doctype": "Resource Disk Sample",
			"resource_mount": doc.name,
			"monitored_host": host_name,
			"mount_point": mount_point,
			"collected_at": now,
			"used_bytes": used_bytes,
			"total_bytes": total_bytes,
			"usage_percent": usage_percent,
			"inodes_used": inodes_used,
			"inodes_total": inodes_total,
			"inode_usage_percent": inode_usage_percent,
		}
	).insert(ignore_permissions=True)

	# Reuses the existing "Critical Resource"/"Resource Warning" alert
	# types verbatim — a filling mount is the same shape/urgency as a
	# filling Proxmox Datastore, so no new alert_type is needed here (only
	# the Monitored Host-level load/swap checks got new ones).
	_handle_transition(
		old_is_critical,
		is_critical,
		"Resource Mount",
		doc.name,
		"Critical Resource",
		f"{host_name}: {mount_point} {round(usage_percent)}% full (above {thresholds.critical_percent}%)",
		recovery_message=f"{host_name}: {mount_point} back below {thresholds.critical_percent}% full (now {round(usage_percent)}%)",
		notify_global=notify_global,
	)
	_handle_transition(
		old_is_warning,
		is_warning,
		"Resource Mount",
		doc.name,
		"Resource Warning",
		f"{host_name}: {mount_point} {round(usage_percent)}% full "
		f"(above {thresholds.warning_percent}% for {thresholds.confirmation_checks}+ checks)",
		notify_global=notify_global,
	)

	return mount_point


def _delete_stale_resource_mounts(host_name: str, seen_mount_points: set) -> None:
	"""Mounts that were reported on a previous push but aren't in this
	push's disks list anymore — unmounted, or the disk row was dropped.
	Deleted outright, not tombstoned: Resource Mount is a live snapshot,
	same reasoning as Frappe Active Job's own docstring. Resource Disk
	Sample history for a deleted mount is left alone — only the retention
	purge ever removes it — so a recently-unmounted disk's chart still
	works for a while."""
	stale = frappe.get_all(
		"Resource Mount",
		filters={"monitored_host": host_name, "mount_point": ["not in", list(seen_mount_points) or [""]]},
		pluck="name",
	)
	for name in stale:
		frappe.delete_doc("Resource Mount", name, ignore_permissions=True, force=True)
