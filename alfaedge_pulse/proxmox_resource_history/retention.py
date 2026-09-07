# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Retention purge for Proxmox Resource History's two append-only tables —
same batched-delete shape as tasks.resource_monitor.purge_old_resource_logs,
for the same reason (see that module's docstring): unbounded history
tables have bitten this app twice already (Uptime Check Log, LLM Usage
Log). At once-a-minute live sampling for one host plus every guest, these
tables would eventually hit the same wall without a purge.
"""

from __future__ import annotations

import frappe
from frappe.utils import add_to_date, cint, now_datetime

# Same batch size and reasoning as purge_old_resource_logs — deleted in
# slices so a large purge never holds a long-running lock over a table
# the live poller is concurrently inserting into.
_PURGE_BATCH_SIZE = 5000

DEFAULT_RETENTION_DAYS = 30


def purge_old_resource_history() -> None:
	"""Deletes Proxmox Host Metric Log and Proxmox Guest Metric Log rows
	older than Proxmox Monitor Settings' Resource History Retention
	(days). Wired to Frappe's daily scheduler event (see hooks.py),
	alongside purge_old_resource_logs."""
	retention_days = cint(
		frappe.db.get_single_value("Proxmox Monitor Settings", "resource_history_retention_days")
	) or DEFAULT_RETENTION_DAYS
	cutoff = add_to_date(now_datetime(), days=-retention_days)

	for doctype in ("Proxmox Host Metric Log", "Proxmox Guest Metric Log"):
		total_deleted = 0
		while True:
			names = frappe.get_all(
				doctype, filters={"collected_at": ["<", cutoff]}, limit_page_length=_PURGE_BATCH_SIZE, pluck="name"
			)
			if not names:
				break
			frappe.db.delete(doctype, {"name": ["in", names]})
			frappe.db.commit()
			total_deleted += len(names)
			if len(names) < _PURGE_BATCH_SIZE:
				break

		if total_deleted:
			frappe.logger().info(
				f"Proxmox Resource History: purged {total_deleted} {doctype} row(s) older than {retention_days} days"
			)
