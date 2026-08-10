# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document


class BifrostSettings(Document):
	"""Single DocType holding connection config for the Bifrost LLM gateway sync.

	Read by ``proxmox_monitor.tasks.bifrost_sync``. Holds the Bifrost
	dashboard admin username/password, used as HTTP Basic Auth for the
	Management API — self-hosted Bifrost has no separate API key (that's
	an Enterprise-only feature). Never a provider/virtual key, and never
	any of the underlying OpenAI/Anthropic/etc. credentials, which stay
	inside Bifrost itself.
	"""


@frappe.whitelist()
def sync_now() -> dict:
	"""Whitelisted handler for the "Sync Now" button.

	Runs one sync cycle immediately, bypassing the self-throttle guard
	(see ``bifrost_sync.sync_bifrost_logs``) so a user doesn't have to
	wait out ``sync_interval_minutes`` after first configuring the token.
	"""
	frappe.only_for(("System Manager", "Proxmox Monitor Manager"))
	from proxmox_monitor.tasks.bifrost_sync import run_sync_now

	try:
		row_count = run_sync_now()
		return {"ok": True, "message": frappe._("Synced {0} log row(s).").format(row_count)}
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title="Bifrost Monitor: manual sync failed", message=frappe.get_traceback())
		return {"ok": False, "error": str(e)}
