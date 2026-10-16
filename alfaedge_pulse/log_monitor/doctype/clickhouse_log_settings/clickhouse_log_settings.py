# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import cint

from alfaedge_pulse.clickhouse_client.base import ClickHouseAPIError
from alfaedge_pulse.log_monitor.connection import (
	LogMonitorNotConfigured,
	build_base_url,
	get_client,
	validate_identifier,
)

MANAGER_ROLES = ("System Manager", "Proxmox Monitor Manager")


class ClickHouseLogSettings(Document):
	"""Connection, column mapping and alert thresholds for the Log Monitor
	module (Frappe Error Logs shipped into ClickHouse by each production
	site's server script). See ``log_monitor/connection.py``."""

	@property
	def effective_url(self) -> str:
		if not self.host:
			return ""
		return build_base_url(self.protocol, self.host, self.port, self.url_path)

	def validate(self):
		if self.enabled and not (self.host or "").strip():
			frappe.throw(frappe._("Host is required when Log Monitor is enabled"))
		for field in ("database_name", "table_name", "ts_column", "site_column", "content_column"):
			validate_identifier(self.get(field), self.meta.get_label(field))
		for field in ("title_column", "id_column", "log_type_column"):
			validate_identifier(self.get(field), self.meta.get_label(field), required=False)
		if cint(self.occurrence_threshold) < 1:
			self.occurrence_threshold = 1
		if cint(self.window_minutes) < 1:
			self.window_minutes = 1
		if cint(self.check_interval_minutes) < 1:
			self.check_interval_minutes = 1


def run_connection_test(overrides: dict | None = None) -> dict:
	"""Connect, read the server version, and count rows in the configured
	table. ``overrides`` (unsaved dashboard values) take precedence over
	the saved connection/database/table/timestamp-column settings."""
	overrides = overrides or {}
	try:
		settings = frappe.get_cached_doc("ClickHouse Log Settings")
		db = overrides.get("database_name") or settings.database_name
		table = overrides.get("table_name") or settings.table_name
		ts_column = overrides.get("ts_column") or settings.ts_column
		validate_identifier(db, "Database")
		validate_identifier(table, "Table")
		validate_identifier(ts_column, "Timestamp Column")
		client = get_client(overrides, require_enabled=False)
		version = client.query("SELECT version() AS version")[0]["version"]
		stats = client.query(f"SELECT count() AS c, toString(max(`{ts_column}`)) AS latest FROM `{db}`.`{table}`")[0]
	except (ClickHouseAPIError, LogMonitorNotConfigured, frappe.ValidationError) as e:
		return {"ok": False, "error": str(e)}
	return {
		"ok": True,
		"message": frappe._("Connected to ClickHouse {0} — {1} rows in {2}.{3}, latest {4}").format(
			version, stats.get("c"), db, table, stats.get("latest") or "—"
		),
	}


@frappe.whitelist()
def test_connection() -> dict:
	"""Desk "Test Connection" button — tests the saved settings."""
	frappe.only_for(MANAGER_ROLES)
	return run_connection_test()
