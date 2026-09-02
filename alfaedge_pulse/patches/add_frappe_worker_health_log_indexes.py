# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Composite index for Frappe Worker Health Log, same reasoning as
add_llm_usage_log_indexes/add_uptime_check_log_indexes: the "latest row
for this host+bench" query (both the dashboard tile and the ingest-time
streak computation — see host_health/ingest.py's _process_bench) needs
(monitored_host, bench_name, timestamp) together to stay off a full scan
as this genuinely-append-only table grows.
"""

import frappe


def execute():
	frappe.db.add_index("Frappe Worker Health Log", ["monitored_host", "bench_name", "timestamp"])
