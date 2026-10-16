# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class RecurringError(Document):
	"""Local alert state for one recurring error signature (same error,
	same site) — the logs themselves stay in ClickHouse. Upserted only by
	``tasks/log_monitor.py``; never created by hand."""
