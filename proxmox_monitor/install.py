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
	from proxmox_monitor.tasks.poller import ensure_poller_running

	ensure_poller_running()
	_setup_host_health_agent()
	_seed_default_expected_services()
	frappe.db.commit()


def _setup_host_health_agent() -> None:
	"""Creates the one shared, low-privilege identity every Monitored
	Host authenticates as (see monitored_host.py's docstring for why a
	shared identity + per-host api_key/api_secret, rather than a Frappe
	User per host). Idempotent — safe to call more than once."""
	from proxmox_monitor.host_health.doctype.monitored_host.monitored_host import agent_user_email

	if not frappe.db.exists("Role", "Host Health Agent"):
		frappe.get_doc(
			{
				"doctype": "Role",
				"role_name": "Host Health Agent",
				"desk_access": 0,
				# Deliberately zero permission rows anywhere — the ingest
				# endpoint writes via ignore_permissions=True, matching every
				# other poller/upsert in this app. This role exists purely to
				# keep the shared user's blast radius at zero if its
				# credentials ever leaked, not to grant it access.
			}
		).insert(ignore_permissions=True)

	email = agent_user_email()
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": "Host Health Agent",
				# Frappe's own User.set_system_user() recomputes this from the
				# role's desk_access regardless of what's passed here — since
				# Host Health Agent has none, this always lands on "Website
				# User" (no desk access at all), which suits a pure API-only
				# identity even better than "System User" would have.
				"user_type": "System User",
				"send_welcome_email": 0,
				"enabled": 1,
				"roles": [{"role": "Host Health Agent"}],
			}
		).insert(ignore_permissions=True)


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
