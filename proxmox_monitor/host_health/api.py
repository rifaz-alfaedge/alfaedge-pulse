# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Dashboard-facing reads for Host Health that the generic doctype-list API
can't serve directly.

``Monitored Host Site`` is a child table (``istable: 1``) — Frappe's own
permission engine always resolves a child doctype's permission check
against its *parent* doctype's context (see
``frappe.permissions.has_child_permission``: "This doctype is a child
table, permissions will be checked on parent"), completely ignoring
whatever the child doctype's own ``permissions`` block says. The generic
list API (``frappe.client.get_list``, what the frontend's
``useFrappeGetDocList`` calls under the hood) has no parent to check
against when listing every row fleet-wide, so it always denies non-
Administrator users outright — confirmed live: ``Insufficient Permission
for Monitored Host Site`` for a real System Manager account, even with
explicit read permission rows added directly to the child doctype (which
turned out to be silently unused dead weight — reverted).

This wrapper checks the one permission that actually matters (read on
``Monitored Host``, the real parent) explicitly, then reads every row with
``ignore_permissions=True`` — the same shape as any other cross-cutting
read elsewhere in this app (e.g. ``api.get_recent_backup_logs``).
"""

from __future__ import annotations

import frappe


@frappe.whitelist()
def get_hosted_sites() -> list[dict]:
	frappe.has_permission("Monitored Host", "read", throw=True)
	return frappe.get_all(
		"Monitored Host Site",
		fields=["name", "parent", "bench_name", "site_name", "site_url"],
		limit_page_length=0,
		ignore_permissions=True,
	)
