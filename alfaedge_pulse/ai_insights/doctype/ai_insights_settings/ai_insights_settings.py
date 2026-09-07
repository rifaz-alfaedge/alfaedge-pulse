# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document


class AIInsightsSettings(Document):
	"""Connection/delivery config for the two weekly AI reports (Proxmox
	Fleet, Host Health). Deliberately separate from both Bifrost Settings
	(that's Bifrost's Management API admin credentials, a different auth
	shape) and Proxmox Monitor Settings (that's Proxmox-only alerting) —
	this feature spans both Proxmox and Host Health domains, so it gets
	its own home.
	"""


@frappe.whitelist()
def send_test_report_message() -> dict:
	"""Whitelisted handler for a "Send Test Message" button — verifies the
	WhatsApp template/recipients are configured correctly without waiting
	for a real weekly run. Mirrors alerts.dispatch.send_test_alert's role.
	"""
	frappe.only_for(("System Manager", "Proxmox Monitor Manager"))
	from alfaedge_pulse.ai_insights.delivery import send_whatsapp_report

	settings = frappe.get_cached_doc("AI Insights Settings")
	if not (settings.whatsapp_recipients and settings.whatsapp_report_template):
		return {"ok": False, "error": "WhatsApp Recipients and WhatsApp Report Template Name must both be set."}

	sent = send_whatsapp_report(
		settings, summary="alfaEdge Pulse Test: this is a test of the weekly AI report WhatsApp message.", report_name=None
	)
	return {"ok": sent, "error": None if sent else "Send failed — check WhatsApp Report Template Name and Error Log."}
