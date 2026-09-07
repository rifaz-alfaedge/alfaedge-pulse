# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Live-sampling entry points for Proxmox Resource History.

Called directly from alfaedge_pulse.tasks.poller's existing
_apply_host_status / _sync_pve_guests — passing along data the poller has
already fetched from Proxmox, so this adds zero new API calls. Each entry
point is interval-gated independently of the poller's own poll_interval_seconds
(which stays tight, 5-20s, for responsive live alerting): history only
needs a coarser cadence (Resource History Sample Interval, default 60s) to
be useful for root-causing a spike that builds up over minutes, and logging
every poll cycle would grow these tables far faster than necessary — this
app has hit that exact bloat problem twice before (see
tasks/resource_monitor.py's docstring).

The gate is a short-lived Redis cache key per host/guest, mirroring the
poller's own HEARTBEAT_CACHE_KEY pattern: if the key is present, a sample
was already logged recently and this cycle is skipped; otherwise the key is
set (TTL = the configured interval) and a new row is inserted.
"""

from __future__ import annotations

from typing import NamedTuple

import frappe
from frappe.utils import cint, flt, now_datetime

_HOST_CACHE_KEY_FMT = "alfaedge_pulse:res_hist_last:host:{server_name}"
_GUEST_CACHE_KEY_FMT = "alfaedge_pulse:res_hist_last:guest:{guest_name}"

DEFAULT_INTERVAL_SECONDS = 60


class _Settings(NamedTuple):
	enabled: bool
	interval_seconds: int


def _get_settings() -> _Settings:
	# Re-read every call (cheap single-row lookup, same pattern as the
	# poller's own _get_thresholds()) rather than caching in a module
	# global, so a Settings change takes effect on the very next poll
	# cycle without a worker restart.
	return _Settings(
		enabled=bool(cint(frappe.db.get_single_value("Proxmox Monitor Settings", "resource_history_enabled"))),
		interval_seconds=cint(
			frappe.db.get_single_value("Proxmox Monitor Settings", "resource_history_interval_seconds")
		)
		or DEFAULT_INTERVAL_SECONDS,
	)


def _should_log(cache_key: str, interval_seconds: int) -> bool:
	if frappe.cache().get_value(cache_key):
		return False  # sampled recently, skip this cycle
	frappe.cache().set_value(cache_key, "1", expires_in_sec=max(interval_seconds, 1))
	return True


def _should_log_safe(cache_key: str, interval_seconds: int) -> bool:
	try:
		return _should_log(cache_key, interval_seconds)
	except Exception:
		# Fail OPEN: a Redis hiccup should degrade to "log every cycle"
		# (worse case: a few extra rows, cleaned up by the daily purge
		# anyway) rather than silently going blind on exactly the kind of
		# infra flakiness this feature exists to help diagnose.
		frappe.log_error(title="Proxmox Resource History: cache gate failed, logging anyway")
		return True


def log_host_metrics(server, status: dict) -> None:
	"""Append one Proxmox Host Metric Log row, if the sample interval has
	elapsed. ``status`` is the raw `/nodes/{node}/status` dict already
	fetched by `_apply_host_status` — computed independently here (not
	copied from the already-rounded `server.*` fields) so history isn't
	lossy-of-lossy.
	"""
	settings = _get_settings()
	if not settings.enabled:
		return

	cache_key = _HOST_CACHE_KEY_FMT.format(server_name=server.name)
	if not _should_log_safe(cache_key, settings.interval_seconds):
		return

	memory = status.get("memory") or {}
	swap = status.get("swap") or {}
	rootfs = status.get("rootfs") or {}
	swap_total_bytes = flt(swap.get("total"))

	try:
		frappe.get_doc(
			{
				"doctype": "Proxmox Host Metric Log",
				"server": server.name,
				"collected_at": now_datetime(),
				"source": "Live Poll",
				"cpu_usage": round(flt(status.get("cpu")) * 100, 2),
				"memory_usage": round((flt(memory.get("used")) / flt(memory.get("total") or 1)) * 100, 2),
				"memory_used_gb": round(flt(memory.get("used")) / 1e9, 4),
				"memory_total_gb": round(flt(memory.get("total")) / 1e9, 4),
				"swap_usage": round((flt(swap.get("used")) / swap_total_bytes) * 100, 2) if swap_total_bytes else None,
				"swap_used_gb": round(flt(swap.get("used")) / 1e9, 4) if swap_total_bytes else None,
				"swap_total_gb": round(swap_total_bytes / 1e9, 4) if swap_total_bytes else None,
				"storage_usage": round((flt(rootfs.get("used")) / flt(rootfs.get("total") or 1)) * 100, 2),
				"storage_used_gb": round(flt(rootfs.get("used")) / 1e9, 4),
				"storage_total_gb": round(flt(rootfs.get("total")) / 1e9, 4),
			}
		).insert(ignore_permissions=True)
	except Exception:
		# Best-effort, like every other non-essential call in poller.py
		# (e.g. list_backup_files) — this call sits inside the same
		# per-server transaction as guest upserts and alert dispatch
		# (sync_all_servers commits/rolls back once per server, not once
		# per guest). Letting an insert failure here propagate would abort
		# that whole transaction, silently undoing an already-*sent* alert
		# email's DB bookkeeping (the is_critical flip, the Alert Log row)
		# for any guest already processed this cycle — and since the email
		# itself can't be un-sent, the next cycle would see a stale
		# is_critical=0 and could fire a duplicate notification for a
		# condition already reported once. A lost history sample is a
		# trivial cost by comparison.
		frappe.log_error(title=f"Proxmox Resource History: failed to log host metrics for {server.name}")


def log_guest_metrics(
	server,
	vmid: int,
	guest_type: str,
	*,
	cpu_pct: float,
	mem_used: float,
	mem_total: float,
	disk_pct: float | None,
	swap_used: float | None,
	swap_total: float | None,
) -> None:
	"""Append one Proxmox Guest Metric Log row, if the sample interval has
	elapsed. Called as a sibling to `_upsert_guest`, not from inside it —
	`_upsert_guest`'s own signature/behavior is untouched. `swap_used`/
	`swap_total` are only ever non-None for LXC containers (real cgroup
	swap, already present in the same `/nodes/{node}/lxc` summary response
	the poller already fetched); QEMU has no API-visible swap.
	"""
	settings = _get_settings()
	if not settings.enabled:
		return

	guest_name = f"{server.name}-{vmid}"
	cache_key = _GUEST_CACHE_KEY_FMT.format(guest_name=guest_name)
	if not _should_log_safe(cache_key, settings.interval_seconds):
		return

	try:
		frappe.get_doc(
			{
				"doctype": "Proxmox Guest Metric Log",
				"guest": guest_name,
				"server": server.name,
				"vmid": vmid,
				"guest_type": guest_type,
				"collected_at": now_datetime(),
				"source": "Live Poll",
				"cpu_usage": round(cpu_pct, 2),
				"memory_usage": round((mem_used / mem_total) * 100, 2) if mem_total else 0,
				"memory_used_gb": round(mem_used / 1e9, 4),
				"memory_total_gb": round(mem_total / 1e9, 4),
				"disk_usage": disk_pct,
				"swap_usage_percent": round((swap_used / swap_total) * 100, 2) if swap_total else None,
				"swap_used_gb": round(swap_used / 1e9, 4) if swap_total else None,
				"swap_total_gb": round(swap_total / 1e9, 4) if swap_total else None,
			}
		).insert(ignore_permissions=True)
	except Exception:
		# Best-effort — see the identical comment in log_host_metrics above
		# for why this must never be allowed to abort the shared per-server
		# transaction (it would risk rolling back an already-sent alert's
		# DB bookkeeping alongside this guest's own upsert).
		frappe.log_error(title=f"Proxmox Resource History: failed to log guest metrics for {guest_name}")
