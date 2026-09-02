# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Composite indexes for LLM Usage Log, which is expected to grow into the
hundreds of thousands of rows over time.

DocType JSON's ``search_index`` only creates single-column indexes — every
dashboard query here is a range-on-timestamp plus a group/filter on one of
source/provider/model/status, so the composite pairing is what actually
keeps those queries off a full table scan. Run once; ``add_index`` is a
no-op if the index already exists (e.g. re-running after a partial
migrate).
"""

import frappe


def execute():
	for column in ("source", "provider", "model", "status"):
		frappe.db.add_index("LLM Usage Log", [column, "request_timestamp"])
