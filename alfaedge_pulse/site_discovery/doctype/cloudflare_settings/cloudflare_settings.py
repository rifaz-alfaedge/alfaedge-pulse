# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import cint


class CloudflareSettings(Document):
	"""Cloudflare API token (encrypted) and daily site-sync schedule — see
	``site_discovery/sync.py``."""

	def validate(self):
		if self.api_token:
			self.api_token = self.api_token.strip()
		self.sync_hour = min(max(cint(self.sync_hour), 0), 23)
		self.zones = ", ".join(z.strip().lower() for z in (self.zones or "").split(",") if z.strip())


@frappe.whitelist()
def test_connection() -> dict:
	frappe.only_for(("Proxmox Monitor Manager",))
	from alfaedge_pulse.site_discovery.api import run_cloudflare_test

	return run_cloudflare_test()


@frappe.whitelist()
def sync_now() -> dict:
	frappe.only_for(("Proxmox Monitor Manager",))
	from alfaedge_pulse.site_discovery.sync import enqueue_sync

	enqueue_sync()
	return {"ok": True, "message": frappe._("Site sync started — results appear in a minute or two.")}
