# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Polls every enabled Monitored Host over SSH and stores what it finds.

Same shape as ``tasks/poller.py`` (read its docstring for the why): a
bounded background loop on the ``long`` queue, kept alive by a heartbeat
key plus a once-a-minute watchdog cron. Frappe's scheduler cannot go below
one minute, so the sub-minute cadence lives in the loop.

Threading: the SSH round-trips run in a small thread pool (they only touch
``Target``/key objects, never Frappe state, which is thread-local). All
database writes happen back on the loop's own thread as each host's result
arrives, through ``host_health/ingest.py``'s ``process_status`` and
``process_resources``.

Nothing is polled unless ``SSH Settings`` → *Polling Enabled* is on, so a
restored copy of production stays silent until someone turns it on.
"""

from __future__ import annotations

import signal
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import frappe
from frappe.utils import cint

from alfaedge_pulse.host_health import ssh_client
from alfaedge_pulse.host_health.ingest import process_performance, process_resources, process_status
from alfaedge_pulse.tasks.jobs import POLLER_QUEUE

POLLER_JOB_ID = "alfaedge_pulse_host_poller"
HEARTBEAT_CACHE_KEY = "alfaedge_pulse:host_poller_heartbeat"
MAX_LOOP_SECONDS = 300
STALE_JOB_SECONDS = MAX_LOOP_SECONDS + 300
DEFAULT_INTERVAL_SECONDS = 30
DEFAULT_PARALLELISM = 8


def _enabled() -> bool:
	return bool(cint(frappe.db.get_single_value("SSH Settings", "enabled")))


def ensure_host_poller_running() -> None:
	"""Watchdog: (re)start the loop if its heartbeat went stale. Does nothing
	while SSH polling is switched off."""
	if not _enabled():
		return
	if frappe.cache().get_value(HEARTBEAT_CACHE_KEY):
		return

	from alfaedge_pulse.tasks.jobs import clear_stale_job

	clear_stale_job(POLLER_JOB_ID, STALE_JOB_SECONDS, "host poller")
	frappe.enqueue(
		"alfaedge_pulse.tasks.host_poller.run_host_poll_loop",
		queue=POLLER_QUEUE,
		timeout=MAX_LOOP_SECONDS + 120,
		job_id=POLLER_JOB_ID,
		deduplicate=True,
	)


def run_host_poll_loop() -> None:
	"""One bounded run (up to MAX_LOOP_SECONDS), then returns normally."""
	stop_requested = False

	def _request_stop(signum, frame):
		nonlocal stop_requested
		stop_requested = True

	previous_handler = signal.signal(signal.SIGTERM, _request_stop)
	start = time.monotonic()
	try:
		while not stop_requested and (time.monotonic() - start) < MAX_LOOP_SECONDS:
			if not _enabled():
				return
			interval = cint(frappe.db.get_single_value("SSH Settings", "poll_interval_seconds")) or DEFAULT_INTERVAL_SECONDS
			frappe.cache().set_value(HEARTBEAT_CACHE_KEY, "1", expires_in_sec=max(interval * 4, 120))

			cycle_started = time.monotonic()
			try:
				poll_all_hosts()
			except Exception:
				frappe.db.rollback()
				frappe.log_error(title="Host Health: SSH poll cycle failed", message=frappe.get_traceback())

			remaining = max(1, interval - int(time.monotonic() - cycle_started))
			for _ in range(remaining):
				if stop_requested:
					break
				time.sleep(1)
	finally:
		signal.signal(signal.SIGTERM, previous_handler)


def poll_all_hosts() -> None:
	"""One pass over every enabled host."""
	hosts = frappe.get_all("Monitored Host", filters={"enabled": 1}, pluck="name")
	if not hosts:
		return

	try:
		pkey = ssh_client.load_private_key()
	except ssh_client.SSHPollError as exc:
		for name in hosts:
			_record_failure(name, exc.kind, str(exc), 0)
		return

	parallelism = cint(frappe.db.get_single_value("SSH Settings", "parallelism")) or DEFAULT_PARALLELISM
	targets = {}
	for name in hosts:
		try:
			targets[name] = ssh_client.resolve_target(name)
		except ssh_client.SSHPollError as exc:
			_record_failure(name, exc.kind, str(exc), 0)
		except Exception:
			frappe.log_error(title=f"Host Health: cannot resolve SSH target for {name}", message=frappe.get_traceback())

	def _timed(target):
		started = time.monotonic()
		try:
			report, new_key = ssh_client.collect(target, pkey)
			return report, new_key, None, int((time.monotonic() - started) * 1000)
		except ssh_client.SSHPollError as exc:
			return None, None, exc, int((time.monotonic() - started) * 1000)

	with ThreadPoolExecutor(max_workers=parallelism) as pool:
		futures = {pool.submit(_timed, t): name for name, t in targets.items()}
		for future in as_completed(futures):
			name = futures[future]
			try:
				report, new_key, error, duration_ms = future.result()
			except Exception:
				frappe.log_error(title=f"Host Health: poll crashed for {name}", message=frappe.get_traceback())
				continue
			if error:
				_record_failure(name, error.kind, str(error), duration_ms)
			else:
				_store_report(name, targets[name], report, new_key, duration_ms)


def _store_report(name: str, target, report: dict, new_key: str | None, duration_ms: int) -> None:
	try:
		if new_key:
			_pin_key(name, target, new_key)
		status = report.get("status") or {}
		resources = report.get("resources") or {}
		process_status(name, **status)
		process_resources(name, **resources)
		performance = report.get("performance")
		if performance:
			try:
				process_performance(name, **performance)
			except Exception:
				# Never let a performance problem hide the host's status.
				frappe.db.rollback()
				frappe.log_error(title=f"Host Health: performance report from {name} failed", message=frappe.get_traceback())
		collector_errors = report.get("errors") or []
		frappe.db.set_value(
			"Monitored Host",
			name,
			{
				"last_poll_kind": "ok",
				"last_poll_error": "; ".join(collector_errors)[:500] if collector_errors else "",
				"last_poll_duration_ms": duration_ms,
			},
		)
		frappe.db.commit()
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title=f"Host Health: storing report from {name} failed", message=frappe.get_traceback())


def _record_failure(name: str, kind: str, message: str, duration_ms: int) -> None:
	"""A failed poll leaves last_seen alone; the heartbeat watchdog turns a
	run of failures into Host Unreachable once the timeout passes."""
	values = {"last_poll_kind": kind, "last_poll_error": message[:500], "last_poll_duration_ms": duration_ms}
	current = frappe.db.get_value("Monitored Host", name, list(values), as_dict=True)
	# A host that keeps failing the same way (for instance one with no Warpgate
	# target yet) would otherwise be rewritten and committed every cycle.
	if current and all((current.get(k) or "") == (v or "") for k, v in values.items() if k != "last_poll_duration_ms"):
		return
	frappe.db.set_value("Monitored Host", name, values)
	frappe.db.commit()


def _pin_key(name: str, target, key: str) -> None:
	if target.pin_scope == "settings":
		frappe.db.set_single_value("SSH Settings", "server_host_key", key)
		frappe.clear_cache(doctype="SSH Settings")
	else:
		frappe.db.set_value("Monitored Host", name, "pinned_host_key", key)
