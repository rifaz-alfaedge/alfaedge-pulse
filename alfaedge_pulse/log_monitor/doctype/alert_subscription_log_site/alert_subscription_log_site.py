# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class AlertSubscriptionLogSite(Document):
	"""One Log Monitor site an Alert Subscription watches for recurring
	errors (child of Alert Subscription)."""
