# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document

MANAGE_ROLES = ("System Manager", "Proxmox Monitor Manager")


class MonitoredHost(Document):
	"""One guest being watched for OS-service and Frappe-bench health. Pulse
	polls it over SSH (see ``host_health/ssh_client.py`` and
	``tasks/host_poller.py``); nothing runs on the guest itself."""


def get_host_role(monitored_host: str) -> str | None:
	"""Resolves a Monitored Host's Production/Development/Staging/Backup
	role live, the same way poller.py's _upsert_guest does for a Proxmox
	Guest — role lives only on Proxmox Server, never cached on the guest
	(or here). Used to gate notify_global on Service Down/Worker
	Degraded/Failed Job Threshold, matching how per-guest resource alerts
	already work."""
	proxmox_guest = frappe.db.get_value("Monitored Host", monitored_host, "proxmox_guest")
	if not proxmox_guest:
		return None
	server = frappe.db.get_value("Proxmox Guest", proxmox_guest, "server")
	if not server:
		return None
	return frappe.db.get_value("Proxmox Server", server, "role")
