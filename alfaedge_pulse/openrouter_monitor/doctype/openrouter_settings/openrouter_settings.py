# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document

MANAGE_ROLES = ("System Manager", "Proxmox Monitor Manager")


class OpenRouterSettings(Document):
	"""Single DocType holding the OpenRouter connection and alert settings.

	Read by ``alfaedge_pulse.tasks.openrouter_sync``. Holds one OpenRouter
	*Management* API key (stored encrypted in a Password field, read with
	``get_decrypted_password``); a normal inference key cannot read credits
	or activity. Nothing else about OpenRouter lives here.
	"""


@frappe.whitelist()
def sync_usage_now() -> dict:
	"""The "Sync Usage Now" button: queue the daily-usage sync in the background,
	ignoring the once-a-day schedule, so a first backfill never blocks (or times
	out) the web request. Shares one deduplicated job with the scheduled run."""
	frappe.only_for(MANAGE_ROLES)
	from alfaedge_pulse.tasks.openrouter_sync import enqueue_activity_sync

	problem = _not_ready()
	if problem:
		return {"ok": False, "error": problem}
	enqueue_activity_sync()
	return {"ok": True, "message": frappe._("Usage sync started in the background. Refresh in a moment to see Usage Last Synced / Last Error.")}


@frappe.whitelist()
def check_balance_now() -> dict:
	"""The "Check Balance Now" button: queue one /credits reading."""
	frappe.only_for(MANAGE_ROLES)
	from alfaedge_pulse.tasks.openrouter_sync import enqueue_balance_check

	problem = _not_ready()
	if problem:
		return {"ok": False, "error": problem}
	enqueue_balance_check()
	return {"ok": True, "message": frappe._("Balance check started. Refresh in a moment to see the remaining credit.")}


def _not_ready() -> str | None:
	from frappe.utils.password import get_decrypted_password

	settings = frappe.get_cached_doc("OpenRouter Settings")
	if not settings.enabled:
		return "OpenRouter sync is disabled. Enable it above first."
	if not get_decrypted_password("OpenRouter Settings", "OpenRouter Settings", "management_key", raise_exception=False):
		return "No Management API key is saved yet."
	return None
