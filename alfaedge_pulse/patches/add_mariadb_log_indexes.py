# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Composite indexes for the MariaDB history tables: read as "this host,
newest first" (ingest needs the previous sample to turn counters into rates;
the Diagnose view charts a time range)."""

import frappe


def execute():
	frappe.db.add_index("MariaDB Metric Log", ["monitored_host", "collected_at"])
	frappe.db.add_index("MariaDB Config Snapshot", ["monitored_host", "collected_at"])
