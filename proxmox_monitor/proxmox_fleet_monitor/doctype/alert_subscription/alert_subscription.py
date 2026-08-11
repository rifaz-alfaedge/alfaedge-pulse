# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document


class AlertSubscription(Document):
	"""One engineer's notification profile: their own Email/WhatsApp
	contact info (in addition to Proxmox Monitor Settings' global
	recipients), plus a table of servers/instances they want to watch,
	and a separate table of Uptime Kuma sites they want to watch.
	Telegram is deliberately not offered here — it's a fleet-wide-only
	channel, configured solely in Proxmox Monitor Settings.

	One document per Frappe User (autoname: field:user). Deliberately
	non-cascading: watching a server only matches alerts fired directly
	against that server, never its guests/datastores — see
	``_get_matching_subscriptions`` in ``alerts/dispatch.py``. Uptime
	sites are matched by site_name there, not by the exact Uptime Site
	docname chosen below — sites are shared across every connected
	instance (see ``uptime_monitor.api``'s module docstring), so which
	instance's copy you pick here doesn't change what you're watching.
	"""

	def before_insert(self):
		if not self.user:
			self.user = frappe.session.user

	def validate(self):
		self._validate_at_least_one_channel()
		self._validate_channel_contacts()
		for row in self.scenarios:
			self._validate_server_is_pve(row)
			self._validate_instance_belongs_to_server(row)

	def _validate_server_is_pve(self, row):
		"""PBS servers don't run VMs/CTs, so they're not valid subscription
		targets — defense against a stale/bypassed client-side grid filter.
		"""
		if not row.server:
			return
		server_type = frappe.db.get_value("Proxmox Server", row.server, "server_type")
		if server_type != "PVE":
			frappe.throw(_("Row {0}: {1} is a PBS server and has no VMs/CTs to subscribe to.").format(row.idx, row.server))

	def _validate_at_least_one_channel(self):
		if not (self.enable_email or self.enable_whatsapp):
			frappe.throw(_("Enable at least one notification channel (Email or WhatsApp)."))

	def _validate_channel_contacts(self):
		if self.enable_email and not (self.email or "").strip():
			frappe.throw(_("Email Address is required when Email is enabled."))
		if self.enable_whatsapp and not (self.whatsapp_number or "").strip():
			frappe.throw(_("WhatsApp Number is required when WhatsApp is enabled."))

	def _validate_instance_belongs_to_server(self, row):
		"""Defense against a stale/bypassed client-side grid query filter —
		the instance field is only ever meant to hold a Guest that actually
		lives on the row's own server. Missing server/instance is left to
		Frappe's own mandatory-field check, which runs after validate().
		"""
		if not row.server or not row.instance:
			return
		owning_server = frappe.db.get_value("Proxmox Guest", row.instance, "server")
		if owning_server != row.server:
			frappe.throw(_("Row {0}: the selected instance does not belong to the selected server.").format(row.idx))


def get_permission_query_conditions(user: str | None = None) -> str:
	"""List-view/report scoping: Managers see every subscription (to audit
	who's subscribed to what); everyone else only sees their own.

	Filters on the `user` field, not `owner` — a subscription is tied to a
	user, not necessarily its creator (e.g. a manager subscribing an
	engineer on their behalf) — something Frappe's built-in if_owner
	permission level can't express, since it's pinned to the owner
	metafield.
	"""
	user = user or frappe.session.user
	if user == "Administrator" or set(frappe.get_roles(user)) & {"System Manager", "Proxmox Monitor Manager"}:
		return ""
	return f"(`tabAlert Subscription`.`user` = {frappe.db.escape(user)})"


def has_permission(doc, user: str | None = None, permission_type: str | None = None) -> bool:
	"""Doc-level counterpart to get_permission_query_conditions — needed
	because the query-condition hook alone only narrows list views/reports;
	a direct by-name open (URL, API, report link) still goes through this.
	"""
	user = user or frappe.session.user
	if user == "Administrator" or set(frappe.get_roles(user)) & {"System Manager", "Proxmox Monitor Manager"}:
		return True
	return doc.user == user
