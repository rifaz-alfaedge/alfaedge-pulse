# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from __future__ import annotations

from frappe.model.document import Document


class WeeklyAIReport(Document):
	"""One weekly AI-generated report — either "Proxmox Fleet" or "Host
	Health" (report_type), never both; the two are analyzed and generated
	completely independently even though they share this one doctype's
	shape. Populated by alfaedge_pulse.ai_insights.proxmox_report /
	.host_health_report's scheduled orchestration functions — never
	created or edited by hand.
	"""
