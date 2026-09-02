# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Composite index for Resource Disk Sample, same reasoning as
add_frappe_worker_health_log_indexes: host_health/api.py's
get_disk_forecasts() queries "samples for this resource_mount within the
lookback window" on every call, which needs (resource_mount,
collected_at) together to stay off a full scan as this append-only table
grows.
"""

import frappe


def execute():
	frappe.db.add_index("Resource Disk Sample", ["resource_mount", "collected_at"])
