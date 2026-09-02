# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document


class BifrostSettings(Document):
	"""Single DocType holding connection config for the Bifrost LLM gateway sync.

	Read by ``alfaedge_pulse.tasks.bifrost_sync``. Holds the Bifrost
	dashboard admin username/password, used as HTTP Basic Auth for the
	Management API — self-hosted Bifrost has no separate API key (that's
	an Enterprise-only feature). Never a provider/virtual key, and never
	any of the underlying OpenAI/Anthropic/etc. credentials, which stay
	inside Bifrost itself.
	"""


@frappe.whitelist()
def sync_now() -> dict:
	"""Whitelisted handler for the "Sync Now" button.

	Enqueues a sync in the background rather than running it inline —
	bypassing ``sync_interval_minutes``'s throttle so a user doesn't have
	to wait after first configuring credentials, but a first-time 30-day
	backfill can genuinely take minutes, which would otherwise block (and
	likely time out) the web request. See ``bifrost_sync``'s module
	docstring for why this and the cron path share one deduped job.
	"""
	frappe.only_for(("System Manager", "Proxmox Monitor Manager"))
	from alfaedge_pulse.tasks.bifrost_sync import enqueue_sync

	settings = frappe.get_cached_doc("Bifrost Settings")
	if not settings.enabled:
		return {"ok": False, "error": "Bifrost sync is disabled — enable it above first."}

	enqueue_sync()
	return {
		"ok": True,
		"message": frappe._("Sync started in the background — refresh in a moment to see Last Synced / Last Error."),
	}
