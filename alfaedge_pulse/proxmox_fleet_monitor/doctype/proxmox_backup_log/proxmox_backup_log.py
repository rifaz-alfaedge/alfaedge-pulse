# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class ProxmoxBackupLog(Document):
	"""One backup run for one guest, from either backup path.

	``backup_source = Local Disk`` entries come from parsing each PVE
	node's vzdump task log (the only reliable source of pass/fail for the
	daily local backups to Drive 1). ``backup_source = PBS Remote`` entries
	come from the Proxmox Backup Server snapshot listing for the incremental
	backups shipped to the Linode-backed datastore.

	A ``status = Failed`` row is what triggers an immediate alert dispatch
	(see ``alfaedge_pulse.alerts.dispatch``) — even a single failed VM/CT
	backup matters, so there is no batching/aggregation here.
	"""
