# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Builds a ClickHouse client from ClickHouse Log Settings.

Nothing about the ClickHouse endpoint is hardcoded anywhere in Python —
host, port, protocol, credentials, database, table, and even the column
names are read from the Single doctype on every call, so a change made in
Desk or the dashboard's settings dialog takes effect on the very next
query with no restart.

Credentials (username, password, API key) are ``Password`` fields — Frappe
stores them encrypted in ``__Auth`` with the site's ``encryption_key`` —
and are only ever decrypted here, server-side, to build request headers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import frappe
from frappe.utils import cint
from frappe.utils.password import get_decrypted_password

from alfaedge_pulse.clickhouse_client.base import ClickHouseClient

SETTINGS_DOCTYPE = "ClickHouse Log Settings"
SECRET_FIELDS = ("username", "password", "api_key")

#: ClickHouse identifiers (database/table/column names) can't be passed as
#: query parameters, so they're interpolated — and therefore must be
#: validated to a strict shape first.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class LogMonitorNotConfigured(Exception):
	"""Raised when Log Monitor is disabled or missing its host."""


def validate_identifier(value: str | None, label: str, required: bool = True) -> None:
	if not value:
		if required:
			frappe.throw(frappe._("{0} is required").format(label))
		return
	if not _IDENTIFIER_RE.match(value):
		frappe.throw(
			frappe._("{0} may only contain letters, digits and underscores (got {1!r})").format(label, value)
		)


def build_base_url(protocol: str | None, host: str | None, port: int | None, url_path: str | None) -> str:
	protocol = (protocol or "http").strip().lower()
	host = (host or "").strip()
	# Forgive someone pasting a full URL into Host.
	host = re.sub(r"^[a-z]+://", "", host).rstrip("/")
	port = cint(port)
	default_port = 443 if protocol == "https" else 80
	netloc = host if not port or port == default_port else f"{host}:{port}"
	path = (url_path or "").strip().strip("/")
	return f"{protocol}://{netloc}/" + (f"{path}/" if path else "")


@dataclass
class ColumnMap:
	"""Validated column identifiers, ready to interpolate into SQL."""

	table: str
	ts: str
	site: str
	content: str
	title: str | None
	id: str | None
	log_type: str | None


def normalize_site(value: str | None) -> str:
	"""A site's identity in Log Monitor: its bare lower-cased host, so
	``http://Anson.example.org/`` and ``https://anson.example.org:443``
	are the same site. Must match ``queries.site_host_expr``."""
	value = (value or "").strip().lower()
	value = re.sub(r"^[a-z][a-z0-9+.-]*://", "", value)
	return re.split(r"[/:?#]", value, maxsplit=1)[0]


def normalize_site_label(value: str | None) -> str:
	"""An alias target: a URL/host is normalized like ``normalize_site``;
	a free-text display name (anything with a space) is kept as typed."""
	value = (value or "").strip()
	if " " in value and "://" not in value:
		return value
	return normalize_site(value)


def get_site_aliases() -> dict[str, str]:
	"""{source_host: site} for every Log Site Alias, cached until one changes."""
	from alfaedge_pulse.log_monitor.doctype.log_site_alias.log_site_alias import ALIAS_CACHE_KEY

	aliases = frappe.cache().get_value(ALIAS_CACHE_KEY)
	if aliases is None:
		aliases = {r.source_host: r.site for r in frappe.get_all("Log Site Alias", fields=["source_host", "site"])}
		frappe.cache().set_value(ALIAS_CACHE_KEY, aliases)
	return aliases


def get_settings():
	return frappe.get_cached_doc(SETTINGS_DOCTYPE)


def get_secret(fieldname: str) -> str | None:
	return get_decrypted_password(SETTINGS_DOCTYPE, SETTINGS_DOCTYPE, fieldname, raise_exception=False) or None


def get_column_map(settings=None) -> ColumnMap:
	s = settings or get_settings()
	for field, label, required in (
		("database_name", "Database", True),
		("table_name", "Table", True),
		("ts_column", "Timestamp Column", True),
		("site_column", "Site URL Column", True),
		("content_column", "Content Column", True),
		("title_column", "Title Column", False),
		("id_column", "ID Column", False),
		("log_type_column", "Log Type Column", False),
	):
		validate_identifier(s.get(field), label, required)
	return ColumnMap(
		table=f"`{s.database_name}`.`{s.table_name}`",
		ts=f"`{s.ts_column}`",
		site=f"`{s.site_column}`",
		content=f"`{s.content_column}`",
		title=f"`{s.title_column}`" if s.get("title_column") else None,
		id=f"`{s.id_column}`" if s.get("id_column") else None,
		log_type=f"`{s.log_type_column}`" if s.get("log_type_column") else None,
	)


def get_client(overrides: dict | None = None, require_enabled: bool = True) -> ClickHouseClient:
	"""Client built from saved settings, optionally overlaid with unsaved
	values (the dashboard's "Test" before "Save"). A blank secret in
	``overrides`` means "keep the stored one", matching the write-only
	secret inputs in the settings dialog."""
	s = get_settings()
	values = {k: s.get(k) for k in ("enabled", "protocol", "host", "port", "url_path", "verify_ssl",
		"query_timeout_seconds", "database_name", "api_key_header", "api_key_prefix")}
	secrets = {k: get_secret(k) for k in SECRET_FIELDS}
	for key, value in (overrides or {}).items():
		if key in SECRET_FIELDS:
			if value:
				secrets[key] = value
		elif key in values:
			values[key] = value

	if require_enabled and not cint(values["enabled"]):
		raise LogMonitorNotConfigured("Log Monitor is disabled — enable it in ClickHouse Log Settings.")
	if not (values["host"] or "").strip():
		raise LogMonitorNotConfigured("ClickHouse host is not set — fill it in ClickHouse Log Settings.")
	validate_identifier(values["database_name"], "Database")

	extra_headers = {}
	if secrets["api_key"]:
		header = (values["api_key_header"] or "Authorization").strip()
		extra_headers[header] = f"{values['api_key_prefix'] or ''}{secrets['api_key']}"

	return ClickHouseClient(
		base_url=build_base_url(values["protocol"], values["host"], values["port"], values["url_path"]),
		username=secrets["username"],
		password=secrets["password"],
		database=values["database_name"],
		verify_ssl=values["protocol"] != "https" or cint(values["verify_ssl"]),
		timeout=cint(values["query_timeout_seconds"]) or 30,
		extra_headers=extra_headers,
	)
