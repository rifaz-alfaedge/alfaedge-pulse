# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Read-only Cloudflare DNS client (API token: Zone:Read + DNS:Read).

Lists every zone the token can see (or a configured subset) and their
A/AAAA/CNAME records. For proxied (orange-cloud) records the API still
returns the real origin in ``content``, which is what site discovery needs.
"""

from __future__ import annotations

import requests

API = "https://api.cloudflare.com/client/v4"
RECORD_TYPES = ("A", "AAAA", "CNAME")
MAX_PAGES = 200


class CloudflareAPIError(Exception):
	"""Any failure talking to Cloudflare."""


class CloudflareClient:
	timeout = 30

	def __init__(self, api_token: str):
		if not api_token:
			raise CloudflareAPIError("Cloudflare API token not set")
		self.session = requests.Session()
		self.session.headers["Authorization"] = f"Bearer {api_token}"

	def _get(self, path: str, params: dict | None = None) -> dict:
		try:
			response = self.session.get(f"{API}{path}", params=params, timeout=self.timeout)
		except requests.RequestException as e:
			raise CloudflareAPIError(f"Could not reach Cloudflare: {e}") from e
		try:
			data = response.json()
		except ValueError as e:
			raise CloudflareAPIError(f"Cloudflare {path} returned HTTP {response.status_code} (non-JSON)") from e
		if response.status_code >= 400 or not data.get("success", False):
			errors = "; ".join(e.get("message", "") for e in data.get("errors") or []) or f"HTTP {response.status_code}"
			raise CloudflareAPIError(f"Cloudflare {path}: {errors}")
		return data

	def _paged(self, path: str, params: dict | None = None, per_page: int = 100) -> list[dict]:
		out, page = [], 1
		while page <= MAX_PAGES:
			data = self._get(path, {**(params or {}), "page": page, "per_page": per_page})
			out.extend(data.get("result") or [])
			info = data.get("result_info") or {}
			if page >= (info.get("total_pages") or 1):
				break
			page += 1
		return out

	def list_zones(self, only: list[str] | None = None) -> list[dict]:
		zones = self._paged("/zones", per_page=50)
		if only:
			wanted = {z.strip().lower() for z in only if z.strip()}
			zones = [z for z in zones if z.get("name", "").lower() in wanted]
		return zones

	def list_records(self, zone_id: str) -> list[dict]:
		"""A/AAAA/CNAME records as ``{name, type, content, proxied}``."""
		records = self._paged(f"/zones/{zone_id}/dns_records", per_page=1000)
		return [
			{
				"name": (r.get("name") or "").lower().rstrip("."),
				"type": r.get("type"),
				"content": (r.get("content") or "").lower().rstrip("."),
				"proxied": bool(r.get("proxied")),
			}
			for r in records
			if r.get("type") in RECORD_TYPES
		]
