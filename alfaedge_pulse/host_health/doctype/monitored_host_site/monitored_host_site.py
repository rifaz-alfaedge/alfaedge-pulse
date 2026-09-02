# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class MonitoredHostSite(Document):
	"""One Frappe site discovered on one bench on a Monitored Host — pure
	inventory, not a monitored condition (no status/streak fields). The
	whole table is replaced wholesale on every ingest push rather than
	upserted row-by-row — see ingest.py's push_status.
	"""
