# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Shared orchestration for both weekly AI reports (Proxmox Fleet, Host
Health) — extracted after code review found the two report modules'
generate_weekly_report() functions were ~80% line-for-line identical
(settings gate, period calc, doc construction, error handling, delivery).
Each report module now only supplies the pieces that actually differ: what
data to aggregate and what prompt to build.

Also fixes two bugs the duplication had let slip into both copies:
- The try/except previously only caught BifrostInferenceError, so a DB
  error during aggregation (or an unconfigured Bifrost Settings singleton
  raising AttributeError) crashed the cron with no "Failed" row ever
  recorded. Widened to `except Exception` — any failure anywhere in this
  function is now recorded on the doc, matching this app's established
  per-job isolation convention (see tasks.poller.sync_all_servers).
- The summary was extracted as the first non-blank line of the LLM's
  response, which — when the LLM follows the prompt's own instruction to
  start with a "## Executive Summary" heading — is the heading itself, not
  the actual takeaway sentence beneath it. See _extract_summary below.
"""

from __future__ import annotations

import re
from typing import Callable

import frappe
from frappe.utils import add_to_date, cint, now_datetime

from alfaedge_pulse.ai_insights.bifrost_inference import generate_completion
from alfaedge_pulse.ai_insights.delivery import send_email_report, send_whatsapp_report

_EXECUTIVE_SUMMARY_HEADING = re.compile(r"^#{1,2}\s*executive summary", re.IGNORECASE)


def _extract_summary(report_text: str, fallback: str) -> str:
	"""The first non-blank line AFTER the "## Executive Summary" heading —
	not the heading itself, which `next(line for line in ... if
	line.strip())` used to return verbatim, making every report's summary
	(and the entire WhatsApp message, which sends only this field) read
	as the literal string "Executive Summary". Falls back to the first
	non-heading non-blank line if that heading isn't found (the LLM didn't
	follow the prompt's formatting instructions exactly), and to
	`fallback` if the response has no usable content at all.
	"""
	lines = report_text.splitlines()
	for i, line in enumerate(lines):
		if _EXECUTIVE_SUMMARY_HEADING.match(line.strip()):
			for candidate in lines[i + 1 :]:
				if candidate.strip():
					return candidate.strip()[:250]
			break

	for line in lines:
		stripped = line.strip()
		if stripped and not stripped.startswith("#"):
			return stripped[:250]
	return fallback


def is_report_enabled(enabled_field: str) -> bool:
	"""The master `enabled` switch AND'd with one report's own toggle
	(e.g. "proxmox_report_enabled"). A plain helper rather than baked into
	generate_weekly_report below, since Proxmox Fleet now fans out into
	one run_report call per Proxmox Server role (see proxmox_report.py's
	own generate_weekly_report/_run_now_job, which call this directly
	instead of going through the single-shot wrapper below) — the gate
	check itself doesn't depend on how many reports get generated."""
	settings = frappe.get_cached_doc("AI Insights Settings")
	return bool(cint(settings.enabled) and cint(settings.get(enabled_field)))


def generate_weekly_report(
	report_type: str,
	enabled_field: str,
	build_data: Callable[[object], dict],
	build_prompt: Callable[[dict, object, object], str],
) -> None:
	"""Cron-facing entrypoint for a report with no per-role fan-out (see
	host_health_report.py's own thin `generate_weekly_report()` wrapper,
	which is what hooks.py's scheduled cron entry actually calls) — checks
	the settings gate, then delegates to `run_report`. Proxmox Fleet
	doesn't use this anymore; see its own module for why (per-role
	looping needs to call `run_report` once per role, not once overall).

	Args:
		report_type: "Host Health" — a Weekly AI Report.report_type value.
		enabled_field: The AI Insights Settings fieldname gating this specific report.
		build_data: Called with `period_start` — returns the compact aggregated dict.
		build_prompt: Called with `(data, period_start, period_end)` — returns the LLM prompt.
	"""
	if not is_report_enabled(enabled_field):
		return
	run_report(report_type, build_data, build_prompt)


def run_report(
	report_type: str,
	build_data: Callable[[object], dict],
	build_prompt: Callable[[dict, object, object], str],
	role: str | None = None,
):
	"""The actual generation body, with no settings-gate check — shared by
	the cron wrapper above and by each report module's manual "Run Now"
	whitelisted method (and, for Proxmox Fleet, called once per server
	role — see proxmox_report.py). Returns the created Weekly AI Report
	doc so a manual trigger can report success/failure back to the user
	immediately, the same way proxmox_server.sync_now does.

	Args:
		role: A Proxmox Server.role value ("Production", "Development", ...),
			or None for report types with no role concept (Host Health).
			Tags the created doc and is folded into the email subject/
			WhatsApp summary so multiple same-day reports stay distinguishable.
	"""
	settings = frappe.get_cached_doc("AI Insights Settings")
	period_end = now_datetime()
	period_start = add_to_date(period_end, days=-7)

	doc = frappe.new_doc("Weekly AI Report")
	doc.report_type = report_type
	doc.role = role
	doc.period_start = period_start
	doc.period_end = period_end
	doc.generated_at = now_datetime()
	doc.model_used = settings.model
	doc.status = "Failed"  # overwritten to "Success" below if generation completes

	try:
		data = build_data(period_start)
		# Closes out the aggregation reads' transaction/read-view before the
		# slow, synchronous LLM call below (which can take tens of seconds
		# to minutes) — previously this stayed open for the whole call.
		frappe.db.commit()

		virtual_key = settings.get_password("virtual_key", raise_exception=False)
		if not virtual_key:
			raise ValueError("AI Insights Settings: Bifrost Virtual Key is not configured.")

		bifrost_settings = frappe.get_cached_doc("Bifrost Settings")
		if not bifrost_settings.base_url:
			raise ValueError("Bifrost Settings: Base URL is not configured.")

		report_text = generate_completion(
			base_url=bifrost_settings.base_url,
			virtual_key=virtual_key,
			model=settings.model,
			prompt=build_prompt(data, period_start, period_end),
			max_tokens=cint(settings.max_report_tokens) or 2000,
		)
		doc.status = "Success"
		doc.report_text = report_text
		doc.summary = _extract_summary(report_text, f"Weekly {report_type} report is ready.")
	except Exception as e:
		doc.status = "Failed"
		doc.error = str(e)
		frappe.log_error(title=f"AI Insights: {report_type} weekly report generation failed", message=frappe.get_traceback())

	doc.insert(ignore_permissions=True)
	frappe.db.commit()

	if doc.status == "Success":
		label = f"{report_type} — {role}" if role else report_type
		subject = f"alfaEdge Pulse — Weekly {label} Report ({period_start.date()} – {period_end.date()})"
		doc.sent_email = 1 if send_email_report(settings, subject=subject, report_text=doc.report_text) else 0
		# Prefixed so the WhatsApp notification is distinguishable at a
		# glance when several role-reports land in the same short window
		# (e.g. Production and Development both generated by one Monday
		# cron run, or one "Generate Now" click covering every role).
		whatsapp_summary = f"[{role}] {doc.summary}" if role else doc.summary
		doc.sent_whatsapp = 1 if send_whatsapp_report(settings, summary=whatsapp_summary, report_name=doc.name) else 0
		doc.save(ignore_permissions=True)
		frappe.db.commit()

	return doc
