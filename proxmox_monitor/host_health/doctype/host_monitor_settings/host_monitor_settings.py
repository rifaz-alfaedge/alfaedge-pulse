# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from __future__ import annotations

from frappe.model.document import Document


class HostMonitorSettings(Document):
	"""Single DocType holding fleet-wide Host Health polling/threshold
	config. Read by proxmox_monitor.host_health.ingest and
	proxmox_monitor.tasks.host_health_watchdog every cycle.

	Deliberately has no alerting/channel section of its own — Host Health
	alerts flow through the exact same dispatch_alert/dispatch_recovery
	used everywhere else in this app, which read Email/Telegram/WhatsApp
	config from Proxmox Monitor Settings unconditionally (confirmed by
	reading dispatch.py directly: every settings lookup there is hardcoded
	to that one doctype, not parameterized). A separate copy of those
	fields here would just be inert. Uptime Monitor Settings follows the
	same precedent for the same reason.
	"""
