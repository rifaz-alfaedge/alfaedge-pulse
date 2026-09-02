# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Fan-out alert dispatch for Email / Telegram / WhatsApp.

Called exclusively by ``alfaedge_pulse.tasks.poller`` whenever it detects
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

import json

import frappe

#: Host Health's own alert types (service/worker/job/scheduler issues on a
#: guest) — not urgent enough to interrupt for in real time. Routed into
#: the three scheduled digests instead (see alerts/digest.py); every other
#: alert type (Uptime, resource usage, backups, server-offline) is
#: unaffected and keeps sending immediately. Gated here rather than at
#: each of the three call sites (ingest.py, host_health_watchdog.py) since
#: all of them funnel through dispatch_alert/dispatch_recovery regardless
#: of which one raised the alert.
DIGEST_ONLY_ALERT_TYPES = {
	"Service Down",
	"Worker Degraded",
	"Failed Job Threshold",
	"Long Running Job",
	"Scheduler Stalled",
	"Host Unreachable",
}


def dispatch_alert(
	alert_type: str, reference_doctype: str, reference_name: str, message: str, notify_global: bool = True
) -> None:
	"""Record and send one alert for a newly-observed problem.

	Args:
		alert_type: One of "Critical Resource", "Backup Failure", "Server Offline".
		reference_doctype: The DocType the alert is about (e.g. "Proxmox Guest").
		reference_name: The specific document name (e.g. "alpha-104").
		message: Human-readable alert text, sent verbatim to every enabled channel.
		notify_global: Whether Proxmox Monitor Settings' global recipient
			lists get notified — False for guest-level alerts on
			non-Production servers (see poller.py's _upsert_guest).
			Individual Alert Subscriptions are notified either way.

	Alert types in DIGEST_ONLY_ALERT_TYPES skip every channel below —
	they're still recorded in Proxmox Alert Log exactly as before (dashboard
	visibility and has_open_alert/resolve_alert bookkeeping are unaffected),
	just picked up by the next scheduled digest instead of sent immediately.
	"""
	channels_sent = []

	if alert_type not in DIGEST_ONLY_ALERT_TYPES:
		settings = frappe.get_cached_doc("Proxmox Monitor Settings")
		subscriptions = _get_matching_subscriptions(reference_doctype, reference_name)

		email_recipients = _merge_contacts(
			bool(settings.enable_email_alerts),
			settings.email_recipients,
			(s.email for s in subscriptions if s.enable_email),
			include_global=notify_global,
		)
		if email_recipients and _send_email(email_recipients, alert_type, message):
			channels_sent.append("Email")

		# Telegram is fleet-wide-only — Alert Subscription doesn't offer it, so
		# there's no per-subscription contact to merge in here, unlike Email/WhatsApp.
		telegram_ids = _merge_contacts(
			bool(settings.enable_telegram_alerts),
			settings.telegram_chat_id,
			[],
			include_global=notify_global,
		)
		if telegram_ids and any([_send_telegram(cid, message) for cid in telegram_ids]):
			channels_sent.append("Telegram")

		whatsapp_numbers = _merge_contacts(
			bool(settings.enable_whatsapp_alerts),
			settings.whatsapp_recipients,
			(s.whatsapp_number for s in subscriptions if s.enable_whatsapp),
			include_global=notify_global,
		)
		template_name = _resolve_whatsapp_template(settings.whatsapp_template) if settings.whatsapp_template else None
		if whatsapp_numbers and template_name and _send_whatsapp(whatsapp_numbers, template_name, message):
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


def dispatch_recovery(
	alert_type: str, reference_doctype: str, reference_name: str, message: str, notify_global: bool = True
) -> None:
	"""Send a "back to normal" notification once a previously critical
	condition has cleared, and log it as its own Proxmox Alert Log row.

	Only ever called for critical-severity alert_types with a meaningful
	recovery ("Critical Resource", "Server Offline") — see poller.py's
	callers. Logged as a new row (resolved=1 from creation) rather than
	mutating the original incident's row — that row is independently
	flipped to resolved by resolve_alert(); this is a separate, later
	event ("we told people it's fixed"), distinguishable in the Alert Log
	by its message text and later timestamp. No schema change needed.

	``notify_global`` mirrors dispatch_alert's — see there.

	Alert types in DIGEST_ONLY_ALERT_TYPES skip every channel below, same
	as dispatch_alert — still recorded in Proxmox Alert Log as a resolved
	row, just not sent immediately.
	"""
	channels_sent = []

	if alert_type not in DIGEST_ONLY_ALERT_TYPES:
		settings = frappe.get_cached_doc("Proxmox Monitor Settings")
		subscriptions = _get_matching_subscriptions(reference_doctype, reference_name)

		email_recipients = _merge_contacts(
			bool(settings.enable_email_alerts),
			settings.email_recipients,
			(s.email for s in subscriptions if s.enable_email),
			include_global=notify_global,
		)
		if email_recipients and _send_email(email_recipients, alert_type, message, severity="Resolved"):
			channels_sent.append("Email")

		# Telegram is fleet-wide-only — Alert Subscription doesn't offer it, so
		# there's no per-subscription contact to merge in here, unlike Email/WhatsApp.
		telegram_ids = _merge_contacts(
			bool(settings.enable_telegram_alerts),
			settings.telegram_chat_id,
			[],
			include_global=notify_global,
		)
		if telegram_ids and any([_send_telegram(cid, message) for cid in telegram_ids]):
			channels_sent.append("Telegram")

		whatsapp_numbers = _merge_contacts(
			bool(settings.enable_whatsapp_alerts),
			settings.whatsapp_recipients,
			(s.whatsapp_number for s in subscriptions if s.enable_whatsapp),
			include_global=notify_global,
		)
		template_name = (
			_resolve_whatsapp_template(settings.whatsapp_recovery_template) if settings.whatsapp_recovery_template else None
		)
		if whatsapp_numbers and template_name and _send_whatsapp(whatsapp_numbers, template_name, message):
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
			"resolved": 1,
		}
	).insert(ignore_permissions=True)


def _get_matching_subscriptions(reference_doctype: str, reference_name: str) -> list:
	"""Alert Subscription (parent, contact info) rows to additionally notify
	for this alert/recovery, found via their Alert Subscription Item
	(Proxmox Guest), Alert Subscription Uptime Site (Uptime Site), or
	Alert Subscription Monitored Host (Monitored Host / Service Status Log)
	rows.

	Per-instance subscriptions only ever target a Proxmox Guest (VM/CT) —
	watching a server itself, or a datastore, is handled purely by the
	global recipients in Proxmox Monitor Settings, not individual
	subscriptions. Uptime Site alerts are matched on their site_name, not
	the literal reference_name docname — a "Site Down" alert always fires
	against one deterministic sibling doc (see
	``uptime_kuma_poller._reconcile_site_criticality``), but a subscriber
	may have picked a *different* instance's copy of the same site when
	subscribing, and both should match.
	"""
	if reference_doctype == "Proxmox Guest":
		rows = frappe.get_all("Alert Subscription Item", filters={"instance": reference_name}, fields=["parent"])
	elif reference_doctype == "Uptime Site":
		site_name = frappe.db.get_value("Uptime Site", reference_name, "site_name")
		if not site_name:
			return []
		sibling_names = frappe.get_all("Uptime Site", filters={"site_name": site_name}, pluck="name")
		rows = frappe.get_all(
			"Alert Subscription Uptime Site", filters={"site": ["in", sibling_names]}, fields=["parent"]
		)
	elif reference_doctype == "Monitored Host":
		rows = frappe.get_all(
			"Alert Subscription Monitored Host", filters={"monitored_host": reference_name}, fields=["parent"]
		)
	elif reference_doctype == "Service Status Log":
		# Service Down references the log row itself (upserted in place,
		# stable docname — see host_health/ingest.py), not the host, so a
		# subscriber who watches the *host* needs one hop through the log
		# row's own monitored_host field to still match its per-service
		# alerts.
		monitored_host = frappe.db.get_value("Service Status Log", reference_name, "monitored_host")
		if not monitored_host:
			return []
		rows = frappe.get_all(
			"Alert Subscription Monitored Host", filters={"monitored_host": monitored_host}, fields=["parent"]
		)
	else:
		return []

	if not rows:
		return []
	return frappe.get_all(
		"Alert Subscription",
		filters={"name": ["in", {r.parent for r in rows}]},
		fields=["enable_email", "email", "enable_whatsapp", "whatsapp_number"],
	)


def _merge_contacts(
	enabled: bool, global_value: str | None, subscription_values, include_global: bool = True
) -> list[str]:
	"""Merge a channel's global recipient(s) (Proxmox Monitor Settings) with
	each matching Alert Subscription's own contact for that channel.

	[] outright if the channel's master "Enable ... Alerts" checkbox is
	off — that's a fleet-wide kill switch a subscription can't override.
	``include_global`` is a separate, narrower gate: False excludes just
	the global recipient list for this particular alert (e.g. a
	non-Production guest), while subscription values still flow through —
	this is what lets an individual subscriber override the role-based
	gate without touching the fleet-wide switch. When both are satisfied,
	the global field is allowed to be empty (subscriptions alone still get
	notified), which is what makes subscriptions additive rather than
	requiring the global list to be populated first. Deduped
	case/whitespace-insensitively so a subscriber who's also in the global
	list is never double-notified.
	"""
	if not enabled:
		return []
	seen: set[str] = set()
	merged: list[str] = []
	candidates = ([v.strip() for v in (global_value or "").split(",")] if include_global else []) + [
		(v or "").strip() for v in subscription_values
	]
	for value in candidates:
		if not value:
			continue
		key = value.lower()
		if key in seen:
			continue
		seen.add(key)
		merged.append(value)
	return merged


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


def _send_email(recipients: list[str], alert_type: str, message: str, severity: str | None = None) -> bool:
	"""Send via core Frappe email (site's configured Email Account) to an explicit recipient list."""
	try:
		if not recipients:
			return False
		# Every message from poller.py starts with "<server>: ..." or
		# "<server> → <guest>: ..." — reusing that same location prefix in
		# the subject line means an inbox list is useful on its own, without
		# opening the email. Only "Resource Warning" is ever a Warning-tier
		# alert; everything else (Critical Resource, Backup Failure, Server
		# Offline) is urgent enough to bucket as Critical here, unless the
		# caller passes an explicit severity (e.g. "Resolved" for recovery
		# notifications).
		location = message.split(":", 1)[0]
		severity = severity or (
			"Warning" if alert_type == "Resource Warning" else "Test" if alert_type == "Test Alert" else "Critical"
		)
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


def _send_telegram(chat_id: str, message: str) -> bool:
	"""Send one message via an installed Telegram integration app, if any, to one chat id.

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
		send_message(chat_id=chat_id, message=message)
		return True
	except Exception:
		frappe.log_error(title="Proxmox Monitor: telegram alert failed", message=frappe.get_traceback())
		return False


def _resolve_whatsapp_template(template_setting: str) -> str | None:
	"""Resolve the configured template setting to a WhatsApp Templates document name.

	frappe_whatsapp autonames WhatsApp Templates as
	"{template_name}-{language_code}" (e.g. "alert_notification-en_US"),
	which isn't obvious from the Settings UI — accept either that exact
	document name or the plain template_name someone would naturally type.
	"""
	if frappe.db.exists("WhatsApp Templates", template_setting):
		return template_setting
	return frappe.db.get_value("WhatsApp Templates", {"template_name": template_setting}, "name")


def _send_whatsapp(numbers: list[str], template_name: str, message: str) -> bool:
	"""Send via the frappe_whatsapp app, if installed, to an explicit number list.

	Sends by inserting a document in frappe_whatsapp's own "WhatsApp
	Message" DocType (its documented integration pattern) — this app never
	touches the underlying WhatsApp Cloud API credentials.

	Sent via a Meta-approved template (message_type "Template") rather than
	free text: Meta requires business-initiated messages outside an open
	customer conversation window to use a pre-approved template, and flags
	free-text sends like this as non-compliant "marketing" traffic. The
	template name is pre-resolved by the caller via _resolve_whatsapp_template.
	"""
	if "frappe_whatsapp" not in frappe.get_installed_apps():
		return False
	try:
		for number in numbers:
			frappe.get_doc(
				{
					"doctype": "WhatsApp Message",
					"to": number,
					# frappe_whatsapp's before_insert hook only calls its
					# outgoing-send logic when type == "Outgoing" — without it,
					# insert() succeeds silently with no API call ever made.
					# content_type is a mandatory field there too, independent
					# of message_type.
					"type": "Outgoing",
					"content_type": "text",
					"message_type": "Template",
					"template": template_name,
					# Only read by send_template() if the target template has
					# sample_values configured — harmless to pass otherwise.
					"body_param": json.dumps({"1": message}),
					"message": message,
				}
			).insert(ignore_permissions=True)
		return bool(numbers)
	except Exception:
		frappe.log_error(title="Proxmox Monitor: whatsapp alert failed", message=frappe.get_traceback())
		return False


@frappe.whitelist()
def send_test_alert() -> dict:
	"""Send a one-off test message through every enabled, fully-configured channel.

	Lets an admin verify SMTP/Telegram/WhatsApp config from the Settings
	form without waiting for a real Warning/Critical condition to occur.
	"""
	frappe.only_for(("System Manager", "Proxmox Monitor Manager"))
	settings = frappe.get_cached_doc("Proxmox Monitor Settings")
	message = "alfaEdge Pulse Test: this is a test alert to verify your notification channels are configured correctly."
	results = {}

	if settings.enable_email_alerts and settings.email_recipients:
		recipients = [r.strip() for r in settings.email_recipients.split(",") if r.strip()]
		results["Email"] = _send_email(recipients, "Test Alert", message)
	if settings.enable_telegram_alerts and settings.telegram_chat_id:
		results["Telegram"] = _send_telegram(settings.telegram_chat_id, message)
	if settings.enable_whatsapp_alerts and settings.whatsapp_recipients and settings.whatsapp_template:
		numbers = [n.strip() for n in settings.whatsapp_recipients.split(",") if n.strip()]
		template_name = _resolve_whatsapp_template(settings.whatsapp_template)
		results["WhatsApp"] = _send_whatsapp(numbers, template_name, message) if template_name else False

	return results


@frappe.whitelist()
def send_subscription_test_alert(name: str) -> dict:
	"""Send a one-off test message using exactly one Alert Subscription's
	own enabled channels/contacts.

	Lets a subscriber verify their own setup without needing access to the
	global Settings test button (which requires Manager access).
	"""
	subscription = frappe.get_doc("Alert Subscription", name)
	subscription.check_permission("read")
	settings = frappe.get_cached_doc("Proxmox Monitor Settings")
	message = "alfaEdge Pulse Test: this is a test alert to verify your subscription is configured correctly."
	results = {}

	if subscription.enable_email:
		results["Email"] = _send_email([subscription.email], "Test Alert", message)
	if subscription.enable_whatsapp:
		template_name = _resolve_whatsapp_template(settings.whatsapp_template) if settings.whatsapp_template else None
		results["WhatsApp"] = _send_whatsapp([subscription.whatsapp_number], template_name, message) if template_name else False

	return results
