# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Give the new Proxmox Monitor Settings.backup_overdue_hours its default.

Frappe applies a field's ``default`` only to new documents — an existing
Single keeps the new field empty, which reads as 0 and would leave the
per-guest Backup Overdue check switched off on every existing install.
"""

import frappe


def execute():
	# get_single_value() casts a missing Int to 0, indistinguishable from a
	# deliberate 0 ("off") — so check whether a value was ever stored.
	stored = frappe.db.exists(
		"Singles", {"doctype": "Proxmox Monitor Settings", "field": "backup_overdue_hours"}
	)
	if not stored:
		frappe.db.set_single_value("Proxmox Monitor Settings", "backup_overdue_hours", 26)
