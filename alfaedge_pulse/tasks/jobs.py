# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Shared helpers for this app's fixed-id background jobs."""

from __future__ import annotations

from datetime import datetime, timezone

import frappe

#: The preferred queue for this app's three always-on poll loops (Proxmox,
#: Uptime Kuma, SSH hosts). Each loop holds a worker for its whole bounded run
#: and is restarted straight away, so they get workers of their own
#: (``workers`` in common_site_config.json plus a supervisor program, one
#: process per loop); left on ``long`` they would pin its workers and starve
#: every real long job (Bifrost sync, site discovery, AI reports,
#: ERPNext/Helpdesk scheduled jobs). Adding a fourth loop means adding one
#: worker process here. Use ``poller_queue()`` to enqueue, not this constant.
POLLER_QUEUE = "pulse"
_FALLBACK_WARNING_KEY = "alfaedge_pulse:poller_queue_fallback_warned"


def poller_queue() -> str:
	"""The queue to start a poll loop on: ``pulse`` when it is configured and
	has a worker, otherwise ``long``.

	A bench that has this code but not the ``pulse`` setup (an upgrade done
	without the supervisor step) would otherwise queue the loops where no
	worker ever picks them up, silently stopping Proxmox, Uptime and host
	monitoring. Falling back keeps them running, at the cost of occupying the
	``long`` workers, and says so in the log (once an hour)."""
	reason = None
	if POLLER_QUEUE not in (frappe.conf.get("workers") or {}):
		reason = f'"{POLLER_QUEUE}" is not listed under "workers" in common_site_config.json'
	else:
		try:
			from frappe.utils.background_jobs import get_queue
			from rq import Worker

			if not Worker.all(queue=get_queue(POLLER_QUEUE)):
				reason = f'no worker is serving the "{POLLER_QUEUE}" queue (is the supervisor program installed and running?)'
		except Exception as exc:
			reason = f'the "{POLLER_QUEUE}" queue could not be checked ({type(exc).__name__})'
	if reason is None:
		return POLLER_QUEUE
	if not frappe.cache().get_value(_FALLBACK_WARNING_KEY):
		frappe.cache().set_value(_FALLBACK_WARNING_KEY, "1", expires_in_sec=3600)
		frappe.logger("alfaedge_pulse").warning(f"Poll loops are using the long queue because {reason}.")
	return "long"


def clear_stale_job(job_id: str, max_age_seconds: int, label: str) -> bool:
	"""Delete a job RQ still reports as queued/started after ``max_age_seconds``.

	Frappe's ``enqueue(..., job_id=..., deduplicate=True)`` refuses to
	enqueue while a job with that id is QUEUED or STARTED. A worker killed
	mid-run (e.g. ``supervisorctl restart`` during a deploy) can leave the
	job recorded as STARTED with nothing behind it, which then blocks every
	future enqueue silently — this stopped production's Proxmox poller for
	days in Oct 2026. Callers pass an age no real run could reach.

	Returns True if a stale job was removed.
	"""
	from frappe.utils.background_jobs import get_job
	from rq.job import JobStatus

	job = get_job(job_id)
	if not job or job.get_status(refresh=False) not in (JobStatus.QUEUED, JobStatus.STARTED):
		return False
	since = job.started_at or job.enqueued_at
	if since is None:
		return False
	if since.tzinfo is None:
		since = since.replace(tzinfo=timezone.utc)  # RQ stores UTC
	age = (datetime.now(timezone.utc) - since).total_seconds()
	if age < max_age_seconds:
		return False  # genuinely pending/running — leave it alone
	frappe.log_error(
		title=f"alfaEdge Pulse: cleared stale {label} job",
		message=f"Job {job.id} was still {job.get_status(refresh=False)} after {int(age)}s "
		f"(worker {job.worker_name or 'unknown'}); deleted so it can be re-enqueued.",
	)
	job.delete()
	return True
