# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class ProxmoxAlertLog(Document):
	"""Audit trail of every red-flag alert the poller has raised.

	One row is created per *incident*, not per poll cycle: the poller
	(``alfaedge_pulse.tasks.poller``) only calls
	``alfaedge_pulse.alerts.dispatch.dispatch_alert`` on a state
	transition (e.g. a resource crossing the critical threshold, a backup
	failing, a server going offline), so this table naturally de-duplicates
	repeated notifications for the same ongoing problem.

	Always written regardless of whether any notification channel is
	enabled/configured, so there is a dashboard-visible history of red
	flags even before Email/Telegram/WhatsApp are set up.
	"""
