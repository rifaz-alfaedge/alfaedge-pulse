# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from __future__ import annotations

from frappe.model.document import Document


class UptimeMonitorSettings(Document):
	"""Single DocType holding fleet-wide Uptime Kuma polling/alerting config.

	Read by proxmox_monitor.tasks.uptime_kuma_poller every cycle.
	"""
