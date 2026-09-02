# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Composite index for Resource Metric Log, same reasoning as
add_frappe_worker_health_log_indexes: the "latest row for this host" query
(both the dashboard and the ingest-time streak computation — see
host_health/ingest.py's _upsert_resource_metrics) needs (monitored_host,
collected_at) together to stay off a full scan as this append-only table
grows.
"""

import frappe


def execute():
	frappe.db.add_index("Resource Metric Log", ["monitored_host", "collected_at"])
