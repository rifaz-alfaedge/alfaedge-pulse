# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Fan-out alert dispatch for Email / Telegram / WhatsApp.

Called exclusively by ``proxmox_monitor.tasks.poller`` whenever it detects
a state *transition* (not on every poll cycle) — a resource crossing the
critical threshold, a backup failing, or a server going offline. This
module owns none of the actual notification credentials:

- Email goes through ``frappe.sendmail``, which uses whatever Email
  Account is already configured in core Frappe.
- WhatsApp is sent by creating a document in the ``frappe_whatsapp`` app's
  own ``WhatsApp Message`` DocType, if that app is installed — its Meta/
  WhatsApp Cloud API credentials never pass through this app.
- Telegram is sent through a dedicated Telegram integration app (e.g.
  ``frappe_telegram``), if installed — its bot token never passes through
  this app either.

Every alert is written to ``Proxmox Alert Log`` regardless of whether any
channel is enabled/configured, so there's always a dashboard-visible
history of red flags even before notifications are set up.
"""

from __future__ import annotations

import frappe


def dispatch_alert(alert_type: str, reference_doctype: str, reference_name: str, message: str) -> None:
	"""Record and send one alert for a newly-observed problem.

	Args:
		alert_type: One of "Critical Resource", "Backup Failure", "Server Offline".
		reference_doctype: The DocType the alert is about (e.g. "Proxmox Guest").
		reference_name: The specific document name (e.g. "alpha-104").
		message: Human-readable alert text, sent verbatim to every enabled channel.
	"""
	settings = frappe.get_cached_doc("Proxmox Monitor Settings")
	channels_sent = []

	if settings.enable_email_alerts and settings.email_recipients:
		if _send_email(settings, alert_type, message):
			channels_sent.append("Email")

	if settings.enable_telegram_alerts and settings.telegram_chat_id:
		if _send_telegram(settings, message):
			channels_sent.append("Telegram")

	if settings.enable_whatsapp_alerts and settings.whatsapp_recipients:
		if _send_whatsapp(settings, message):
			channels_sent.append("WhatsApp")

	frappe.get_doc(
		{
			"doctype": "Proxmox Alert Log",
			"alert_type": alert_type,
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"message": message,
			"channels_sent": ", ".join(channels_sent),
			"sent_at": frappe.utils.now_datetime(),
			"resolved": 0,
		}
	).insert(ignore_permissions=True)


def has_open_alert(reference_doctype: str, reference_name: str, alert_type: str) -> bool:
	"""True if an unresolved alert of this type already exists for this document.

	Used to detect a gap the plain transition check can't see: a condition
	that is *currently* critical but has no tracked open incident — e.g. the
	Alert Log was cleared out from under it, or alerting was only just
	configured while something was already critical. In both cases the
	False->True edge that would normally trigger ``dispatch_alert`` already
	happened (or never will happen again on its own), so without this check
	the condition would stay silently critical forever.
	"""
	return bool(
		frappe.db.exists(
			"Proxmox Alert Log",
			{
				"reference_doctype": reference_doctype,
				"reference_name": reference_name,
				"alert_type": alert_type,
				"resolved": 0,
			},
		)
	)


def resolve_alert(reference_doctype: str, reference_name: str, alert_type: str) -> None:
	"""Mark still-open alerts of one specific type for a reference document as resolved.

	Called by the poller once it observes the underlying condition has
	cleared — e.g. usage drops back below the critical threshold, a
	subsequent backup for the same guest succeeds, or a server comes back
	online. Scoped to a single ``alert_type`` because a guest can have
	multiple *independent* open incidents at once (e.g. both a critical
	disk reading and a failed backup) — resolving one must not silently
	clear the other. Does not send a "resolved" notification; it only
	updates the audit trail so the dashboard stops showing it as open.
	"""
	open_alerts = frappe.get_all(
		"Proxmox Alert Log",
		filters={
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"alert_type": alert_type,
			"resolved": 0,
		},
		pluck="name",
	)
	for name in open_alerts:
		frappe.db.set_value("Proxmox Alert Log", name, "resolved", 1)


def _send_email(settings, alert_type: str, message: str) -> bool:
	"""Send via core Frappe email (site's configured Email Account)."""
	try:
		recipients = [r.strip() for r in settings.email_recipients.split(",") if r.strip()]
		if not recipients:
			return False
		# Every message from poller.py starts with "<server>: ..." or
		# "<server> → <guest>: ..." — reusing that same location prefix in
		# the subject line means an inbox list is useful on its own, without
		# opening the email. Only "Resource Warning" is ever a Warning-tier
		# alert; everything else (Critical Resource, Backup Failure, Server
		# Offline) is urgent enough to bucket as Critical here.
		location = message.split(":", 1)[0]
		severity = "Warning" if alert_type == "Resource Warning" else "Critical"
		frappe.sendmail(
			recipients=recipients,
			subject=f"Infra {severity} Alert - {location}",
			message=message,
			now=True,
		)
		return True
	except Exception:
		frappe.log_error(title="Proxmox Monitor: email alert failed", message=frappe.get_traceback())
		return False


def _send_telegram(settings, message: str) -> bool:
	"""Send via an installed Telegram integration app, if any.

	We deliberately don't call the Telegram Bot API directly here — the
	bot token would then have to live inside this app's config, which
	contradicts the "don't hold notification credentials" requirement.
	Instead this defers to whatever Telegram app is installed (e.g.
	``frappe_telegram``), calling its send function with only a chat id
	and a message body.
	"""
	if "frappe_telegram" not in frappe.get_installed_apps():
		return False
	try:
		# leam-tech/frappe_telegram exposes a simple send_message(chat_id, message)
		# helper. If a different Telegram app is installed instead, this dotted
		# path is the one thing to adjust — everything else in this module is
		# app-agnostic.
		send_message = frappe.get_attr("frappe_telegram.utils.send_message")
		send_message(chat_id=settings.telegram_chat_id, message=message)
		return True
	except Exception:
		frappe.log_error(title="Proxmox Monitor: telegram alert failed", message=frappe.get_traceback())
		return False


def _send_whatsapp(settings, message: str) -> bool:
	"""Send via the frappe_whatsapp app, if installed.

	Sends by inserting a document in frappe_whatsapp's own "WhatsApp
	Message" DocType (its documented integration pattern) — this app never
	touches the underlying WhatsApp Cloud API credentials.
	"""
	if "frappe_whatsapp" not in frappe.get_installed_apps():
		return False
	try:
		numbers = [n.strip() for n in settings.whatsapp_recipients.split(",") if n.strip()]
		for number in numbers:
			frappe.get_doc(
				{
					"doctype": "WhatsApp Message",
					"to": number,
					"message": message,
					"message_type": "Manual",
				}
			).insert(ignore_permissions=True)
		return bool(numbers)
	except Exception:
		frappe.log_error(title="Proxmox Monitor: whatsapp alert failed", message=frappe.get_traceback())
		return False
