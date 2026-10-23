# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Minimal client for Nginx Proxy Manager's admin API.

NPM has no API keys: a client logs in with the admin email/password at
``POST /api/tokens`` and gets a short-lived bearer token. All this app
needs afterwards is the proxy-host list (``GET /api/nginx/proxy-hosts``),
which carries each host's ``domain_names`` and where it forwards to.
"""

from __future__ import annotations

import requests


class NPMAPIError(Exception):
	"""Any failure talking to one NPM instance."""


class NPMClient:
	timeout = 20

	def __init__(self, base_url: str, email: str, password: str, verify_ssl: bool = True):
		self.base_url = base_url.rstrip("/")
		self.email = email
		self.password = password
		self.verify_ssl = bool(verify_ssl)
		self.session = requests.Session()

	def _request(self, method: str, path: str, **kwargs):
		url = f"{self.base_url}{path}"
		try:
			response = self.session.request(method, url, timeout=self.timeout, verify=self.verify_ssl, **kwargs)
		except requests.RequestException as e:
			raise NPMAPIError(f"Could not reach NPM at {self.base_url}: {e}") from e
		if response.status_code in (400, 401, 403) and path == "/api/tokens":
			raise NPMAPIError("NPM rejected the login — check the email and password")
		if response.status_code >= 400:
			raise NPMAPIError(f"NPM {path} returned HTTP {response.status_code}: {response.text.strip()[:300]}")
		try:
			return response.json()
		except ValueError as e:
			raise NPMAPIError(f"NPM {path} returned a non-JSON response — is this the NPM admin port?") from e

	def login(self) -> None:
		if not self.email or not self.password:
			raise NPMAPIError("NPM email/password not set")
		data = self._request("POST", "/api/tokens", json={"identity": self.email, "secret": self.password})
		token = (data or {}).get("token")
		if not token:
			raise NPMAPIError("NPM login returned no token")
		self.session.headers["Authorization"] = f"Bearer {token}"

	def list_proxy_hosts(self) -> list[dict]:
		"""Enabled proxy hosts as ``{domains, forward_host, forward_port, forward_scheme}``."""
		if "Authorization" not in self.session.headers:
			self.login()
		hosts = self._request("GET", "/api/nginx/proxy-hosts")
		if not isinstance(hosts, list):
			raise NPMAPIError("Unexpected proxy-host list from NPM")
		out = []
		for h in hosts:
			if not h.get("enabled", True):
				continue
			out.append(
				{
					"domains": [d.strip().lower() for d in (h.get("domain_names") or []) if d and d.strip()],
					"forward_host": (h.get("forward_host") or "").strip(),
					"forward_port": h.get("forward_port"),
					"forward_scheme": h.get("forward_scheme") or "http",
				}
			)
		return out
