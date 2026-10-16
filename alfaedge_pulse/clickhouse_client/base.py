# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Minimal read-only HTTP client for ClickHouse's HTTP interface.

Built on plain ``requests``, matching ``bifrost_client.base``'s reasoning:
all this app needs is "run one SELECT, get rows back as JSON", which
ClickHouse's own HTTP interface does natively — a driver dependency
(clickhouse-connect, clickhouse-driver) isn't worth it.

- **Auth goes in headers, never the URL.** ``X-ClickHouse-User`` /
  ``X-ClickHouse-Key`` instead of ``?user=&password=`` so credentials never
  land in a reverse proxy's access log. An optional extra header (e.g.
  ``Authorization: Bearer ...``) supports a proxy in front of ClickHouse
  that wants its own key.
- **Every value from a caller is a server-side query parameter.** SQL uses
  ClickHouse's ``{name:Type}`` placeholders and values go in as
  ``param_name=...`` — ClickHouse substitutes them with proper typing, so
  nothing user-supplied is ever string-interpolated into SQL.
- **``readonly=1`` on every request**, so even a misconfigured user with
  write grants can't be made to write through this client. A user whose
  own profile is already ``readonly=1`` rejects *any* per-request setting
  (Code 164, READONLY — verified against ClickHouse 25.8), so on that
  error the query is retried once with no settings, and that endpoint is
  remembered as settings-free for the rest of the process.
"""

from __future__ import annotations

import requests

#: Base URLs whose user profile rejects per-request settings (see module
#: docstring). Process-local — relearned once after each restart.
_SETTINGS_REJECTED: set[str] = set()


class ClickHouseAPIError(Exception):
	"""Raised for any failure talking to ClickHouse (network, HTTP error,
	query error, or non-JSON body). Callers catch this and surface it on
	ClickHouse Log Settings / to the dashboard rather than letting it raise
	into the scheduler."""


class ClickHouseClient:
	"""Authenticated client for one ClickHouse HTTP endpoint."""

	def __init__(
		self,
		base_url: str,
		username: str | None = None,
		password: str | None = None,
		database: str | None = None,
		verify_ssl: bool = True,
		timeout: int = 30,
		extra_headers: dict | None = None,
	):
		self.base_url = base_url.rstrip("/") + "/"
		self.database = database
		self.verify_ssl = bool(verify_ssl)
		self.timeout = timeout
		self.session = requests.Session()
		if username:
			self.session.headers["X-ClickHouse-User"] = username
		if password:
			self.session.headers["X-ClickHouse-Key"] = password
		if extra_headers:
			self.session.headers.update(extra_headers)

	def query(self, sql: str, params: dict | None = None) -> list[dict]:
		"""Run one SELECT and return its rows as a list of dicts.

		Args:
			sql: SQL text using ``{name:Type}`` placeholders. ``FORMAT JSON``
				is appended here — callers must not add their own FORMAT.
			params: Placeholder values, keyed by placeholder name. Lists are
				sent as ClickHouse array literals for ``Array(String)``
				placeholders.

		Raises:
			ClickHouseAPIError: on any failure.
		"""
		settings = {"readonly": "1", "max_execution_time": str(self.timeout)}
		if self.database:
			settings["database"] = self.database
		values = {f"param_{key}": _encode_param(value) for key, value in (params or {}).items()}
		body = (sql.strip().rstrip(";") + "\nFORMAT JSON").encode()

		if self.base_url in _SETTINGS_REJECTED:
			response = self._post(body, values)
		else:
			response = self._post(body, {**settings, **values})
			if response.status_code >= 400 and "READONLY" in response.text:
				_SETTINGS_REJECTED.add(self.base_url)
				# ``database`` is also a setting as far as readonly=1 is
				# concerned, so fall back to the user's default database —
				# every query here qualifies its table as `db`.`table`.
				response = self._post(body, values)

		if response.status_code >= 400:
			# ClickHouse returns the actual query error as plain text in the
			# body — far more useful than "400 Bad Request" alone.
			raise ClickHouseAPIError(f"ClickHouse HTTP {response.status_code}: {response.text.strip()[:1000]}")

		try:
			return response.json().get("data", [])
		except ValueError as e:
			raise ClickHouseAPIError("ClickHouse returned a non-JSON response") from e

	def _post(self, body: bytes, query_params: dict) -> requests.Response:
		try:
			return self.session.post(
				self.base_url, params=query_params, data=body, timeout=self.timeout + 5, verify=self.verify_ssl
			)
		except requests.RequestException as e:
			raise ClickHouseAPIError(f"Could not reach ClickHouse: {e}") from e


def _encode_param(value) -> str:
	"""Serialize one placeholder value the way ClickHouse's HTTP param
	parser expects: arrays as ``['a','b']`` with quotes/backslashes escaped,
	everything else as its plain string form."""
	if isinstance(value, (list, tuple)):
		escaped = ("'" + str(v).replace("\\", "\\\\").replace("'", "\\'") + "'" for v in value)
		return "[" + ",".join(escaped) + "]"
	return str(value)
