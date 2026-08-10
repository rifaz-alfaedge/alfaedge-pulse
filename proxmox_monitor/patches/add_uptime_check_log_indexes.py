# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Composite index for Uptime Check Log, polled roughly every minute per
site — the same reasoning as ``add_llm_usage_log_indexes``: the alert
window and history queries are always "this site, most recent N/range,"
which needs (site, checked_at) together to stay off a full scan as the
table grows.
"""

import frappe


def execute():
	frappe.db.add_index("Uptime Check Log", ["site", "checked_at"])
