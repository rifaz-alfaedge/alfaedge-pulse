# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from __future__ import annotations

from frappe.model.document import Document


class UptimeSite(Document):
	"""One monitored endpoint on one Uptime Kuma instance.

	Created/edited/deleted through alfaedge_pulse.uptime_monitor.api
	(which also makes the matching Socket.IO call against Kuma itself) —
	not meant to be edited directly here without also updating Kuma, since
	the two would then disagree about what's actually being monitored.

	``current_status``/``is_critical``/``last_checked`` are fully
	auto-managed by alfaedge_pulse.tasks.uptime_kuma_poller.
	"""
