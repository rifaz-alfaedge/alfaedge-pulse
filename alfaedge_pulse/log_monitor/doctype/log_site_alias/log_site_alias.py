# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document

from alfaedge_pulse.log_monitor.connection import normalize_site, normalize_site_label


class LogSiteAlias(Document):
	"""Maps a host as it appears in the ClickHouse logs (e.g. ``lulu``, from
	a site whose host_name was never set to its real domain) onto the site
	it really is. Applied inside every Log Monitor query (see
	``queries.base_query``), so it also regroups history already logged.

	http/https variants of the same host never need an alias — every site
	is already keyed on its bare host."""

	def validate(self):
		self.source_host = normalize_site(self.source_host)
		self.site = normalize_site_label(self.site)
		if not self.source_host:
			frappe.throw(frappe._("Host in Logs is required"))
		if not self.site:
			frappe.throw(frappe._("Show As is required"))
		if self.site == self.source_host:
			frappe.throw(frappe._("Show As must differ from the host itself"))
		if frappe.db.exists("Log Site Alias", {"site": self.source_host, "name": ["!=", self.name]}):
			frappe.throw(
				frappe._("Other hosts are already shown as {0} — it can't itself be aliased").format(self.source_host)
			)
		target_alias = frappe.db.get_value("Log Site Alias", {"source_host": self.site}, "site")
		if target_alias:
			# Point straight at the final site rather than building a chain.
			self.site = target_alias

	def on_update(self):
		frappe.cache().delete_value(ALIAS_CACHE_KEY)

	def on_trash(self):
		frappe.cache().delete_value(ALIAS_CACHE_KEY)


ALIAS_CACHE_KEY = "alfaedge_pulse:log_site_aliases"
