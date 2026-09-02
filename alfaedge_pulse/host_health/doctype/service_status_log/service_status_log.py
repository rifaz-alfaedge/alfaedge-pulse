# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document


class ServiceStatusLog(Document):
	"""One service's current status on one Monitored Host — upserted in
	place by ingest.py's push_status (keyed on monitored_host+service_name),
	not appended to as history. validate() below is a cheap defensive
	backstop only; the ingest handler's own exists-then-update lookup is
	the primary mechanism, same pattern as LLM Usage Log's _upsert_log.
	"""

	def validate(self):
		duplicate = frappe.db.exists(
			"Service Status Log",
			{"monitored_host": self.monitored_host, "service_name": self.service_name, "name": ["!=", self.name]},
		)
		if duplicate:
			frappe.throw(
				_("A Service Status Log for {0} on this host already exists ({1}).").format(
					self.service_name, duplicate
				)
			)
