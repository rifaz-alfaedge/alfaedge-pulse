# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Whitelisted read endpoints for the dashboard frontend.

Currently just one: a fair, per-server sampling of ``Proxmox Backup Log``,
which the standard doctype list API can't give us (see
``get_recent_backup_logs``).
"""

from __future__ import annotations

import frappe
from frappe.utils import cint

BACKUP_LOG_FIELDS = [
	"name", "server", "guest", "vmid", "backup_source", "status",
	"backup_time", "size", "storage_target", "notes", "error_message",
]


BACKUP_SOURCES = ["Local Disk", "PBS Remote"]


@frappe.whitelist()
def get_recent_backup_logs(per_bucket_limit: int = 150) -> list[dict]:
	"""The most recent backup log entries, sampled evenly across every
	server *and* every backup source on that server.

	A single "most recent N rows overall" query — the standard doctype list
	API's only option — starves whichever server produces backup records
	more slowly than its siblings: e.g. one host's frequent PBS-remote jobs
	can alone fill an entire shared window, silently pushing a lower-volume
	host's backups out of it completely (observed in practice: one PVE
	host's ~1,500 rows crowded out another's ~800 from a shared top-250
	query). The same crowding happens *within* a single server too, between
	its own Local Disk and PBS Remote jobs, if one runs far more often than
	the other (also observed: a host's infrequent Local Disk backups can be
	entirely absent from its own most-recent-150 window once its
	higher-frequency PBS-remote jobs are mixed in). Querying per
	(server, backup_source) bucket and merging guarantees every combination
	that actually has data gets its own window, regardless of how much log
	volume any other combination produces.
	"""
	per_bucket_limit = cint(per_bucket_limit) or 150
	server_names = frappe.get_list("Proxmox Server", pluck="name")

	rows = []
	for server_name in server_names:
		for backup_source in BACKUP_SOURCES:
			rows.extend(
				frappe.get_list(
					"Proxmox Backup Log",
					filters={"server": server_name, "backup_source": backup_source},
					fields=BACKUP_LOG_FIELDS,
					order_by="backup_time desc",
					limit_page_length=per_bucket_limit,
				)
			)
	return rows
