# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""One-time backfill for Proxmox Guest.last_successful_backup.

That field is only ever written going forward, at the moment a new
``Proxmox Backup Log`` Success row is created (see
``alfaedge_pulse.tasks.poller``). Without this patch, every guest would
read as having *never* been backed up — and every running one as
backup-overdue — right after upgrading to this field, even for guests
with a perfectly good backup already on record from before the field
existed. This runs once, populating it from whatever Success history is
already there.
"""

import frappe


def execute():
	frappe.db.sql(
		"""
		update `tabProxmox Guest` guest
		join (
			select guest, max(backup_time) as last_backup
			from `tabProxmox Backup Log`
			where status = 'Success' and guest is not null and guest != ''
			group by guest
		) latest on latest.guest = guest.name
		set guest.last_successful_backup = latest.last_backup
		where guest.last_successful_backup is null
		"""
	)
