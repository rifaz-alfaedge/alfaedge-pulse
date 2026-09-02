# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from __future__ import annotations

from frappe.model.document import Document


class UptimeCheckLog(Document):
	"""One row per Uptime Site per poll (~every minute) — our own recorded
	heartbeat history, independent of whatever history Kuma itself retains.

	Fully auto-managed by alfaedge_pulse.tasks.uptime_kuma_poller. This is
	the table the >50%-of-last-N-checks alert rule reads from — see
	``uptime_kuma_poller._is_site_critical``.
	"""
