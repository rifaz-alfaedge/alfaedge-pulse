# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""failure_signature is the root-cause grouping key api.get_failed_job_groups
filters/aggregates by (see host_health/ingest.py) — same reasoning as this
app's other add_*_indexes patches, keeping that lookup off a full scan as
the table grows.
"""

import frappe


def execute():
	frappe.db.add_index("Frappe Failed Job Log", ["failure_signature"])
