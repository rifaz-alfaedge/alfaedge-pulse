# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class ProxmoxGuest(Document):
	"""A single VM (QEMU) or container (LXC) discovered on a Proxmox Server.

	Fully auto-managed: created, updated, and deleted by the background
	poller (``alfaedge_pulse.tasks.poller``) as it observes the real state
	of each connected host. Only ``assigned_engineer``, ``network_mode``,
	``public_ip`` and ``access_url`` are meant to be edited by hand — every
	other field is overwritten on the next sync.
	"""
