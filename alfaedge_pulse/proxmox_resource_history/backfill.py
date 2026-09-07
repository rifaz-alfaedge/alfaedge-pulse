# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""One-time historical import from Proxmox's own built-in RRD data.

Proxmox already retains its own internal CPU/RAM/swap/disk history for
every host and guest (``/nodes/{node}/rrddata`` and the per-guest
equivalents), at progressively coarser resolution the further back you
look — unused by this app until now. This gives immediate historical
context for a host that's already had swap/resource problems, rather than
only capturing spikes from whenever Proxmox Resource History first shipped.

Triggered manually via the "Backfill Resource History (RRD)" button on
Proxmox Server (see proxmox_server.json/.js) — not part of the regular
poll cycle, and safe to re-run (existing rows for the same
server/guest + collected_at are skipped, not duplicated).
"""

from __future__ import annotations

from datetime import datetime

import frappe
from frappe.utils import add_to_date, cint, flt, now_datetime
from frappe.utils.password import get_decrypted_password

from alfaedge_pulse.proxmox_client.pbs import PBSClient
from alfaedge_pulse.proxmox_client.pve import PVEClient
from alfaedge_pulse.proxmox_resource_history.retention import DEFAULT_RETENTION_DAYS


@frappe.whitelist()
def backfill_server(server_name: str, timeframe: str = "week") -> dict:
	"""Whitelisted handler for the "Backfill Resource History (RRD)" button.

	Pulls Proxmox's own RRD history for the host (and, for PVE, every
	current guest) and imports it into Proxmox Host/Guest Metric Log with
	``source="RRD Backfill"``. Caps the lookback at Resource History
	Retention (days) — no point importing data the next daily purge would
	immediately delete.

	Args:
		server_name: The ``Proxmox Server`` document name to backfill.
		timeframe: One of Proxmox's RRD timeframes ("hour"/"day"/"week"/
			"month"/"year") — "week" by default, a reasonable balance of
			recency and resolution for root-causing a recent spike.

	Returns:
		A dict with ``ok`` (bool) and either ``message`` or ``error``, meant
		to be shown directly to the user via ``frappe.msgprint``.
	"""
	server = frappe.get_doc("Proxmox Server", server_name)
	token_secret = get_decrypted_password("Proxmox Server", server.name, "api_token_secret", raise_exception=False)

	retention_days = cint(
		frappe.db.get_single_value("Proxmox Monitor Settings", "resource_history_retention_days")
	) or DEFAULT_RETENTION_DAYS
	cutoff = add_to_date(now_datetime(), days=-retention_days)

	try:
		if server.server_type == "PBS":
			client = PBSClient(server.hostname, server.port, server.api_token_id, token_secret, cint(server.verify_ssl))
			node = client.discover_node_name()
			host_count = _import_host_rrd(server, client.get_node_rrddata(node, timeframe=timeframe), cutoff)
			guest_count = 0
		else:
			client = PVEClient(server.hostname, server.port, server.api_token_id, token_secret, cint(server.verify_ssl))
			node = client.discover_node_name()
			host_count = _import_host_rrd(server, client.get_node_rrddata(node, timeframe=timeframe), cutoff)

			guest_count = 0
			for vm in client.list_qemu(node):
				vmid = cint(vm["vmid"])
				points = client.get_qemu_rrddata(node, vmid, timeframe=timeframe)
				guest_count += _import_guest_rrd(server, vmid, "QEMU (VM)", points, cutoff)
			for ct in client.list_lxc(node):
				vmid = cint(ct["vmid"])
				points = client.get_lxc_rrddata(node, vmid, timeframe=timeframe)
				guest_count += _import_guest_rrd(server, vmid, "LXC (CT)", points, cutoff)

		frappe.db.commit()
		return {
			"ok": True,
			"message": f"Backfilled {host_count} host sample(s) and {guest_count} guest sample(s) for {server_name}.",
		}
	except Exception as e:
		# Broad on purpose, matching proxmox_server.sync_now's pattern:
		# this is a whitelisted method whose caller (the frontend button)
		# always needs an {"ok": ..., "error": ...} dict back, never an
		# uncaught exception — a narrower `except ProxmoxAPIError` here
		# previously let a validation/DB error from _import_host_rrd's or
		# _import_guest_rrd's doc.insert() calls escape with no such dict,
		# leaving the button showing Frappe's generic error dialog instead
		# of the intended "Backfill Failed" message.
		frappe.db.rollback()
		frappe.log_error(title=f"Proxmox Resource History: backfill failed for {server_name}", message=frappe.get_traceback())
		return {"ok": False, "error": str(e)}


def _import_host_rrd(server, points: list[dict], cutoff) -> int:
	# One upfront query for every already-recorded timestamp in this
	# window, checked in-memory per point below — a re-run backfill over a
	# "week" timeframe (300+ points) previously did one frappe.db.exists()
	# round-trip per point instead of one per host.
	existing = set(
		frappe.get_all(
			"Proxmox Host Metric Log",
			filters={"server": server.name, "collected_at": [">=", cutoff]},
			pluck="collected_at",
		)
	)

	imported = 0
	for point in points:
		ts = point.get("time")
		if not ts:
			continue
		collected_at = datetime.fromtimestamp(ts)
		if collected_at < cutoff:
			continue

		cpu = point.get("cpu")
		memtotal = point.get("memtotal")
		memused = point.get("memused")
		# Proxmox leaves a bucket's fields as null/absent when no sample
		# landed in it (common at the sparse end of a longer timeframe) —
		# skip those rather than record a misleading zero.
		if cpu is None or not memtotal or memused is None:
			continue
		if collected_at in existing:
			continue

		swaptotal = flt(point.get("swaptotal"))
		swapused = point.get("swapused")
		roottotal = flt(point.get("roottotal"))
		rootused = flt(point.get("rootused"))

		frappe.get_doc(
			{
				"doctype": "Proxmox Host Metric Log",
				"server": server.name,
				"collected_at": collected_at,
				"source": "RRD Backfill",
				"cpu_usage": round(flt(cpu) * 100, 2),
				"memory_usage": round((flt(memused) / flt(memtotal)) * 100, 2),
				"memory_used_gb": round(flt(memused) / 1e9, 4),
				"memory_total_gb": round(flt(memtotal) / 1e9, 4),
				"swap_usage": round((flt(swapused) / swaptotal) * 100, 2) if swaptotal and swapused is not None else None,
				"swap_used_gb": round(flt(swapused) / 1e9, 4) if swaptotal and swapused is not None else None,
				"swap_total_gb": round(swaptotal / 1e9, 4) if swaptotal else None,
				"storage_usage": round((rootused / roottotal) * 100, 2) if roottotal else 0,
				"storage_used_gb": round(rootused / 1e9, 4),
				"storage_total_gb": round(roottotal / 1e9, 4),
			}
		).insert(ignore_permissions=True)
		existing.add(collected_at)
		imported += 1
	return imported


def _import_guest_rrd(server, vmid: int, guest_type: str, points: list[dict], cutoff) -> int:
	guest_name = f"{server.name}-{vmid}"
	if not frappe.db.exists("Proxmox Guest", guest_name):
		return 0  # guest not currently known to us — skip rather than create a placeholder

	# Same upfront-batch dedup as _import_host_rrd above, instead of one
	# frappe.db.exists() round-trip per RRD point.
	existing = set(
		frappe.get_all(
			"Proxmox Guest Metric Log",
			filters={"guest": guest_name, "collected_at": [">=", cutoff]},
			pluck="collected_at",
		)
	)

	imported = 0
	for point in points:
		ts = point.get("time")
		if not ts:
			continue
		collected_at = datetime.fromtimestamp(ts)
		if collected_at < cutoff:
			continue

		cpu = point.get("cpu")
		maxmem = point.get("maxmem")
		mem = point.get("mem")
		if cpu is None or not maxmem or mem is None:
			continue
		if collected_at in existing:
			continue

		maxdisk = point.get("maxdisk")
		disk = point.get("disk")
		disk_pct = round((flt(disk) / flt(maxdisk)) * 100, 1) if maxdisk and disk is not None else None

		frappe.get_doc(
			{
				"doctype": "Proxmox Guest Metric Log",
				"guest": guest_name,
				"server": server.name,
				"vmid": vmid,
				"guest_type": guest_type,
				"collected_at": collected_at,
				"source": "RRD Backfill",
				"cpu_usage": round(flt(cpu) * 100, 2),
				"memory_usage": round((flt(mem) / flt(maxmem)) * 100, 2),
				"memory_used_gb": round(flt(mem) / 1e9, 4),
				"memory_total_gb": round(flt(maxmem) / 1e9, 4),
				"disk_usage": disk_pct,
				# Proxmox's guest RRD data doesn't appear to carry an
				# absolute swap-usage-level field (only swapin/swapout
				# activity counters, which measure I/O rate, not usage
				# level) — left null rather than fabricated. Live-polled
				# LXC rows (collector.log_guest_metrics) do have a real
				# value, sourced from the live status endpoint instead.
				"swap_usage_percent": None,
				"swap_used_gb": None,
				"swap_total_gb": None,
			}
		).insert(ignore_permissions=True)
		existing.add(collected_at)
		imported += 1
	return imported
