# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Minimal HTTP client for Bifrost's Management API.

Built on plain ``requests``, matching ``proxmox_client.base``'s reasoning:
the surface we need is a handful of GET endpoints under ``/api/logs``, so a
dedicated Bifrost SDK dependency isn't worth it. Unlike Proxmox's `/api2/json`
envelope, Bifrost's responses are the payload directly — no unwrapping needed.

Auth: self-hosted (non-Enterprise) Bifrost has no separate "Management API
key" concept — confirmed directly from a live self-hosted instance's own
docs, which state plainly to "use Basic auth with your admin credentials"
(username:password, base64-encoded). Scoped API keys are an Enterprise-only
upsell feature, not available on the open-source edition this app targets.
"""

from __future__ import annotations

import requests


class BifrostAPIError(Exception):
	"""Raised for any failure talking to Bifrost's Management API.

	Callers (the sync job) catch this and record it on Bifrost Settings
	rather than letting it raise into the scheduler.
	"""


class BifrostClient:
	"""Authenticated HTTP client for one Bifrost instance's Management API."""

	# A page of /api/logs can be a genuinely large response (each row
	# optionally carries full raw_request/raw_response text) — 30s proved
	# too tight against a real self-hosted instance in practice (observed
	# read timeouts), especially once the date-range bug this app also
	# fixed meant early requests were scanning far more than intended.
	default_timeout = 60

	def __init__(self, base_url: str, username: str, password: str, verify_ssl: bool = True):
		self.base_url = base_url.rstrip("/")
		self.verify_ssl = bool(verify_ssl)
		self.session = requests.Session()
		self.session.auth = (username, password)

	def get(self, path: str, params: dict | None = None) -> dict:
		"""GET a Bifrost Management API path and return its JSON body.

		Args:
			path: API path, e.g. ``/api/logs``.
			params: Optional query string parameters.

		Raises:
			BifrostAPIError: on any network failure, non-2xx response, or
				non-JSON payload.
		"""
		url = f"{self.base_url}{path}"
		try:
			response = self.session.get(url, params=params, timeout=self.default_timeout, verify=self.verify_ssl)
			response.raise_for_status()
		except requests.RequestException as e:
			raise BifrostAPIError(f"{url} failed: {e}") from e

		try:
			return response.json()
		except ValueError as e:
			raise BifrostAPIError(f"{url} returned non-JSON response") from e
