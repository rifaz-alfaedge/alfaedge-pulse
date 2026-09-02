# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class ProxmoxDatastore(Document):
	"""Usage of one storage pool/datastore on a Proxmox Server or PBS instance.

	One ``Proxmox Server`` can have multiple ``Proxmox Datastore`` rows —
	notably, every PVE host in this fleet has exactly two physical drives:
	the OS + local-backup drive (``storage_role = OS + Local Backup Drive``)
	and the LVM-Thin pool holding guest disks
	(``storage_role = Guest Storage (LVM-Thin)``). Keeping them as separate
	rows (rather than one blended "disk usage" number) is what lets the
	dashboard say precisely which drive is about to fill up.

	Fully auto-managed by the poller; not meant to be edited by hand.
	"""
