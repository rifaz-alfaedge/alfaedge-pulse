# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class ProxmoxServer(Document):
	"""A single Proxmox VE host or Proxmox Backup Server instance.

	This is the only DocType a user creates by hand. Everything else
	(``Proxmox Guest``, ``Proxmox Datastore``, ``Proxmox Backup Log``) is
	discovered and kept in sync automatically by the background poller in
	``proxmox_monitor.tasks.poller`` once a server is enabled here.
	"""

	def before_save(self):
		"""Fill in a sensible default API port based on the server type.

		Users very rarely change the Proxmox default ports (8006 for PVE,
		8007 for PBS), so we only set a default when the field is empty
		rather than forcing it, in case a host is proxied on a custom port.
		"""
		if not self.port:
			self.port = 8007 if self.server_type == "PBS" else 8006


@frappe.whitelist()
def sync_now(server_name: str) -> dict:
	"""Whitelisted handler for the "Test Connection & Sync Now" button.

	Runs one synchronous poll cycle for a single server (as opposed to the
	background poller, which loops over every enabled server). Used to give
	immediate feedback when a server is first added, rather than making the
	user wait for the next background cycle.

	Args:
		server_name: The ``Proxmox Server`` document name to sync.

	Returns:
		A dict with ``ok`` (bool) and either ``message`` or ``error``, meant
		to be shown directly to the user via ``frappe.msgprint``.
	"""
	from proxmox_monitor.tasks.poller import sync_server

	server = frappe.get_doc("Proxmox Server", server_name)
	try:
		sync_server(server)
		frappe.db.commit()
		return {"ok": True, "message": f"Synced {server_name} successfully."}
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title=f"Proxmox Monitor: manual sync failed for {server_name}", message=frappe.get_traceback())
		return {"ok": False, "error": str(e)}
