# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class ProxmoxServerArchive(Document):
	"""Audit record of a deleted Proxmox Server: who deleted it, when, how
	many records went with it, and a gzipped JSON archive of them — see
	``proxmox_fleet_monitor/server_deletion.py``."""
