# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Client for the Proxmox VE REST API.

Every host in this fleet (Alpha, Beta, Gamma) is a single-node PVE
install, not a cluster — so ``discover_node_name`` just takes whichever
node the ``/nodes`` endpoint returns first, rather than us hardcoding a
node name that may not match what the admin named the host in Proxmox.
"""

from __future__ import annotations

from proxmox_monitor.proxmox_client.base import BaseProxmoxClient, ProxmoxAPIError


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
