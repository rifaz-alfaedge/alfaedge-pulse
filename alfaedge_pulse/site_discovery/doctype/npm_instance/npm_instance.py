# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from __future__ import annotations

import ipaddress

import frappe
from frappe.model.document import Document


class NPMInstance(Document):
	"""One Nginx Proxy Manager (usually one per Proxmox host), read during
	the daily site sync — see ``site_discovery/sync.py``. Email/password
	are Password fields (encrypted); the address defaults to the Proxmox
	host's own hostname on port 81 when no URL is set."""

	@property
	def effective_url(self) -> str:
		from alfaedge_pulse.site_discovery.sync import npm_effective_url

		return npm_effective_url(self)

	def validate(self):
		for field in ("url", "email", "password"):
			if self.get(field):
				self.set(field, self.get(field).strip())
		ips = [v.strip() for v in (self.public_ips or "").replace("\n", ",").split(",") if v.strip()]
		for ip in ips:
			try:
				ipaddress.ip_address(ip)
			except ValueError:
				frappe.throw(frappe._("Public IP {0} is not a valid IP address").format(ip))
		self.public_ips = ", ".join(ips)
		if not self.port:
			self.port = 81


@frappe.whitelist()
def test_connection(name: str) -> dict:
	frappe.only_for(("Proxmox Monitor Manager",))
	from alfaedge_pulse.site_discovery.api import run_npm_test

	return run_npm_test(frappe.get_doc("NPM Instance", name))
