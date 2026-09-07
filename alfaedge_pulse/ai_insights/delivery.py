# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Shared email/WhatsApp delivery for both weekly AI reports.

Email has no generic long-form report sender to reuse — alerts/dispatch.py
and alerts/digest.py's own `_send_email` helpers both bake an alert-type-
specific subject line into the call, not suitable for a report email with
its own subject — so this is a plain `frappe.sendmail` call instead.

WhatsApp reuses alerts.dispatch's proven template-send mechanics as-is
(`_send_whatsapp`/`_resolve_whatsapp_template`) — the same cross-module
import alerts/digest.py already does for its own WhatsApp digest message,
so this isn't a new pattern in this codebase.
"""

from __future__ import annotations

import frappe

from alfaedge_pulse.alerts.dispatch import _resolve_whatsapp_template, _send_whatsapp


def send_email_report(settings, subject: str, report_text: str) -> bool:
	"""Sends the full report text verbatim as the email body."""
	recipients = [r.strip() for r in (settings.email_recipients or "").split(",") if r.strip()]
	if not recipients:
		return False
	try:
		frappe.sendmail(recipients=recipients, subject=subject, message=report_text, now=True)
		return True
	except Exception:
		frappe.log_error(title="AI Insights: report email failed", message=frappe.get_traceback())
		return False


def send_whatsapp_report(settings, summary: str, report_name: str | None) -> bool:
	"""Sends only the one-line summary plus (if configured) a link to the
	full report — Meta's one-body-variable template constraint (see
	dispatch._send_whatsapp) means the itemized detail can never go here,
	same "count-only, not itemized" split as the existing health digest's
	WhatsApp message.
	"""
	numbers = [n.strip() for n in (settings.whatsapp_recipients or "").split(",") if n.strip()]
	if not numbers or not settings.whatsapp_report_template:
		return False
	template_name = _resolve_whatsapp_template(settings.whatsapp_report_template)
	if not template_name:
		return False

	message = summary
	if report_name and settings.dashboard_report_url:
		message = f"{summary}\nView: {settings.dashboard_report_url.rstrip('/')}/app/weekly-ai-report/{report_name}"
	return _send_whatsapp(numbers, template_name, message)
