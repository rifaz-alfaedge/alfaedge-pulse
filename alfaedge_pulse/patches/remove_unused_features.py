# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Drop the tables, columns and settings of features removed in v5.0.0:
Client, the console relay, Alert Subscription, Telegram, and four unused
Proxmox Guest fields.

Migrate's own orphan cleanup deletes a removed DocType's record but keeps
its table, and nothing ever drops a removed field's column — so both are
done here. Every step is a no-op when already done.
"""

import frappe

REMOVED_DOCTYPES = (
	"Alert Subscription Item",
	"Alert Subscription Monitored Host",
	"Alert Subscription Uptime Site",
	"Alert Subscription Log Site",
	"Alert Subscription",
	"Proxmox Console Session Log",
	"Client",
)

REMOVED_COLUMNS = {
	"Proxmox Server": ("client", "console_username", "console_password"),
	"Proxmox Guest": ("network_mode", "public_ip", "access_url", "assigned_engineer"),
}

REMOVED_SINGLE_FIELDS = {
	"Proxmox Monitor Settings": ("enable_telegram_alerts", "telegram_chat_id"),
}


def execute():
	for doctype in REMOVED_DOCTYPES:
		if frappe.db.exists("DocType", doctype):
			frappe.delete_doc("DocType", doctype, force=True, ignore_missing=True)
		frappe.db.sql_ddl(f"drop table if exists `tab{doctype}`")

	for doctype, columns in REMOVED_COLUMNS.items():
		for column in columns:
			if frappe.db.has_column(doctype, column):
				frappe.db.sql_ddl(f"alter table `tab{doctype}` drop column `{column}`")
		# Password fields keep their encrypted value in __Auth, not the table.
		frappe.db.delete("__Auth", {"doctype": doctype, "fieldname": ["in", columns]})

	for doctype, fields in REMOVED_SINGLE_FIELDS.items():
		frappe.db.delete("Singles", {"doctype": doctype, "field": ["in", fields]})
