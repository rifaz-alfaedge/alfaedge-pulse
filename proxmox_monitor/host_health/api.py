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
from frappe.utils import cint


@frappe.whitelist()
def get_hosted_sites() -> list[dict]:
	frappe.has_permission("Monitored Host", "read", throw=True)
	return frappe.get_all(
		"Monitored Host Site",
		fields=["name", "parent", "bench_name", "site_name", "site_url"],
		limit_page_length=0,
		ignore_permissions=True,
	)


@frappe.whitelist()
def get_failed_job_groups(resolved: int = 0) -> list[dict]:
	"""Root-cause grouping for the Host Health dashboard's failed-jobs view —
	unlike ``get_hosted_sites`` above, ``Frappe Failed Job Log`` isn't a
	child table and has its own real permissions block, so a plain
	``useFrappeGetDocList`` already reads it fine; the reason this needs a
	dedicated endpoint is that "N raw rows collapsed into groups by
	``failure_signature``, with per-group aggregates" isn't a shape the
	generic list API can express. Aggregation happens in Python rather than
	SQL ``GROUP BY`` because we also need one representative row (the most
	recent occurrence's ``exc_info``) per group, not just counts — cheap at
	this doctype's actual scale (dozens to low hundreds of open rows), and
	consistent with how the rest of this app favours ``frappe.get_all`` over
	raw SQL."""
	frappe.has_permission("Frappe Failed Job Log", "read", throw=True)
	rows = frappe.get_all(
		"Frappe Failed Job Log",
		filters={"resolved": cint(resolved)},
		fields=[
			"monitored_host",
			"bench_name",
			"queue_name",
			"job_name",
			"exc_type",
			"failure_signature",
			"exc_info",
			"failed_at",
			"first_seen",
			"last_seen",
		],
		order_by="last_seen desc",
		limit_page_length=0,
		ignore_permissions=True,
	)

	groups: dict[str, dict] = {}
	for row in rows:
		signature = row["failure_signature"]
		group = groups.get(signature)
		if not group:
			# Rows are already ordered last_seen desc, so the first row seen
			# per signature is its most recent occurrence — used as-is for
			# the group's representative exc_type/job_name/sample traceback.
			group = groups[signature] = {
				"failure_signature": signature,
				"exc_type": row["exc_type"],
				"job_name": row["job_name"],
				"sample_exc_info": row["exc_info"],
				"occurrence_count": 0,
				"affected_hosts": set(),
				"first_seen": row["first_seen"],
				"last_seen": row["last_seen"],
			}
		group["occurrence_count"] += 1
		group["affected_hosts"].add(row["monitored_host"])
		if row["first_seen"] and row["first_seen"] < group["first_seen"]:
			group["first_seen"] = row["first_seen"]

	return [
		{
			"failure_signature": g["failure_signature"],
			"exc_type": g["exc_type"],
			"job_name": g["job_name"],
			"sample_exc_info": g["sample_exc_info"],
			"occurrence_count": g["occurrence_count"],
			"affected_host_count": len(g["affected_hosts"]),
			"first_seen": g["first_seen"],
			"last_seen": g["last_seen"],
		}
		for g in sorted(groups.values(), key=lambda g: g["occurrence_count"], reverse=True)
	]
