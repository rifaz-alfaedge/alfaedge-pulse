# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class ProxmoxMonitorSettings(Document):
	"""Single DocType holding global configuration for the poller and alerting.

	Read by ``alfaedge_pulse.tasks.poller`` (poll interval, critical
	threshold) and ``alfaedge_pulse.alerts.dispatch`` (which channels are
	enabled and who to notify). Deliberately holds no third-party API
	credentials — Email uses Frappe's own Email Account, Telegram/WhatsApp
	defer entirely to their own dedicated integration apps.
	"""
