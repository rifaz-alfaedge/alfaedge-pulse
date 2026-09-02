# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document


class ResourceMonitorSettings(Document):
	"""Single DocType holding fleet-wide Resource & Capacity Monitoring
	polling/threshold config. Read by
	alfaedge_pulse.host_health.ingest.push_resource_metrics and
	alfaedge_pulse.tasks.resource_monitor.purge_old_resource_logs.

	Deliberately has no alerting/channel section of its own — see Host
	Monitor Settings' own docstring for why (same reasoning applies here
	verbatim: dispatch_alert/dispatch_recovery read Email/Telegram/WhatsApp
	config from Proxmox Monitor Settings unconditionally).
	"""

	def validate(self):
		if self.forecast_lookback_days and self.retention_days and self.forecast_lookback_days > self.retention_days:
			frappe.throw(
				_("Forecast Lookback (days) cannot exceed Retention (days) — the forecast window would outlive its own data.")
			)
