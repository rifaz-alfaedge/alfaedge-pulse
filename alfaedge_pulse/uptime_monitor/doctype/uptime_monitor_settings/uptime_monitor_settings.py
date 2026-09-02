# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document


class UptimeMonitorSettings(Document):
	"""Single DocType holding fleet-wide Uptime Kuma polling/alerting config.

	Read by alfaedge_pulse.tasks.uptime_kuma_poller every cycle.
	"""

	def on_update(self):
		# Keeps every Uptime Site's own check interval — and Kuma's, via a
		# live edit call — aligned with Heartbeat Interval without a manual
		# step. Deliberately keyed off heartbeat_interval_seconds, not
		# poll_interval_seconds: the two are independent by design (see
		# their field descriptions) — poll_interval_seconds is purely how
		# often *we* read a result, not something Kuma needs to know about.
		# Enqueued (not run inline) since it makes one live Socket.IO call
		# per instance, which would otherwise block this very save. See
		# uptime_monitor.api.sync_check_interval_to_all_sites.
		if self.has_value_changed("heartbeat_interval_seconds"):
			frappe.enqueue(
				"alfaedge_pulse.uptime_monitor.api.sync_check_interval_to_all_sites",
				queue="long",
				job_id="alfaedge_pulse_sync_check_interval",
				deduplicate=True,
			)
