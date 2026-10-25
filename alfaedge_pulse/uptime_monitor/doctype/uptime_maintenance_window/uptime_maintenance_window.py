# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, get_datetime

from alfaedge_pulse.uptime_monitor.maintenance import WEEKDAYS


class UptimeMaintenanceWindow(Document):
	"""A scheduled downtime window for one site (by site_name, across every
	Kuma instance). See ``alfaedge_pulse.uptime_monitor.maintenance`` for
	how windows are evaluated and what they suppress."""

	def validate(self):
		self.site_name = (self.site_name or "").strip()
		if not frappe.db.exists("Uptime Site", {"site_name": self.site_name}):
			frappe.throw(_("No Uptime site named {0}").format(self.site_name))

		if self.window_type == "One-time":
			if not (self.start_datetime and self.end_datetime):
				frappe.throw(_("A one-time window needs a start and an end."))
			if get_datetime(self.end_datetime) <= get_datetime(self.start_datetime):
				frappe.throw(_("The end must be after the start."))
			return

		if self.window_type == "Weekly" and not any(cint(self.get(d)) for d in WEEKDAYS):
			frappe.throw(_("Pick at least one day for a weekly window."))
		if self.window_type == "Daily" or not cint(self.all_day):
			if not (self.start_time and self.end_time):
				frappe.throw(_("Set a start and an end time."))
