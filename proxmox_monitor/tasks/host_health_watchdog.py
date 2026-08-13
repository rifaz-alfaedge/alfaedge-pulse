# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Periodic staleness checks for Host Health — the two conditions that
have to keep getting re-evaluated even when the agent stops pushing
entirely (Host Unreachable), or keeps pushing while the *underlying*
Frappe scheduler itself has stalled (Scheduler Stalled) — neither of
which a push-triggered check alone could ever catch on its own once the
pushes themselves are the thing that's stopped, or aren't what's broken.

Wired to the same 1-minute cron as the Proxmox/Uptime watchdogs (see
hooks.py), but deliberately does *not* use their bounded-loop/heartbeat-
cache pattern — that machinery exists solely to get sub-minute polling
out of Frappe's once-a-minute scheduler floor. A staleness check that
only needs to run once a minute (timeouts here are already expected to
be several times the agent's own push interval) is fully satisfied by
the cron floor itself — this is a plain self-throttled-nothing function,
closer in shape to uptime_kuma_poller.purge_old_check_logs than to
poller.run_poll_loop.
"""

from __future__ import annotations

import frappe
from frappe.utils import add_to_date, cint, get_datetime, now_datetime

from proxmox_monitor.tasks.poller import _handle_transition

DEFAULT_HEARTBEAT_TIMEOUT_SECONDS = 90
DEFAULT_SCHEDULER_HEARTBEAT_TIMEOUT_MINUTES = 15


def check_host_heartbeats() -> None:
	settings = frappe.get_cached_doc("Host Monitor Settings")
	heartbeat_timeout = cint(settings.heartbeat_timeout_seconds) or DEFAULT_HEARTBEAT_TIMEOUT_SECONDS
	scheduler_timeout_minutes = (
		cint(settings.scheduler_heartbeat_timeout_minutes) or DEFAULT_SCHEDULER_HEARTBEAT_TIMEOUT_MINUTES
	)
	heartbeat_cutoff = add_to_date(now_datetime(), seconds=-heartbeat_timeout)
	scheduler_cutoff = add_to_date(now_datetime(), minutes=-scheduler_timeout_minutes)

	hosts = frappe.get_all(
		"Monitored Host",
		filters={"enabled": 1},
		fields=["name", "hostname", "last_seen", "is_online", "scheduler_last_run", "scheduler_overdue"],
	)
	for host in hosts:
		try:
			_check_one_host(host, heartbeat_cutoff, scheduler_cutoff, heartbeat_timeout, scheduler_timeout_minutes)
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(
				title=f"Host Health: heartbeat check failed for {host.name}", message=frappe.get_traceback()
			)


def _check_one_host(host, heartbeat_cutoff, scheduler_cutoff, heartbeat_timeout: int, scheduler_timeout_minutes: int) -> None:
	# Availability-type conditions, not resource-type — both alerts below
	# pass notify_global=True unconditionally, same as Server Offline/
	# Backup Failure in poller.py's sync_server, regardless of role. No
	# role lookup needed here (unlike ingest.py's Service Down/Worker
	# Degraded/Failed Job Threshold, which are role-gated).
	was_online = bool(cint(host.is_online))
	is_online = bool(host.last_seen) and get_datetime(host.last_seen) >= heartbeat_cutoff
	if is_online != was_online:
		frappe.db.set_value("Monitored Host", host.name, "is_online", 1 if is_online else 0)
	_handle_transition(
		not was_online,
		not is_online,
		"Monitored Host",
		host.name,
		"Host Unreachable",
		f"{host.hostname or host.name}: agent has not reported in over {heartbeat_timeout}s.",
		recovery_message=f"{host.hostname or host.name}: agent reporting again.",
		notify_global=True,
	)

	was_overdue = bool(cint(host.scheduler_overdue))
	is_overdue = bool(host.scheduler_last_run) and get_datetime(host.scheduler_last_run) < scheduler_cutoff
	# A host that has never reported a scheduler run at all isn't
	# "overdue" — it may simply not run a bench with a Frappe scheduler
	# (e.g. it only reports OS services). Only flag once we've actually
	# seen a run go stale.
	if host.scheduler_last_run and is_overdue != was_overdue:
		frappe.db.set_value("Monitored Host", host.name, "scheduler_overdue", 1 if is_overdue else 0)
	if host.scheduler_last_run:
		_handle_transition(
			was_overdue,
			is_overdue,
			"Monitored Host",
			host.name,
			"Scheduler Stalled",
			f"{host.hostname or host.name}: Frappe scheduler has not run in over {scheduler_timeout_minutes} minute(s).",
			recovery_message=f"{host.hostname or host.name}: scheduler resumed.",
			notify_global=True,
		)
