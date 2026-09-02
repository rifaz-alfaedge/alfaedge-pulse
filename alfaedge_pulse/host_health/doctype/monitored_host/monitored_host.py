# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document

MANAGE_ROLES = ("System Manager", "Proxmox Monitor Manager")


def agent_user_email() -> str:
	"""The one shared, low-privilege User every Monitored Host authenticates
	as. Site-derived to avoid collisions on a multi-site bench. Created (if
	missing) in ``alfaedge_pulse.install.after_install`` — this function is
	just the naming convention, reused from there and from ``before_insert``
	below so the two never drift apart.

	The site name is embedded in the local part, not the domain: many real
	site names (e.g. ``alfaedge``) have no dot and fail
	``frappe.utils.validate_email_address``'s domain pattern outright if
	used as-is after an ``@``. A fixed, always-valid pseudo-domain sidesteps
	that regardless of how any given bench's sites happen to be named.
	"""
	return f"host-health-agent+{frappe.scrub(frappe.local.site)}@proxmox-monitor.local"


class MonitoredHost(Document):
	"""One guest being watched for OS-service and Frappe-bench health, fed
	by a push agent (see ``host_health/ingest.py``) rather than polled.

	``api_key``/``api_secret``/``user`` are not ordinary fields — their
	names are hardcoded literals inside Frappe core's own
	``validate_api_key_secret`` (frappe/auth.py), invoked via
	``Frappe-Authorization-Source: Monitored Host``. Every Monitored Host
	shares the same ``user`` (see ``agent_user_email``); what actually
	identifies *which* host is pushing is ``api_key``, re-derived by the
	ingest endpoint itself from the Authorization header (core doesn't
	expose which doctype record it resolved during auth).
	"""

	def before_insert(self):
		if not self.user:
			self.user = agent_user_email()


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


def get_host_client(monitored_host: str) -> str | None:
	"""Resolves a Monitored Host's owning Client live, mirroring
	get_host_role() above — Client lives only on Proxmox Server, never
	cached on the guest or here. Not currently used to gate any alerting
	decision (that stays keyed on role); this exists for tenant-scoped
	reporting."""
	proxmox_guest = frappe.db.get_value("Monitored Host", monitored_host, "proxmox_guest")
	if not proxmox_guest:
		return None
	server = frappe.db.get_value("Proxmox Guest", proxmox_guest, "server")
	if not server:
		return None
	return frappe.db.get_value("Proxmox Server", server, "client")


@frappe.whitelist()
def regenerate_agent_key(monitored_host: str) -> dict:
	"""Invalidates the previous key/secret immediately. Returned once, for
	a client-side dialog — never stored in plaintext anywhere after this
	call returns, and never re-displayed (same handling as any other
	Frappe API secret). Module-level, not a whitelisted Document method —
	matches this app's existing button-handler convention (see
	uptime_monitor_settings.py's sync_check_interval_now, bifrost_settings.py's
	sync_now)."""
	frappe.only_for(MANAGE_ROLES)
	doc = frappe.get_doc("Monitored Host", monitored_host)
	doc.check_permission("write")
	api_key = frappe.generate_hash(length=15)
	api_secret = frappe.generate_hash(length=15)
	doc.api_key = api_key
	doc.api_secret = api_secret
	doc.agent_key_generated_on = frappe.utils.now_datetime()
	doc.save()
	return {"api_key": api_key, "api_secret": api_secret}
