# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""One-time setup run when the app is first installed on a site."""

import frappe

DEFAULT_EXPECTED_SERVICES = [
	{"service_name": "SSH", "unit_name": "ssh.service"},
	{"service_name": "nginx", "unit_name": "nginx.service"},
	{"service_name": "MariaDB", "unit_name": "mariadb.service"},
	{"service_name": "Redis", "unit_name": "redis-server.service"},
]


def after_install() -> None:
	"""Start the background poll loops right after install, and seed
	Host Health's own one-time setup.

	The various roles (Proxmox Monitor Manager / Viewer) and Settings
	singles are already created automatically by Frappe's DocType sync
	during install — nothing to seed there beyond getting the pollers
	running so the dashboard isn't empty-looking the moment the first
	Proxmox Server is added.
	"""
	from alfaedge_pulse.tasks.poller import ensure_poller_running

	ensure_poller_running()
	from alfaedge_pulse.db_indexes import ensure_indexes

	ensure_indexes()
	_seed_default_expected_services()
	frappe.db.commit()


def _seed_default_expected_services() -> None:
	"""Baseline service list so Host Monitor Settings isn't empty on first
	open — matching whatever's actually in use on this bench's own host is
	a reasonable Ubuntu-oriented starting point, not a guarantee: verify
	the exact unit names (ssh.service vs sshd.service, mariadb.service vs
	mysql.service) against real target hosts before relying on them.
	Idempotent — only seeds if the table is genuinely still empty, so a
	re-run (or a site that's already customized this) is left alone."""
	settings = frappe.get_cached_doc("Host Monitor Settings")
	if settings.default_expected_services:
		return
	doc = frappe.get_doc("Host Monitor Settings")
	for row in DEFAULT_EXPECTED_SERVICES:
		doc.append("default_expected_services", row)
	doc.save(ignore_permissions=True)
