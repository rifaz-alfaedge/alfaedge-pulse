# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from __future__ import annotations

from frappe.model.document import Document


class ProxmoxHostMetricLog(Document):
	"""One point-in-time CPU/RAM/swap/disk reading for one Proxmox Server —
	pure append-only history, no alerting fields, same shape as Host
	Health's Resource Metric Log. Populated by
	alfaedge_pulse.proxmox_resource_history.collector.log_host_metrics
	(called from the poller) and .backfill.backfill_server (one-time RRD
	import) — never edited directly.
	"""
