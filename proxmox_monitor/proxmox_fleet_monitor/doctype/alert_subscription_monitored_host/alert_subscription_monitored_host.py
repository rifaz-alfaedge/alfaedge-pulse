# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class AlertSubscriptionMonitoredHost(Document):
	"""One row of an Alert Subscription: a Host Health Monitored Host to
	watch. Validated as part of its parent's validate() — see
	alert_subscription.py.
	"""
