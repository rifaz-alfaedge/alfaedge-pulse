# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from __future__ import annotations

from frappe.model.document import Document


class ResourceDiskSample(Document):
	"""One point-in-time disk/inode reading for one Resource Mount — pure
	append-only history, zero alerting fields, same shape as Uptime Check
	Log. Resource Mount is the alert-reference target; this doctype only
	feeds host_health/api.py's get_disk_forecasts() and drill-down charts.
	"""
