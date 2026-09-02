# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class ExpectedService(Document):
	"""One systemd unit a Monitored Host is expected to have running.
	Rows live either on Host Monitor Settings (fleet-wide default list) or
	directly on a Monitored Host (per-host override) — see both doctypes'
	own docstrings.
	"""
