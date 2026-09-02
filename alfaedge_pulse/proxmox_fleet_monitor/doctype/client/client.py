# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from __future__ import annotations

from frappe.model.document import Document


class Client(Document):
	"""A fleet segment's owning customer/organization — linked from
	Proxmox Server.client. Deliberately minimal (no billing/CRM fields):
	this exists so Monitored Host can resolve an owning client live via
	get_host_client() (monitored_host.py), the same way it already
	resolves role via get_host_role(). Purely plumbing for tenant-scoped
	reporting; not wired into notify_global or any alerting decision.
	"""
