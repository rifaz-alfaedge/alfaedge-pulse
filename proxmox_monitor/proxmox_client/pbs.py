# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Client for the Proxmox Backup Server REST API.

PBS's API is close to PVE's but not identical — notably the API token
header uses a colon between the token id and secret instead of PVE's "=",
and datastore/snapshot endpoints live under `/admin/datastore` rather than
per-node.
"""

from __future__ import annotations

from collections import defaultdict

from proxmox_monitor.proxmox_client.base import BaseProxmoxClient


class PBSClient(BaseProxmoxClient):
	"""Talks to a Proxmox Backup Server's `/api2/json` API using a read-only API token."""

	def _auth_header(self, token_id: str, token_secret: str) -> str:
		# PBS token header format: "PBSAPIToken=USER@REALM!TOKENID:SECRET"
		# — a colon, not "=", between the token id and secret. Easy to get
		# wrong if copy-pasting the PVE format.
		return f"PBSAPIToken={token_id}:{token_secret}"

	def discover_node_name(self) -> str:
		"""PBS is always single-node, so this never actually calls `/nodes`.

		That listing endpoint rejects *any* API token with "permission check
		failed" regardless of the privileges granted to it — confirmed against
		a real instance with a token holding full Admin-equivalent rights at
		"/". This looks like a hardcoded platform restriction on that one
		bootstrap call, not something fixable via ACLs. PBS does, however,
		accept the literal alias "localhost" in place of a real node name on
		every `/nodes/{node}/...` path (verified against `/nodes/localhost/status`),
		which sidesteps the blocked call entirely.
		"""
		return "localhost"

	def get_node_status(self, node: str) -> dict:
		"""CPU/RAM/root-disk/uptime for the PBS host itself."""
		return self.get(f"/nodes/{node}/status")

	def list_datastores(self) -> list[dict]:
		"""All datastores configured on this PBS instance, e.g. the Linode-backed one."""
		return self.get("/admin/datastore")

	def get_datastore_usage(self, store: str) -> dict:
		"""Used/available/total bytes for a single datastore."""
		return self.get(f"/admin/datastore/{store}/status")

	def list_snapshots_by_guest(self, store: str) -> dict[str, list[dict]]:
		"""All backup snapshots in a datastore, grouped by guest (backup-id/vmid).

		Returns a dict keyed by ``backup-id`` (the vmid as a string, since
		PBS snapshots aren't necessarily VM/CT-specific — they can also be
		host or generic backups) mapping to that guest's snapshots sorted
		newest-first, so the poller can cheaply take ``[0]`` for "last backup".
		"""
		snapshots = self.get(f"/admin/datastore/{store}/snapshots")
		grouped: dict[str, list[dict]] = defaultdict(list)
		for snap in snapshots:
			grouped[snap.get("backup-id", "unknown")].append(snap)

		for guest_snapshots in grouped.values():
			guest_snapshots.sort(key=lambda s: s.get("backup-time", 0), reverse=True)

		return grouped
