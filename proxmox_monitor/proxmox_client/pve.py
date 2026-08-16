# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Client for the Proxmox VE REST API.

Every host in this fleet (Alpha, Beta, Gamma) is a single-node PVE
install, not a cluster — so ``discover_node_name`` just takes whichever
node the ``/nodes`` endpoint returns first, rather than us hardcoding a
node name that may not match what the admin named the host in Proxmox.
"""

from __future__ import annotations

import requests

from proxmox_monitor.proxmox_client.base import BaseProxmoxClient, ProxmoxAPIError


def login(hostname: str, port: int, username: str, password: str, verify_ssl: bool = False) -> dict:
	"""Real username+password login against ``/access/ticket`` — the same
	call Proxmox's own web UI makes when you log in. Returns
	``{"ticket": ..., "csrf_token": ...}``, both needed by
	``PVEClient.from_ticket`` to talk to the console feature's
	``vncwebsocket`` endpoint, which (unlike every other endpoint this app
	calls) rejects API-token auth outright — see ``base.py``'s
	``from_ticket`` docstring.

	A free function, not a ``PVEClient`` method: every other client in
	this app is constructed already-authenticated (a token is set on
	``self.session`` in ``__init__``), but this call has to happen
	*before* any client exists — there's nothing to authenticate yet.
	"""
	# Same gotcha `base.py`'s _setup already guards against: `requests`
	# treats any truthy value that isn't the exact `True` object as a
	# CA-bundle *path*, so a bare cint() 0/1 int makes `verify=1` crash
	# deep in urllib3 instead of behaving like a bool. Confirmed live.
	verify_ssl = bool(verify_ssl)
	if not verify_ssl:
		import urllib3

		urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
	try:
		response = requests.post(
			f"https://{hostname}:{port}/api2/json/access/ticket",
			data={"username": username, "password": password},
			timeout=10,
			verify=verify_ssl,
		)
		response.raise_for_status()
		data = response.json()["data"]
	except (requests.RequestException, KeyError, ValueError) as e:
		raise ProxmoxAPIError(f"login to {hostname} failed: {e}") from e

	if not data.get("ticket") or not data.get("CSRFPreventionToken"):
		raise ProxmoxAPIError(f"login to {hostname} did not return a ticket — check username/password")
	return {"ticket": data["ticket"], "csrf_token": data["CSRFPreventionToken"]}


class PVEClient(BaseProxmoxClient):
	"""Talks to one Proxmox VE host's `/api2/json` API using a read-only API token."""

	def _auth_header(self, token_id: str, token_secret: str) -> str:
		# PVE token header format: "PVEAPIToken=USER@REALM!TOKENID=SECRET"
		# (note the "=" between token id and secret — PBS uses a colon here
		# instead, which is a common source of confusing 401s if copy-pasted).
		return f"PVEAPIToken={token_id}={token_secret}"

	def discover_node_name(self) -> str:
		"""Return the PVE node name to use for all subsequent calls.

		These hosts are single-node, so we simply take the first (and only)
		entry from ``/nodes`` rather than requiring the admin to enter the
		exact internal node name when adding a ``Proxmox Server``.
		"""
		nodes = self.get("/nodes")
		if not nodes:
			raise ProxmoxAPIError("PVE host returned no nodes")
		return nodes[0]["node"]

	def get_node_status(self, node: str) -> dict:
		"""CPU/RAM/root-disk/uptime for the host itself."""
		return self.get(f"/nodes/{node}/status")

	def list_qemu(self, node: str) -> list[dict]:
		"""Summary list of every QEMU VM on this node (cpu/mem/status/uptime)."""
		return self.get(f"/nodes/{node}/qemu")

	def list_lxc(self, node: str) -> list[dict]:
		"""Summary list of every LXC container on this node.

		Unlike QEMU, the LXC summary includes real disk usage (``disk`` /
		``maxdisk``) natively, since Proxmox can inspect a container's
		filesystem directly without needing an in-guest agent.
		"""
		return self.get(f"/nodes/{node}/lxc")

	def list_storage(self, node: str) -> list[dict]:
		"""All storage pools visible to this node, e.g. the OS/backup drive and the LVM-Thin guest pool."""
		return self.get(f"/nodes/{node}/storage")

	def list_vzdump_tasks(self, node: str, limit: int = 50) -> list[dict]:
		"""Recent local-backup (vzdump) task results for this node.

		This is the only reliable source of pass/fail for the daily local
		backups to Drive 1 — listing files on the backup storage can show
		what *did* get written, but not that a job failed outright, e.g. if
		the guest was skipped or the dump errored before producing a file.
		"""
		return self.get(f"/nodes/{node}/tasks", params={"typefilter": "vzdump", "limit": limit})

	def list_backup_files(self, node: str, storage: str) -> list[dict]:
		"""Backup files on one storage — the only source for each file's Notes
		field (what the Proxmox UI's Backup tab shows in its "Notes" column).
		Not available from the task log or the task list, only here.
		"""
		return self.get(f"/nodes/{node}/storage/{storage}/content", params={"content": "backup"})

	def get_qemu_disk_usage_percent(self, node: str, vmid: int) -> float | None:
		"""Best-effort real disk usage % for a QEMU VM via the QEMU guest agent.

		Returns None if the agent isn't enabled/installed/responding —
		this is a Proxmox limitation (the hypervisor cannot see inside a
		VM's filesystem without the in-guest agent), not something this
		app can work around. Guarded with a short timeout so one
		unresponsive VM doesn't stall the whole poll cycle.
		"""
		try:
			result = self.get(
				f"/nodes/{node}/qemu/{vmid}/agent/get-fsinfo", timeout=self.agent_timeout
			)
		except ProxmoxAPIError:
			return None

		filesystems = result.get("result") if isinstance(result, dict) else result
		if not filesystems:
			return None

		total_bytes = used_bytes = 0
		for fs in filesystems:
			fs_total = fs.get("total-bytes")
			fs_used = fs.get("used-bytes")
			if fs_total:
				total_bytes += fs_total
				used_bytes += fs_used or 0

		if not total_bytes:
			return None
		return round((used_bytes / total_bytes) * 100, 1)

	def get_qemu_agent_ip(self, node: str, vmid: int) -> str | None:
		"""Best-effort primary IPv4 address for a QEMU VM via the guest agent."""
		try:
			result = self.get(
				f"/nodes/{node}/qemu/{vmid}/agent/network-get-interfaces", timeout=self.agent_timeout
			)
		except ProxmoxAPIError:
			return None

		interfaces = result.get("result") if isinstance(result, dict) else result
		for iface in interfaces or []:
			if iface.get("name") == "lo":
				continue
			for addr in iface.get("ip-addresses", []):
				if addr.get("ip-address-type") == "ipv4":
					return addr.get("ip-address")
		return None

	def open_vnc_proxy(self, node: str, vmid: int) -> dict:
		"""Mint a one-time VNC ticket for a QEMU VM's graphical console.

		Requires the token to hold VM.Console (well beyond this client's
		usual read-only monitoring scope) — see console_relay/api.py, the
		only caller, which uses a separate elevated token for exactly this
		reason. `websocket=1` requests the websocket-proxy variant rather
		than the legacy raw-TCP one, since the console relay always speaks
		the websocket variant (see console_relay/relay.py).
		"""
		return self.post(f"/nodes/{node}/qemu/{vmid}/vncproxy", data={"websocket": 1})

	def open_term_proxy(self, node: str, vmid: int) -> dict:
		"""Mint a one-time ticket for an LXC container's text console.

		Same VM.Console privilege requirement as open_vnc_proxy above —
		LXC has no graphical console at all, only ever a real shell.
		"""
		return self.post(f"/nodes/{node}/lxc/{vmid}/termproxy")

	def get_lxc_ip(self, node: str, vmid: int) -> str | None:
		"""Best-effort primary IPv4 address for a running LXC container."""
		try:
			interfaces = self.get(f"/nodes/{node}/lxc/{vmid}/interfaces", timeout=self.agent_timeout)
		except ProxmoxAPIError:
			return None

		for iface in interfaces or []:
			if iface.get("name") == "lo":
				continue
			for inet in (iface.get("inet"), iface.get("inet6")):
				if inet:
					return inet.split("/")[0]
		return None
