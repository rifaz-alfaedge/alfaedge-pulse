# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""One-time setup run when the app is first installed on a site."""

import frappe


def after_install() -> None:
	"""Start the background poll loop right after install.

	The two roles (Proxmox Monitor Manager / Viewer) and the
	Proxmox Monitor Settings single are already created automatically by
	Frappe's DocType sync during install — nothing to seed here beyond
	getting the poller running so the dashboard isn't empty-looking the
	moment the first Proxmox Server is added.
	"""
	from proxmox_monitor.tasks.poller import ensure_poller_running

	ensure_poller_running()
	frappe.db.commit()
