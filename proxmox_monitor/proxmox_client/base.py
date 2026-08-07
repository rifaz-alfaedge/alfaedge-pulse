# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Shared HTTP plumbing for talking to Proxmox VE and Proxmox Backup Server.

Both products expose a very similar `/api2/json/...` REST API authenticated
with a bearer-style API token header, so the PVE and PBS clients
(``proxmox_monitor.proxmox_client.pve`` / ``.pbs``) share this thin wrapper
rather than each re-implementing session/error handling.

Deliberately built on plain ``requests`` rather than the ``proxmoxer``
library: the API surface we need is small (a dozen GET endpoints), and
avoiding the extra dependency keeps this app's dependency footprint
minimal, per the "standalone and lightweight" requirement.
"""

from __future__ import annotations

import urllib3
import requests


class ProxmoxAPIError(Exception):
	"""Raised for any failure talking to a Proxmox VE / PBS API.

	Callers (the poller) catch this per-server so one unreachable host
	doesn't stop the sync of the others.
	"""


class BaseProxmoxClient:
	"""Minimal authenticated HTTP client for a single Proxmox host.

	Not used directly — see ``proxmox_client.pve.PVEClient`` and
	``proxmox_client.pbs.PBSClient``, which only differ in the
	``Authorization`` header format they send.
	"""

	#: Overridden by subclasses. Must contain a single ``{value}`` placeholder
	#: for the fully-assembled "token-id=secret" (PVE) or "token-id:secret"
	#: (PBS) credential string.
	auth_header_template = ""

	#: Short timeout for lightweight status calls.
	default_timeout = 10

	#: QEMU guest-agent calls can hang for several seconds when the agent
	#: is enabled in the VM config but not actually running inside the
	#: guest, so they get their own much shorter timeout — we would rather
	#: skip one VM's live disk figure than stall the whole poll cycle.
	agent_timeout = 4

	def __init__(self, hostname: str, port: int, token_id: str, token_secret: str, verify_ssl: bool = False):
		self.base_url = f"https://{hostname}:{port}/api2/json"
		# requests' `verify` kwarg treats any truthy value that isn't the
		# exact `True` object as a CA-bundle *path* (checked via `is True`,
		# not truthiness) — so passing Frappe's Check-field value straight
		# through (an int 0/1 from cint(), not a real bool) makes `verify=1`
		# get misread as a certificate path and crash deep in urllib3. Cast
		# explicitly so callers can safely pass ints, "0"/"1", etc.
		self.verify_ssl = bool(verify_ssl)
		self.session = requests.Session()
		self.session.headers["Authorization"] = self._auth_header(token_id, token_secret)

		if not verify_ssl:
			# Proxmox hosts almost always run with a self-signed certificate
			# on the private management network, so we silence the noisy
			# per-request warning rather than have it spam the worker logs.
			urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

	def _auth_header(self, token_id: str, token_secret: str) -> str:
		raise NotImplementedError

	def get(self, path: str, params: dict | None = None, timeout: int | None = None) -> dict | list:
		"""GET a `/api2/json` path and return its unwrapped `data` payload.

		Args:
			path: API path without the `/api2/json` prefix, e.g. ``/nodes``.
			params: Optional query string parameters.
			timeout: Overrides ``default_timeout`` — used for guest-agent
				calls, which get a much shorter budget (see ``agent_timeout``).

		Raises:
			ProxmoxAPIError: on any network failure, non-2xx response, or
				unexpected payload shape.
		"""
		url = f"{self.base_url}{path}"
		try:
			response = self.session.get(
				url, params=params, timeout=timeout or self.default_timeout, verify=self.verify_ssl
			)
			response.raise_for_status()
		except requests.RequestException as e:
			raise ProxmoxAPIError(f"{url} failed: {e}") from e

		try:
			payload = response.json()
		except ValueError as e:
			raise ProxmoxAPIError(f"{url} returned non-JSON response") from e

		if "data" not in payload:
			raise ProxmoxAPIError(f"{url} response missing 'data' field")
		return payload["data"]
