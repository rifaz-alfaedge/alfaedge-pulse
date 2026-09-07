# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from __future__ import annotations

from frappe.model.document import Document


class ProxmoxGuestMetricLog(Document):
	"""One point-in-time CPU/RAM/disk/(LXC swap) reading for one Proxmox
	Guest — pure append-only history, no alerting fields. Populated by
	alfaedge_pulse.proxmox_resource_history.collector.log_guest_metrics
	(called from the poller) and .backfill.backfill_server (one-time RRD
	import) — never edited directly.
	"""
