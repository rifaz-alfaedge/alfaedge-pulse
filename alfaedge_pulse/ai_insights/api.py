# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Dashboard-facing reads for the AI Insights tab. Weekly AI Report is a
plain doctype the frontend could list directly via useFrappeGetDocList —
this module only exists for the one thing that needs backend logic: a
plain-text download of one report, mirroring host_health/api.py's
get_failed_job_log_text (same "download as file" button pattern on the
frontend).
"""

from __future__ import annotations

import frappe


@frappe.whitelist()
def get_report_text(name: str) -> str:
	"""Plain-text export of one Weekly AI Report, for the dashboard's
	"Download" button."""
	frappe.has_permission("Weekly AI Report", "read", throw=True)
	doc = frappe.get_doc("Weekly AI Report", name)
	# Trimmed to the minute, not full microsecond precision — same "human-
	# readable, not a raw DB value" convention as get_failed_job_log_text's
	# own timestamps.
	fmt = lambda dt: frappe.utils.get_datetime(dt).strftime("%Y-%m-%d %H:%M")
	header = (
		f"alfaEdge Pulse — Weekly {doc.report_type} Report\n"
		f"Period: {fmt(doc.period_start)} – {fmt(doc.period_end)}\n"
		f"Generated: {fmt(doc.generated_at)}\n\n"
	)
	return header + (doc.report_text or "")
