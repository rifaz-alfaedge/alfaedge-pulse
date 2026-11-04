# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document


class UptimeKumaInstance(Document):
	"""One monitored Uptime Kuma deployment (of potentially several, at
	different locations). Credentials here are used two ways — see
	alfaedge_pulse.uptime_kuma_client:

	- Socket.IO login (username/password) for admin-initiated monitor
	  management (add/edit/delete/pause/resume) — an unofficial but
	  currently-working Kuma API.
	- HTTP Basic Auth (username/password) or an API Key, for the official,
	  stable Prometheus /metrics endpoint polled every minute for status.
	"""

	def on_update(self):
		# The poll loop ends when no instance is enabled (so it does not hold a
		# worker for nothing) and is normally restarted by a check that runs on
		# the scheduler's tick. Start it as soon as an instance is saved
		# enabled, so heartbeats appear straight away and do not depend on that
		# tick (or on the scheduler running at all).
		if self.enabled:
			frappe.db.after_commit.add(_start_uptime_poller)


def _start_uptime_poller() -> None:
	from alfaedge_pulse.tasks.uptime_kuma_poller import ensure_uptime_poller_running

	try:
		ensure_uptime_poller_running()
	except Exception:
		# Never fail a save because the loop could not be queued; the
		# scheduler's own check is still there as the fallback.
		frappe.log_error(title="Uptime Monitor: could not start the poll loop after an instance was saved", message=frappe.get_traceback())
