# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class InstanceSite(Document):
	"""One site URL and the VM/CT it lives on — written only by the daily
	site sync (``site_discovery/sync.py``), never edited by hand."""
