# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""v6.0.0: hosts are polled over SSH, the push agent is gone.

Frappe never drops the columns of removed fields, so this removes the agent
key columns from Monitored Host, deletes the stored agent secrets, the
shared agent user and its role, and seeds SSH Settings' defaults (a new
Single's defaults are not applied by migrate on its own). Polling stays
switched off until someone turns it on.
"""

import frappe

REMOVED_COLUMNS = ("api_key", "api_secret", "user", "agent_key_generated_on")
AGENT_ROLE = "Host Health Agent"
AGENT_USER_PREFIX = "host-health-agent+"

SSH_DEFAULTS = {
	"enabled": 0,
	"connection_mode": "Warpgate",
	"server_port": 2222,
	"poll_interval_seconds": 30,
	"parallelism": 8,
	"connect_timeout": 20,
	"command_timeout": 60,
}


def execute():
	for column in REMOVED_COLUMNS:
		if frappe.db.has_column("Monitored Host", column):
			frappe.db.sql_ddl(f"alter table `tabMonitored Host` drop column `{column}`")

	frappe.db.delete("__Auth", {"doctype": "Monitored Host", "fieldname": "api_secret"})

	for email in frappe.get_all("User", filters={"name": ["like", f"{AGENT_USER_PREFIX}%"]}, pluck="name"):
		frappe.delete_doc("User", email, ignore_permissions=True, force=True)
	if frappe.db.exists("Role", AGENT_ROLE):
		frappe.delete_doc("Role", AGENT_ROLE, ignore_permissions=True, force=True)

	for field, value in SSH_DEFAULTS.items():
		# Int fields read back as 0 when never set, so "unset" means falsy.
		# "enabled" stays as it is: 0 there is a real choice, not a gap.
		if field != "enabled" and not frappe.db.get_single_value("SSH Settings", field):
			frappe.db.set_single_value("SSH Settings", field, value)
