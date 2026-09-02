# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document


class ResourceMount(Document):
	"""One mount point's current disk/inode status on one Monitored Host —
	upserted in place by ingest.py's push_resource_metrics (keyed on
	monitored_host+mount_point), not appended to as history. Resource Disk
	Sample is the append-only history/forecast feed; this doctype is the
	stable alert-reference target, same role Proxmox Datastore plays for
	storage pools. validate() below is a cheap defensive backstop only —
	the ingest handler's own exists-then-update lookup is the primary
	mechanism, same pattern as Service Status Log.
	"""

	def validate(self):
		duplicate = frappe.db.exists(
			"Resource Mount",
			{"monitored_host": self.monitored_host, "mount_point": self.mount_point, "name": ["!=", self.name]},
		)
		if duplicate:
			frappe.throw(
				_("A Resource Mount for {0} on this host already exists ({1}).").format(self.mount_point, duplicate)
			)
