# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Retention purge for Resource & Capacity Monitoring's two append-only
history tables — same batched-delete shape as
``uptime_kuma_poller.purge_old_check_logs``, which exists for the exact
same reason: Uptime Check Log already hit 1.4M+ rows, and LLM Usage Log
hit an 8.7GB bloat incident from unbounded raw-data retention before that.
Resource Metric Log/Resource Disk Sample are both push-fed on a much
slower cadence than either of those, but the same unbounded-growth
pattern applies, so the same fix is applied up front rather than waiting
to hit the same wall a third time.
"""

from __future__ import annotations

import frappe
from frappe.utils import add_to_date, cint, now_datetime

# Same batch size and reasoning as purge_old_check_logs — deleted in
# slices so a large purge never holds a long-running lock over a table
# push_resource_metrics is concurrently inserting into.
_PURGE_BATCH_SIZE = 5000

DEFAULT_RETENTION_DAYS = 30


def purge_old_resource_logs() -> None:
	"""Deletes Resource Metric Log and Resource Disk Sample rows older
	than Resource Monitor Settings' Retention (days). Wired to Frappe's
	daily scheduler event (see hooks.py), alongside
	purge_old_check_logs."""
	retention_days = cint(
		frappe.db.get_single_value("Resource Monitor Settings", "retention_days")
	) or DEFAULT_RETENTION_DAYS
	cutoff = add_to_date(now_datetime(), days=-retention_days)

	for doctype in ("Resource Disk Sample", "Resource Metric Log"):
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
			frappe.logger().info(f"Resource Monitor: purged {total_deleted} {doctype} row(s) older than {retention_days} days")
