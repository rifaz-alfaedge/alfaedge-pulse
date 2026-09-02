# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class AlertSubscriptionUptimeSite(Document):
	"""One row of an Alert Subscription: an Uptime Kuma site to watch.
	Validated as part of its parent's validate() — see alert_subscription.py.
	"""
