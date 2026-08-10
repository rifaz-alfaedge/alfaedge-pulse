# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from __future__ import annotations

from frappe.model.document import Document


class LLMUsageLog(Document):
	"""One row per LLM API request, pulled from a source system (Bifrost
	today; a future direct-provider tracker would add more Source values
	without changing this schema).

	Fully auto-managed by ``proxmox_monitor.tasks.bifrost_sync`` — don't
	create or edit rows by hand, they'll be overwritten on the next sync.
	``autoname`` is ``format:{source}-{external_id}``, which is also the
	idempotent-upsert key: re-syncing the same source row always resolves
	to the same document.
	"""
