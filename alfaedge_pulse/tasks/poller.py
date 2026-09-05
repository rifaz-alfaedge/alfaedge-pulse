# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""The background poller: the one process that keeps everything in sync.

Design notes (the "why" behind the shape of this module):

- **No extra infrastructure.** Instead of a cron-scheduled job (Frappe's
  scheduler grants at most once-a-minute granularity) or a separate
  daemon, ``run_poll_loop`` is a background job that loops internally
  (``while ...: ... sleep(interval)``), giving a real ~20s heartbeat using
  only the RQ worker Frappe already runs.
- **Bounded runtime, not a true infinite loop.** The bench's `long` queue
  worker is configured with a very patient ``stopwaitsecs`` (1560s / 26min
  in supervisor.conf) — Frappe's own design assumes a long-running job
  will *eventually finish on its own* during a graceful shutdown, and
  waits accordingly rather than signalling it. A genuinely unbounded
  ``while True`` loop can never satisfy that — it would make every
  `bench restart` / deploy hang for the full 26 minutes (or forever, if
  ``stopwaitsecs`` were ever raised further). So ``run_poll_loop`` instead
  runs for up to ``MAX_LOOP_SECONDS`` and then returns normally, letting
  a graceful shutdown complete quickly; a SIGTERM handler is kept as a
  belt-and-braces fast exit for the (less common) case where RQ does
  forward the signal into the job.
- **Self-healing via heartbeat, not self-enqueue.** The loop refreshes a
  short-lived cache key every cycle. A separate watchdog
  (``ensure_poller_running``, wired to a 1-minute cron in hooks.py) checks
  that key and restarts the loop if it's gone stale — both after a crash
  and after each bounded loop's normal, expected exit. We deliberately do
  *not* have the loop re-enqueue itself with a fixed job_id: while the
  loop job is still "started" in RQ, a self-enqueue with the same job_id
  would be silently deduplicated away by Frappe's own enqueue-dedup logic,
  killing the loop after a single invocation. The heartbeat+watchdog
  pattern avoids that trap, at the cost of a sub-minute gap between one
  invocation ending and the next one picking back up.
- **One server's failure never blocks another's.** Beta being down
  is the whole reason this dashboard exists — ``sync_all_servers`` wraps
  each server's sync in its own try/except and commits per-server.
- **Backup health is judged over a rolling window, not per task.** With
  jobs running every 30-60 minutes, flagging critical on any single failed
  vzdump task (one guest locked, one transient error) fired constantly
  even though the fleet was still being backed up fine overall. Instead
  ``_handle_backup_task_result`` looks at whether *any* task succeeded
  anywhere on the host within ``BACKUP_OVERDUE_HOURS`` — only a genuine
  multi-hour gap with zero successes marks the *server* critical
  (``Proxmox Server.backup_critical``), not the individual guests inside
  any one job. Every task's outcome is still recorded in full for the
  Backups tab regardless.
- **Per-guest severity is tracked for every role, notified globally only
  for Production.** A Development/Staging/Backup host's VMs/CTs are still
  expected to spike routinely, but suppressing severity tracking entirely
  would also hide it from the dashboard and make per-instance Alert
  Subscriptions unable to ever see a transition. Instead, severity is
  always tracked; only the *global* Settings recipient list is gated to
  Production — see ``notify_global`` in ``_upsert_guest``.
- **A server can be "critical" for two independent reasons.** Host
  resource usage (CPU/RAM, from ``_apply_host_status``) and backup task
  health (``backup_critical``) are OR'd together into the final
  ``is_critical`` in ``sync_server``, but tracked and alerted on
  separately — clearing one never silently clears the other, and a
  transition in one doesn't get misreported as the other's alert type.
- **Both severity tiers require confirmation.** Neither Warning
  (``Thresholds.warning_percent``) nor Critical (``Thresholds.critical_percent``)
  fires on a single reading — each only fires once a reading has stayed at
  or above its line, with no dip below it, for ``Thresholds.confirmation_checks``
  consecutive polls — see ``_track_severity``. This applies identically to
  host CPU/RAM, guest CPU/RAM/disk, and datastore usage.
"""

from __future__ import annotations

import re
import signal
import time
from datetime import datetime, timedelta
from typing import NamedTuple

import frappe
from frappe.utils import add_to_date, cint, flt, get_datetime, now_datetime
from frappe.utils.password import get_decrypted_password

from alfaedge_pulse.alerts.dispatch import dispatch_alert, dispatch_recovery, has_open_alert, resolve_alert
from alfaedge_pulse.proxmox_client.base import ProxmoxAPIError
from alfaedge_pulse.proxmox_client.pbs import PBSClient
from alfaedge_pulse.proxmox_client.pve import PVEClient

POLLER_JOB_ID = "alfaedge_pulse_poller"
HEARTBEAT_CACHE_KEY = "alfaedge_pulse:poller_heartbeat"

# How long a single run_poll_loop invocation runs before returning normally
# (see the module docstring for why this is bounded rather than infinite).
# Comfortably inside the long queue's stopwaitsecs, and short enough that
# the 1-minute watchdog cron picks the loop back up almost immediately.
MAX_LOOP_SECONDS = 300


class Thresholds(NamedTuple):
	"""The three Settings fields that drive every resource check, bundled
	together since they're always read and threaded through as a group —
	see ``_get_thresholds`` and ``_track_severity``."""

	warning_percent: int
	critical_percent: int
	confirmation_checks: int


def _get_thresholds() -> Thresholds:
	return Thresholds(
		warning_percent=cint(frappe.db.get_single_value("Proxmox Monitor Settings", "warning_threshold_percent")) or 85,
		critical_percent=cint(frappe.db.get_single_value("Proxmox Monitor Settings", "critical_threshold_percent")) or 95,
		confirmation_checks=cint(frappe.db.get_single_value("Proxmox Monitor Settings", "confirmation_checks")) or 3,
	)



# ---------------------------------------------------------------------------
# Loop lifecycle
# ---------------------------------------------------------------------------


def ensure_poller_running() -> None:
	"""Watchdog: (re)start the poll loop if its heartbeat has gone stale.

	Wired to a 1-minute cron in hooks.py, and also called once from
	``after_migrate`` so the loop comes up automatically after installing
	or updating the app without any manual step. Also what picks the loop
	back up after each bounded ``run_poll_loop`` invocation ends normally.
	"""
	if frappe.cache().get_value(HEARTBEAT_CACHE_KEY):
		return  # loop is alive and ticking; nothing to do

	frappe.enqueue(
		"alfaedge_pulse.tasks.poller.run_poll_loop",
		queue="long",
		# A little headroom over MAX_LOOP_SECONDS for the final sync +
		# commit to finish; RQ force-kills the job if it ever runs past
		# this, as a hard backstop behind the loop's own bound.
		timeout=MAX_LOOP_SECONDS + 120,
		job_id=POLLER_JOB_ID,
		# job_id alone does not dedupe in Frappe -- deduplicate=True is a
		# separate, required opt-in (confirmed by reading enqueue()'s own
		# source). Without it, a job with this ID already queued/running
		# wouldn't actually block a second enqueue. In practice the
		# heartbeat check above already prevents most redundant calls, but
		# this closes the same race window explicitly rather than by luck.
		deduplicate=True,
	)


def run_poll_loop() -> None:
	"""One bounded run of the poll loop (up to MAX_LOOP_SECONDS), then returns normally.

	Not meant to be called directly outside of ``ensure_poller_running``,
	which is also what re-launches this once it returns — see the module
	docstring for why this is bounded rather than a true infinite loop.
	"""
	stop_requested = False

	def _request_stop(signum, frame):
		nonlocal stop_requested
		stop_requested = True

	# Belt-and-braces: if a SIGTERM *does* get forwarded into this job
	# (behaviour varies by RQ/worker configuration), exit within the
	# current sleep tick rather than waiting out the full bound below.
	previous_handler = signal.signal(signal.SIGTERM, _request_stop)
	start_time = time.monotonic()
	try:
		while not stop_requested and (time.monotonic() - start_time) < MAX_LOOP_SECONDS:
			interval = cint(frappe.db.get_single_value("Proxmox Monitor Settings", "poll_interval_seconds")) or 20

			# Heartbeat TTL is a few cycles wide so a single slow cycle (e.g. one
			# unreachable host taking the full request timeout) doesn't make the
			# watchdog think the loop has died and spawn a duplicate.
			frappe.cache().set_value(HEARTBEAT_CACHE_KEY, "1", expires_in_sec=max(interval * 4, 60))

			try:
				sync_all_servers()
			except Exception:
				# A failure here means something broke outside the per-server
				# try/except in sync_all_servers (e.g. reading Settings itself)
				# — log it but keep the loop alive rather than let one bad
				# cycle end the whole poller.
				frappe.log_error(title="Proxmox Monitor: poll cycle failed", message=frappe.get_traceback())

			# Sleep in 1s increments (rather than one time.sleep(interval) call)
			# so a SIGTERM, if forwarded into this job, lands within ~1s
			# instead of waiting out the full poll interval.
			for _ in range(interval):
				if stop_requested:
					break
				time.sleep(1)
	finally:
		signal.signal(signal.SIGTERM, previous_handler)


# ---------------------------------------------------------------------------
# Per-cycle orchestration
# ---------------------------------------------------------------------------


def sync_all_servers() -> None:
	"""Sync every enabled Proxmox Server, isolating failures per server."""
	server_names = frappe.get_all("Proxmox Server", filters={"enabled": 1}, pluck="name")
	for server_name in server_names:
		try:
			server = frappe.get_doc("Proxmox Server", server_name)
			sync_server(server)
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(
				title=f"Proxmox Monitor: sync failed for {server_name}", message=frappe.get_traceback()
			)


def sync_server(server) -> None:
	"""Sync one Proxmox Server document (PVE host or PBS instance).

	Also used directly by the "Test Connection & Sync Now" button for
	immediate, synchronous feedback outside the regular poll cadence.
	"""
	thresholds = _get_thresholds()
	token_secret = get_decrypted_password("Proxmox Server", server.name, "api_token_secret", raise_exception=False)

	old_status = server.status
	# Recomputed from the raw old readings rather than read off the stored
	# `is_critical` flag/`status`: those also carry `backup_critical` (OR'd
	# into is_critical below), and comparing against the combined value
	# would misattribute a backup-only transition to this "Critical
	# Resource"/"Resource Warning" alert, or mask a genuine resource
	# transition that happens to land while backup_critical is already
	# set. See the analogous note in _upsert_guest's history.
	old_value = max(flt(server.cpu_usage), flt(server.memory_usage)) if server.cpu_usage is not None else None
	old_resource_critical = old_value is not None and old_value >= thresholds.critical_percent
	old_resource_warning = old_status == "Warning"

	try:
		if server.server_type == "PBS":
			client = PBSClient(server.hostname, server.port, server.api_token_id, token_secret, cint(server.verify_ssl))
			node = client.discover_node_name()
			_apply_host_status(server, client.get_node_status(node), thresholds)
			_sync_pbs_datastores(server, client, thresholds)
		else:
			client = PVEClient(server.hostname, server.port, server.api_token_id, token_secret, cint(server.verify_ssl))
			node = client.discover_node_name()
			_apply_host_status(server, client.get_node_status(node), thresholds)
			_sync_pve_guests(server, client, node, thresholds)
			_sync_pve_storage(server, client, node, thresholds)
			_sync_pve_backups(server, client, node)

		server.last_error = ""
	except ProxmoxAPIError as e:
		server.status = "Offline"
		server.last_error = str(e)
	finally:
		# `_apply_host_status` (if it ran) just set is_critical/status purely
		# from CPU/RAM — capture that before OR-ing in the independent
		# backup-task signal, so the two stay decoupled for alerting below.
		new_resource_critical = cint(server.is_critical)
		new_resource_warning = server.status == "Warning"
		if server.server_type != "PBS" and cint(server.backup_critical):
			server.is_critical = 1
		server.last_synced = now_datetime()
		server.save(ignore_permissions=True)

	metrics = [("CPU", flt(server.cpu_usage)), ("RAM", flt(server.memory_usage))]
	_handle_transition(
		old_resource_critical, new_resource_critical, "Proxmox Server", server.name,
		"Critical Resource",
		f"{server.server_name}: {_format_over_threshold(metrics, thresholds.critical_percent)} above {thresholds.critical_percent}%",
		recovery_message=f"{server.server_name}: CPU/RAM usage back below {thresholds.critical_percent}%",
	)
	_handle_transition(
		old_resource_warning, new_resource_warning, "Proxmox Server", server.name,
		"Resource Warning",
		f"{server.server_name}: {_format_over_threshold(metrics, thresholds.warning_percent)} above {thresholds.warning_percent}% "
		f"for {thresholds.confirmation_checks}+ checks",
	)
	# The raw exception text (server.last_error) is deliberately left out of
	# the alert body — it can be a long technical string (a stack trace-like
	# HTTP/connection error) that doesn't help a human reading a
	# notification decide anything; it's still visible in the server's own
	# detail view for whoever needs to actually debug the outage.
	offline_message = f"{server.server_name}: server unreachable ({server.hostname})"
	if old_status != "Offline" and server.status == "Offline":
		dispatch_alert("Server Offline", "Proxmox Server", server.name, offline_message)
	elif old_status == "Offline" and server.status != "Offline":
		resolve_alert("Proxmox Server", server.name, "Server Offline")
		dispatch_recovery(
			"Server Offline", "Proxmox Server", server.name,
			f"{server.server_name}: server back online ({server.hostname})",
		)
	elif server.status == "Offline" and not has_open_alert("Proxmox Server", server.name, "Server Offline"):
		dispatch_alert("Server Offline", "Proxmox Server", server.name, offline_message)


# ---------------------------------------------------------------------------
# Host-level status (shared shape between PVE and PBS)
# ---------------------------------------------------------------------------


def _apply_host_status(server, status: dict, thresholds: Thresholds) -> None:
	"""Populate CPU/RAM/swap/root-disk/uptime from a PVE or PBS `/nodes/{node}/status` response.

	``storage_usage``/``storage_total`` (the root `/` filesystem) and
	``swap_usage``/``swap_total`` are kept as informational fields only —
	they deliberately do **not** feed into ``is_critical``/``is_warning``
	or the resource alerts here. This fleet's actual disk layout is two
	separate physical drives (OS+backup vs LVM-Thin guest storage), each
	already tracked as its own ``Proxmox Datastore`` row with its own
	critical flag and alert (see ``_upsert_datastore``) — folding a third,
	less meaningful root-fs number (or swap, which Linux uses opportunistically
	long before it's actually a problem) into the host's own alert would
	just muddy which resource is actually the issue.
	"""
	memory = status.get("memory") or {}
	swap = status.get("swap") or {}
	rootfs = status.get("rootfs") or {}

	cpu_usage = round(flt(status.get("cpu")) * 100, 1)
	memory_usage = round((flt(memory.get("used")) / flt(memory.get("total") or 1)) * 100, 1)
	storage_usage = round((flt(rootfs.get("used")) / flt(rootfs.get("total") or 1)) * 100, 1)
	swap_total_bytes = flt(swap.get("total"))

	server.status = "Online"
	server.cpu_usage = cpu_usage
	server.memory_usage = memory_usage
	server.memory_total = round(flt(memory.get("total")) / 1e9, 2)
	# Null (not 0%) when a host has no swap configured at all (seen on some
	# of this fleet's hosts) — 0% would misleadingly read as "swap is fine"
	# rather than "there's no swap to measure".
	server.swap_usage = round((flt(swap.get("used")) / swap_total_bytes) * 100, 1) if swap_total_bytes else None
	server.swap_total = round(swap_total_bytes / 1e9, 2) if swap_total_bytes else None
	server.storage_usage = storage_usage
	server.storage_total = round(flt(rootfs.get("total")) / 1e9, 2)
	server.uptime = cint(status.get("uptime"))

	is_critical, is_warning = _track_severity(server, max(cpu_usage, memory_usage), thresholds)
	server.is_critical = 1 if is_critical else 0
	server.status = "Critical" if is_critical else "Warning" if is_warning else "Online"


# ---------------------------------------------------------------------------
# PVE: guests
# ---------------------------------------------------------------------------


def _sync_pve_guests(server, client: PVEClient, node: str, thresholds: Thresholds) -> None:
	"""Upsert Proxmox Guest for every VM/CT on this node, and delete ones no longer present."""
	seen_vmids = set()

	for vm in client.list_qemu(node):
		vmid = cint(vm["vmid"])
		seen_vmids.add(vmid)
		disk_usage = disk_total = None
		if vm.get("status") == "running":
			# Only worth asking the guest agent for VMs that are actually up;
			# it has its own short timeout (see BaseProxmoxClient.agent_timeout)
			# so a VM without the agent installed can't stall this cycle.
			disk_usage = client.get_qemu_disk_usage_percent(node, vmid)
			ip_address = client.get_qemu_agent_ip(node, vmid)
		else:
			ip_address = None
		if vm.get("maxdisk"):
			disk_total = round(flt(vm["maxdisk"]) / 1e9, 2)

		_upsert_guest(
			server, vmid, vm.get("name") or f"vm-{vmid}", "QEMU (VM)", vm.get("status", "unknown"),
			flt(vm.get("cpu")) * 100, flt(vm.get("mem")), flt(vm.get("maxmem")),
			disk_usage, disk_total, cint(vm.get("uptime")), ip_address, thresholds, node,
		)

	for ct in client.list_lxc(node):
		vmid = cint(ct["vmid"])
		seen_vmids.add(vmid)
		disk_usage = None
		if ct.get("maxdisk"):
			disk_usage = round((flt(ct.get("disk")) / flt(ct["maxdisk"])) * 100, 1)
		disk_total = round(flt(ct.get("maxdisk")) / 1e9, 2) if ct.get("maxdisk") else None
		ip_address = client.get_lxc_ip(node, vmid) if ct.get("status") == "running" else None

		_upsert_guest(
			server, vmid, ct.get("name") or f"ct-{vmid}", "LXC (CT)", ct.get("status", "unknown"),
			flt(ct.get("cpu")) * 100, flt(ct.get("mem")), flt(ct.get("maxmem")),
			disk_usage, disk_total, cint(ct.get("uptime")), ip_address, thresholds, node,
		)

	# True auto-sync: a guest Proxmox no longer reports (deleted/migrated
	# away) disappears from the dashboard on the next cycle, no manual
	# cleanup required.
	stale = frappe.get_all(
		"Proxmox Guest", filters={"server": server.name, "vmid": ["not in", list(seen_vmids) or [0]]}, pluck="name"
	)
	for name in stale:
		frappe.delete_doc("Proxmox Guest", name, ignore_permissions=True, force=True)


def _upsert_guest(
	server, vmid: int, guest_name: str, guest_type: str, status: str,
	cpu_pct: float, mem_used: float, mem_total: float,
	disk_pct: float | None, disk_total: float | None, uptime: int, ip_address: str | None, thresholds: Thresholds,
	node: str,
) -> None:
	docname = f"{server.name}-{vmid}"
	memory_usage = round((mem_used / mem_total) * 100, 1) if mem_total else 0
	memory_total_gb = round(mem_total / 1e9, 2) if mem_total else 0
	cpu_usage = round(cpu_pct, 1)

	if frappe.db.exists("Proxmox Guest", docname):
		doc = frappe.get_doc("Proxmox Guest", docname)
	else:
		doc = frappe.new_doc("Proxmox Guest")
		doc.server = server.name
		doc.vmid = vmid

	old_is_critical = cint(doc.is_critical)
	old_is_warning = cint(doc.is_warning)

	# Severity is tracked for every guest regardless of role — a
	# Development/Staging/Backup host's VMs/CTs are still expected to spike
	# routinely, but suppressing that here would also hide it from the
	# dashboard and make it impossible for an individual Alert Subscription
	# to ever see a transition to notify on. Instead, the *global* Settings
	# recipient list stays Production-only — see notify_global below and
	# dispatch.py's _merge_contacts.
	readings = [cpu_usage, memory_usage] + ([disk_pct] if disk_pct is not None else [])
	if readings:
		is_critical, is_warning = _track_severity(doc, max(readings), thresholds)
	else:
		is_critical, is_warning = False, False
		doc.warning_streak = 0
		doc.critical_streak = 0

	doc.guest_name = guest_name
	doc.guest_type = guest_type
	doc.node = node
	doc.status = status
	doc.cpu_usage = cpu_usage
	doc.memory_usage = memory_usage
	doc.memory_total = memory_total_gb
	doc.disk_usage = disk_pct
	doc.disk_total = disk_total
	doc.uptime = uptime
	doc.last_synced = now_datetime()
	doc.is_critical = 1 if is_critical else 0
	doc.is_warning = 1 if is_warning else 0
	if ip_address:
		doc.ip_address = ip_address

	doc.save(ignore_permissions=True)

	metrics = [("CPU", cpu_usage), ("RAM", memory_usage)] + ([("Disk", disk_pct)] if disk_pct is not None else [])
	# Global Settings recipients stay Production-only, matching this app's
	# existing "Dev/Staging spikes routinely, would be noise" stance —
	# individual Alert Subscriptions still get notified regardless of role
	# (see _handle_transition/dispatch.py), since subscribing is an
	# explicit, deliberate opt-in per instance.
	notify_global = server.role == "Production"
	_handle_transition(
		old_is_critical, is_critical, "Proxmox Guest", doc.name,
		"Critical Resource",
		f"{server.server_name} → {guest_name}: {_format_over_threshold(metrics, thresholds.critical_percent)} "
		f"above {thresholds.critical_percent}%",
		recovery_message=f"{server.server_name} → {guest_name}: usage back below {thresholds.critical_percent}%",
		notify_global=notify_global,
	)
	_handle_transition(
		old_is_warning, is_warning, "Proxmox Guest", doc.name,
		"Resource Warning",
		f"{server.server_name} → {guest_name}: {_format_over_threshold(metrics, thresholds.warning_percent)} "
		f"above {thresholds.warning_percent}% for {thresholds.confirmation_checks}+ checks",
		notify_global=notify_global,
	)


# ---------------------------------------------------------------------------
# PVE: storage pools (the 2-drive layout)
# ---------------------------------------------------------------------------


def _classify_storage_role(storage: dict) -> str:
	"""Map a PVE storage entry to one of our two physical drives, or Other.

	Every host in this fleet has exactly two drives: Drive 1 runs the OS
	and holds local vzdump backups (a `dir`-type storage, conventionally
	named "local"), Drive 2 is an LVM-Thin pool holding guest disks. A PBS
	or NFS-type storage (e.g. one pointed at the PBS server itself) is
	neither, hence "Other".
	"""
	storage_type = storage.get("type")
	if storage_type in ("lvmthin", "lvm"):
		return "Guest Storage (LVM-Thin)"
	if storage_type == "dir" and storage.get("storage") == "local":
		return "OS + Local Backup Drive"
	return "Other"


def _sync_pve_storage(server, client: PVEClient, node: str, thresholds: Thresholds) -> None:
	seen_names = set()
	for storage in client.list_storage(node):
		if "total" not in storage:  # storage type doesn't report usage (e.g. an inactive/remote entry)
			continue
		name = storage["storage"]
		seen_names.add(name)
		_upsert_datastore(
			server, name, "PVE Storage", _classify_storage_role(storage),
			flt(storage.get("used")), flt(storage["total"]), thresholds,
		)

	stale = frappe.get_all(
		"Proxmox Datastore",
		filters={"server": server.name, "datastore_type": "PVE Storage", "datastore_name": ["not in", list(seen_names) or [""]]},
		pluck="name",
	)
	for name in stale:
		frappe.delete_doc("Proxmox Datastore", name, ignore_permissions=True, force=True)


def _upsert_datastore(server, name: str, datastore_type: str, storage_role: str, used_bytes: float, total_bytes: float, thresholds: Thresholds) -> None:
	docname = f"{server.name}-{name}"
	usage_percent = round((used_bytes / total_bytes) * 100, 1) if total_bytes else 0

	if frappe.db.exists("Proxmox Datastore", docname):
		doc = frappe.get_doc("Proxmox Datastore", docname)
	else:
		doc = frappe.new_doc("Proxmox Datastore")
		doc.server = server.name
		doc.datastore_name = name

	old_is_critical = cint(doc.is_critical)
	old_is_warning = cint(doc.is_warning)
	is_critical, is_warning = _track_severity(doc, usage_percent, thresholds)

	doc.datastore_type = datastore_type
	doc.storage_role = storage_role
	doc.used = round(used_bytes / 1e9, 2)
	doc.total = round(total_bytes / 1e9, 2)
	doc.usage_percent = usage_percent
	doc.is_critical = 1 if is_critical else 0
	doc.is_warning = 1 if is_warning else 0
	doc.last_synced = now_datetime()
	doc.save(ignore_permissions=True)

	_handle_transition(
		old_is_critical, is_critical, "Proxmox Datastore", doc.name,
		"Critical Resource",
		f"{server.server_name}: {name} storage {round(usage_percent)}% full (above {thresholds.critical_percent}%)",
		recovery_message=f"{server.server_name}: {name} storage back below {thresholds.critical_percent}% full (now {round(usage_percent)}%)",
	)
	_handle_transition(
		old_is_warning, is_warning, "Proxmox Datastore", doc.name,
		"Resource Warning",
		f"{server.server_name}: {name} storage {round(usage_percent)}% full "
		f"(above {thresholds.warning_percent}% for {thresholds.confirmation_checks}+ checks)",
	)


# ---------------------------------------------------------------------------
# PVE: local (vzdump) backups — the authoritative source for both
# Local Disk *and* PBS Remote backup pass/fail, since both are just vzdump
# jobs on the originating PVE host targeting different storage.
#
# A vzdump task comes in two shapes, and both actually occur in this fleet:
#   - A single-guest task (typically a manual "Backup now" run), whose `id`
#     in the /tasks summary is the vmid directly.
#   - A bulk/scheduled job covering every selected guest in one task (the
#     normal case for a nightly "back up everything" job) — Proxmox never
#     breaks this down per guest in the /tasks summary; `id` comes back
#     empty. The only place an individual guest's pass/fail shows up is
#     inside the task's own log text: "Starting Backup of VM <vmid>" /
#     "Finished Backup of VM <vmid>" per guest, or "ERROR: Backup of VM
#     <vmid> failed ..." for one that didn't. Treating an empty `id` as
#     "not a per-guest task, skip it" — the original approach here — meant
#     every guest whose backups only ever ran through a bulk job was
#     silently invisible, no matter how many successful backups actually
#     existed for it in Proxmox.
# ---------------------------------------------------------------------------

_BULK_START_RE = re.compile(r"Starting Backup of VM (\d+)")
_BULK_FINISHED_RE = re.compile(r"Finished Backup of VM (\d+)")
_BULK_FINISHED_AT_RE = re.compile(r"Backup finished at (.+)")
_BULK_FAILED_RE = re.compile(r"ERROR: Backup of VM (\d+) failed(?: - (.+))?")
_ARCHIVE_SIZE_RE = re.compile(r"archive file size:\s*([\d.]+)\s*([KMGT]B)")
_SIZE_UNIT_TO_GB = {"KB": 1e-6, "MB": 1e-3, "GB": 1.0, "TB": 1e3}


def _parse_archive_size_gb(text: str) -> float | None:
	"""Pull the backup's size in GB from a vzdump log line like
	"INFO: archive file size: 1.13GB" or "...: 272MB" — the task summary
	and snapshot listings don't carry this, only the log text does.
	"""
	match = _ARCHIVE_SIZE_RE.search(text)
	if not match:
		return None
	value, unit = match.groups()
	return round(float(value) * _SIZE_UNIT_TO_GB[unit], 3)


def _sync_pve_backups(server, client: PVEClient, node: str) -> None:
	"""Record every guest's backup result (for the Backups tab), then flag
	the *server* critical only if no backup task anywhere on the host has
	succeeded within ``BACKUP_OVERDUE_HOURS``.

	Per-guest history is still kept in full — that's what the dashboard's
	Backups tab groups/sorts/displays — but alerting doesn't react to any
	single task's outcome any more (see ``_handle_backup_task_result``):
	one guest failing inside an otherwise-healthy every-30-60-minutes
	backup schedule shouldn't page anyone.
	"""
	# One extra call per cycle, used only to look up each backup's Notes
	# field (see _find_backup_notes) — the task list/log never carry it.
	# Best-effort: if this fails, backups are still recorded correctly,
	# just without notes.
	try:
		backup_files = client.list_backup_files(node, "local")
	except ProxmoxAPIError:
		backup_files = []

	tasks = client.list_vzdump_tasks(node, limit=50)

	for task in tasks:
		if not task.get("endtime"):
			continue  # still running; we'll see its final status on a later cycle
		upid = task.get("upid")
		if not upid:
			continue

		vmid = _parse_vmid(task.get("id"))
		if vmid is not None:
			_record_single_guest_backup(server, client, node, task, upid, vmid, backup_files)
		else:
			_record_bulk_backup_job(server, client, node, task, upid, backup_files)

	# Evaluated every cycle regardless of whether a new task showed up this
	# time — that's what catches backups having quietly stopped altogether
	# (no new tasks at all), not just "the last one failed".
	_handle_backup_task_result(server)


def _find_backup_notes(backup_files: list[dict], vmid: int, backup_time: datetime) -> str:
	"""Match a recorded backup to its file's Notes field by vmid and closest
	creation time — neither the task list nor its log carries notes, only
	the storage content listing does, and that listing has no direct link
	back to a specific task/UPID. A generous one-hour tolerance absorbs
	the gap between a file's own ctime and this backup's recorded
	finish time inside a long bulk job.
	"""
	candidates = [f for f in backup_files if cint(f.get("vmid")) == vmid]
	if not candidates:
		return ""
	target = backup_time.timestamp()
	closest = min(candidates, key=lambda f: abs(flt(f.get("ctime")) - target))
	if abs(flt(closest.get("ctime")) - target) > 3600:
		return ""
	return closest.get("notes") or ""


def _record_single_guest_backup(
	server, client: PVEClient, node: str, task: dict, upid: str, vmid: int, backup_files: list[dict],
) -> None:
	"""A vzdump task whose `id` is already a specific vmid — e.g. a manual
	"Backup now" run. A no-op if this task was already recorded on a
	previous cycle.
	"""
	if frappe.db.exists("Proxmox Backup Log", {"upid": upid}):
		return

	status = "Success" if task.get("status") == "OK" else "Failed"
	error_message = "" if status == "Success" else task.get("status", "Unknown error")
	backup_source, storage_target, size_gb = _classify_backup_target(client, node, upid)
	backup_time = datetime.fromtimestamp(task["endtime"])
	notes = _find_backup_notes(backup_files, vmid, backup_time) if backup_source == "Local Disk" else ""

	_create_backup_log(
		server, vmid, backup_source, status, backup_time, storage_target, error_message, upid, size_gb, notes,
	)


def _record_bulk_backup_job(
	server, client: PVEClient, node: str, task: dict, upid: str, backup_files: list[dict],
) -> None:
	"""A vzdump task covering multiple guests at once (the common nightly-job case).

	Once fully processed, a task is never re-fetched: any already-recorded
	row with this upid's prefix is proof the whole log was parsed on a
	previous cycle, so a large recurring bulk job only ever costs one
	extra API call the first time it's seen. A no-op if it was already
	recorded, or if its log couldn't be fetched this cycle (try again next
	time).
	"""
	if frappe.db.exists("Proxmox Backup Log", {"upid": ["like", f"{upid}#%"]}):
		return

	try:
		# limit=0 is Proxmox's own convention for "no limit" on this
		# endpoint — a bulk job backing up a whole fleet can run to several
		# hundred log lines, well past any small fixed page size.
		log_lines = client.get(f"/nodes/{node}/tasks/{upid}/log", params={"limit": 0})
	except ProxmoxAPIError:
		return  # try again next cycle

	joined = " ".join(line.get("t", "") for line in log_lines)
	backup_source = "PBS Remote" if "Proxmox Backup Server archive" in joined else "Local Disk"
	storage_target = "pbs" if backup_source == "PBS Remote" else "local"
	fallback_time = datetime.fromtimestamp(task["endtime"])
	tz_offset = _detect_log_timezone_offset(log_lines, task)

	entries = _parse_bulk_backup_log(log_lines, fallback_time, tz_offset)
	for vmid, status, backup_time, error_message, size_gb in entries:
		notes = _find_backup_notes(backup_files, vmid, backup_time) if backup_source == "Local Disk" else ""
		_create_backup_log(
			server, vmid, backup_source, status, backup_time, storage_target, error_message,
			f"{upid}#{vmid}", size_gb, notes,
		)


_BACKUP_STARTED_AT_RE = re.compile(r"Backup started at (.+)")


def _detect_log_timezone_offset(log_lines: list[dict], task: dict) -> timedelta:
	"""Work out the gap between this task's log *text* timestamps and true
	UTC, so per-guest times parsed from that text can be corrected.

	Proxmox prints "Backup started/finished at ..." in the **host's own
	configured local timezone** — which can be anything — while a task's
	own ``starttime``/``endtime`` are always plain (tz-agnostic) Unix
	epochs. Naively parsing the log text as if it were already correct
	silently introduces an error equal to that offset on any host not set
	to UTC (verified against real data: a host running IST, UTC+5:30,
	made every per-guest timestamp come out 5.5 hours ahead — enough to
	break the Notes lookup's tolerance and to misreport "when" throughout
	the app). Measuring it per task from the very first "Backup started
	at" line against that same task's own ``starttime`` means this is
	correct for whatever timezone the host actually has, not an assumed one.
	"""
	for line in log_lines:
		match = _BACKUP_STARTED_AT_RE.search(line.get("t", ""))
		if not match:
			continue
		try:
			wall_clock = datetime.strptime(match.group(1).strip(), "%Y-%m-%d %H:%M:%S")
		except ValueError:
			break
		return wall_clock - datetime.fromtimestamp(task["starttime"])
	return timedelta(0)


def _parse_bulk_backup_log(
	log_lines: list[dict], fallback_time: datetime, tz_offset: timedelta = timedelta(0),
) -> list[tuple[int, str, datetime, str, float | None]]:
	"""Extract one (vmid, status, backup_time, error_message, size_gb) tuple
	per guest found in a bulk vzdump job's log text.

	Matches the exact, verified log shape of a real bulk job on this
	fleet: a "Starting Backup of VM <vmid>" line opens each guest's
	section — within it, "archive file size: ..." gives the size — closed
	eventually by either "Finished Backup of VM <vmid>" (success — the
	precise end timestamp is on the very next line, "Backup finished at
	...", corrected by ``tz_offset`` — see _detect_log_timezone_offset)
	or "ERROR: Backup of VM <vmid> failed ..." (failure, no separate
	timestamp or size line, so this guest's entry uses the whole task's
	own end time and no size).
	"""
	results = []
	current_vmid = None
	current_size_gb = None
	for i, line in enumerate(log_lines):
		text = line.get("t", "")

		start_match = _BULK_START_RE.search(text)
		if start_match:
			current_vmid = int(start_match.group(1))
			current_size_gb = None
			continue

		if current_vmid is None:
			continue

		size_match = _parse_archive_size_gb(text)
		if size_match is not None:
			current_size_gb = size_match
			continue

		failed_match = _BULK_FAILED_RE.search(text)
		if failed_match and int(failed_match.group(1)) == current_vmid:
			results.append(
				(current_vmid, "Failed", fallback_time, failed_match.group(2) or "Backup failed", None)
			)
			current_vmid = None
			continue

		finished_match = _BULK_FINISHED_RE.search(text)
		if finished_match and int(finished_match.group(1)) == current_vmid:
			backup_time = fallback_time
			if i + 1 < len(log_lines):
				at_match = _BULK_FINISHED_AT_RE.search(log_lines[i + 1].get("t", ""))
				if at_match:
					try:
						backup_time = datetime.strptime(at_match.group(1).strip(), "%Y-%m-%d %H:%M:%S") - tz_offset
					except ValueError:
						pass
			results.append((current_vmid, "Success", backup_time, "", current_size_gb))
			current_vmid = None

	return results


def _create_backup_log(
	server, vmid: int, backup_source: str, status: str, backup_time: datetime,
	storage_target: str, error_message: str, upid: str, size_gb: float | None = None, notes: str = "",
) -> None:
	"""Shared insert + last-successful-backup bookkeeping for one guest's backup result.

	Used by both the single-guest and bulk-job paths above so "what happens
	when a backup is recorded" is implemented once. Purely historical
	record-keeping for the Backups tab — critical-flagging and alerting is
	decided separately, once per sync cycle, from this same table's history
	(see ``_handle_backup_task_result``), not here per guest.
	"""
	guest_name = _match_guest_by_vmid(server, vmid)

	doc = frappe.get_doc(
		{
			"doctype": "Proxmox Backup Log",
			"server": server.name,
			"guest": guest_name,
			"vmid": vmid,
			"backup_source": backup_source,
			"status": status,
			"backup_time": backup_time,
			"size": size_gb,
			"storage_target": storage_target,
			"notes": notes,
			"error_message": error_message,
			"upid": upid,
		}
	)
	doc.insert(ignore_permissions=True)

	if status == "Success" and guest_name:
		_bump_last_successful_backup(guest_name, backup_time)


def _classify_backup_target(client: PVEClient, node: str, upid: str) -> tuple[str, str, float | None]:
	"""Work out whether a vzdump task targeted local storage or the PBS
	datastore, and how big the resulting archive was.

	The `/nodes/{node}/tasks` summary list doesn't say which storage a
	vzdump job targeted, or its size — both only show up in the task's log
	text (a line reading "creating Proxmox Backup Server archive" for a
	PBS-target job vs "creating vzdump archive" for a local one; "archive
	file size: ..." for the size). We only pay this extra API call once
	per task, ever, since backups are only classified the first time
	they're recorded (see the `upid` dedup check above).
	"""
	try:
		log_lines = client.get(f"/nodes/{node}/tasks/{upid}/log", params={"limit": 0})
	except ProxmoxAPIError:
		return "Local Disk", "", None

	joined = " ".join(line.get("t", "") for line in log_lines)
	backup_source = "PBS Remote" if "Proxmox Backup Server archive" in joined else "Local Disk"
	storage_target = "pbs" if backup_source == "PBS Remote" else "local"

	size_gb = None
	for line in log_lines:
		size_gb = _parse_archive_size_gb(line.get("t", ""))
		if size_gb is not None:
			break

	return backup_source, storage_target, size_gb


def _bump_last_successful_backup(guest_name: str | None, backup_time: datetime) -> None:
	"""Record the latest successful-backup time directly on the guest.

	Purely informational (shown in the guest detail view) — nothing reads
	this to drive alerting; see ``_handle_backup_task_result`` for that.
	Only ever moves forward — a backfilled older PBS snapshot can't regress
	a more recent local-disk backup already recorded.
	"""
	if not guest_name:
		return
	current = frappe.db.get_value("Proxmox Guest", guest_name, "last_successful_backup")
	if not current or backup_time > current:
		frappe.db.set_value("Proxmox Guest", guest_name, "last_successful_backup", backup_time)


#: A single failed vzdump task no longer flags anything on its own — with
#: jobs running every 30-60 minutes, one guest's transient failure amid an
#: otherwise-healthy schedule isn't an incident. Only a full day with zero
#: successful backups anywhere on the host is.
BACKUP_OVERDUE_HOURS = 24


def _handle_backup_task_result(server) -> None:
	"""Flag the server critical only if no backup task anywhere on this
	host has succeeded within ``BACKUP_OVERDUE_HOURS`` — not on any single
	task's own pass/fail, which is still recorded in full on the Backups
	tab regardless (see ``_create_backup_log``/``_sync_pve_backups``).

	Called once per sync cycle, unconditionally — including cycles with no
	new tasks at all — so a schedule that's quietly stopped producing tasks
	altogether is caught the same as one whose tasks keep failing.
	``backup_critical`` is sticky: it only changes when this evaluation's
	result actually flips, so a genuinely overdue host stays flagged across
	cycles until a new success is recorded, and clears the moment one is.
	Scoped to the server rather than any individual guest, since "nothing
	has backed up successfully in a day" (e.g. the backup drive filling up,
	or the schedule itself broken) is a host-level incident, not a per-VM
	one.

	Sets ``server.backup_critical`` as a plain in-memory attribute rather
	than writing it straight to the database: this runs from inside
	``_sync_pve_backups``, itself called from ``sync_server`` while it's
	still holding this same ``server`` document and will ``.save()`` it
	once more before returning. A direct DB write here bumps the row's
	``modified`` timestamp out from under that still-pending save, which
	Frappe then rejects as a conflicting edit (``TimestampMismatchError``)
	— caught once this started happening on *every* cycle for a host with
	no backup tasks at all, since until then a same-cycle collision was
	rare enough (only ever-so-often a new task) to not have been noticed.
	"""
	cutoff = add_to_date(now_datetime(), hours=-BACKUP_OVERDUE_HOURS)
	last_success = frappe.db.get_value(
		"Proxmox Backup Log", {"server": server.name, "status": "Success"}, [{"MAX": "backup_time"}]
	)
	is_critical_now = not last_success or get_datetime(last_success) < cutoff
	was_critical = cint(server.backup_critical)
	server.backup_critical = 1 if is_critical_now else 0

	if is_critical_now and not was_critical:
		detail = f"last successful backup was {last_success}" if last_success else "no successful backup has ever been recorded"
		dispatch_alert(
			"Backup Failure", "Proxmox Server", server.name,
			f"{server.server_name}: no successful backup in over {BACKUP_OVERDUE_HOURS}h ({detail}) — see Backups tab for details",
		)
	elif was_critical and not is_critical_now:
		resolve_alert("Proxmox Server", server.name, "Backup Failure")


def _parse_vmid(raw) -> int | None:
	"""Safely parse a Proxmox task/snapshot id as a vmid, or None if it isn't one.

	Not every `/tasks` or `/snapshots` entry represents a specific guest —
	e.g. an aggregate/host-level job — and Frappe's ``cint()`` silently
	returns 0 for non-numeric input rather than raising, so a bare
	``cint(raw)`` can't tell "genuinely vmid 0" apart from "not a vmid at
	all". We check the shape explicitly instead of trusting a try/except
	around cint() (which never actually raises).
	"""
	if raw is None:
		return None
	raw = str(raw)
	if not raw.lstrip("-").isdigit():
		return None
	return cint(raw)


def _match_guest_by_vmid(server, vmid: int | None) -> str | None:
	"""Best-effort match of a bare vmid (from a backup task) to a Proxmox Guest.

	Same-host backups match unambiguously via (server, vmid). Cross-host
	ambiguity is a non-issue in this fleet since only Alpha ships to the
	shared PBS datastore, but if that ever changes and the same vmid
	exists on multiple hosts, we deliberately return None rather than
	guess — an unmatched backup log row is still fully visible on the
	Backups tab, just not linked to a specific guest record.
	"""
	if vmid is None:
		return None
	return frappe.db.get_value("Proxmox Guest", {"server": server.name, "vmid": vmid})


# ---------------------------------------------------------------------------
# PBS: datastores + snapshot backfill
# ---------------------------------------------------------------------------


def _sync_pbs_datastores(server, client: PBSClient, thresholds: Thresholds) -> None:
	for ds in client.list_datastores():
		store = ds["store"]
		usage = client.get_datastore_usage(store)
		_upsert_datastore(server, store, "PBS Datastore", "Other", flt(usage.get("used")), flt(usage.get("total")), thresholds)
		_backfill_pbs_snapshots(server, client, store)


def _backfill_pbs_snapshots(server, client: PBSClient, store: str) -> None:
	"""Record any PBS snapshot not already captured via the PVE task-log path.

	PBS snapshots only ever represent *successful* writes — a failed
	backup attempt never produces a snapshot, so this can only confirm
	successes (failure detection is the PVE task log's job, in
	``_sync_pve_backups``). This mainly backfills backup history that has
	aged out of the originating PVE host's much shorter task-log retention.
	"""
	snapshots_by_guest = client.list_snapshots_by_guest(store)
	for backup_id, snapshots in snapshots_by_guest.items():
		latest = snapshots[0]
		vmid = _parse_vmid(backup_id)
		if vmid is None:
			continue

		synthetic_upid = f"pbs:{store}:{backup_id}:{latest.get('backup-time')}"
		if frappe.db.exists("Proxmox Backup Log", {"upid": synthetic_upid}):
			continue

		guest_name = frappe.db.get_value("Proxmox Guest", {"vmid": vmid})
		backup_time = datetime.fromtimestamp(latest["backup-time"])
		frappe.get_doc(
			{
				"doctype": "Proxmox Backup Log",
				"server": server.name,
				"guest": guest_name,
				"vmid": vmid,
				"backup_source": "PBS Remote",
				"status": "Success",
				"backup_time": backup_time,
				"size": round(flt(latest.get("size")) / 1e9, 3) if latest.get("size") else None,
				"storage_target": store,
				"upid": synthetic_upid,
			}
		).insert(ignore_permissions=True)
		_bump_last_successful_backup(guest_name, backup_time)


# ---------------------------------------------------------------------------
# Shared alert-transition helper
# ---------------------------------------------------------------------------


def _format_over_threshold(metrics: list[tuple[str, float]], threshold: int) -> str:
	"""Format a comma-joined "LABEL value%" list for whichever metrics are
	actually at/over threshold, e.g. "CPU 95%, RAM 89%" — so a resource
	alert names only the metric(s) that actually triggered it, not every
	metric that was checked regardless of whether it's the problem.
	"""
	return ", ".join(f"{label} {round(value)}%" for label, value in metrics if value >= threshold)


def _track_severity(doc, value: float, thresholds: Thresholds) -> tuple[bool, bool]:
	"""Compute two-phase (Warning/Critical) severity for one reading,
	updating ``doc.warning_streak``/``doc.critical_streak`` in place.

	Neither tier is immediate: a reading only counts once it's stayed at
	or above the relevant line continuously (no dip below it, not even
	for one cycle) for ``thresholds.confirmation_checks`` consecutive
	polls. The two streaks are tracked independently — ``warning_streak``
	is keyed off ``warning_percent`` alone, not "below critical", so a
	reading that's already critical has plainly also been at-or-above the
	warning line the whole time it's been critical, and escalating to
	critical doesn't reset it; dropping back out of critical into the
	warning band keeps whatever count it already had rather than
	restarting from zero.

	Sets ``doc.warning_streak``/``doc.critical_streak`` as plain in-memory
	attributes rather than writing them straight to the database — every
	caller holds a document it's about to ``.save()`` itself, and a direct
	DB write here would corrupt that save the same way an earlier direct
	write to ``backup_critical`` did (see ``_handle_backup_task_result``'s
	note).

	Returns ``(is_critical, is_warning)`` — the two are mutually
	exclusive, so a caller never needs to check both.
	"""
	if value >= thresholds.critical_percent:
		doc.critical_streak = (doc.critical_streak or 0) + 1
	else:
		doc.critical_streak = 0

	if value >= thresholds.warning_percent:
		doc.warning_streak = (doc.warning_streak or 0) + 1
	else:
		doc.warning_streak = 0

	is_critical = doc.critical_streak >= thresholds.confirmation_checks
	is_warning = doc.warning_streak >= thresholds.confirmation_checks

	return is_critical, is_warning and not is_critical


def _handle_transition(
	was_critical: bool,
	is_critical: bool,
	doctype: str,
	name: str,
	alert_type: str,
	message: str,
	recovery_message: str | None = None,
	notify_global: bool = True,
) -> None:
	"""Dispatch an alert on False->True, resolve it on True->False. No-op otherwise.

	Shared by server/guest/datastore critical-resource checks so "only
	alert once per incident" is implemented in exactly one place.

	Also self-heals a steady-state critical condition with no tracked open
	incident (see ``has_open_alert``) by dispatching then too — this is the
	one case a pure edge-trigger can never recover from on its own.

	``recovery_message`` is opt-in: pass it (only from "Critical Resource"
	call sites, per this app's decision not to notify on Resource Warning
	recovering) to also send a "back to normal" notification once the
	condition clears.

	``notify_global`` gates whether Proxmox Monitor Settings' global
	recipient lists get notified (see dispatch.py's _merge_contacts) —
	individual Alert Subscriptions are notified either way. Only guest-level
	calls ever pass False (non-Production servers); every other caller
	(server/datastore/offline/backup) keeps the default.
	"""
	if is_critical and not was_critical:
		dispatch_alert(alert_type, doctype, name, message, notify_global=notify_global)
	elif was_critical and not is_critical:
		resolve_alert(doctype, name, alert_type)
		if recovery_message:
			dispatch_recovery(alert_type, doctype, name, recovery_message, notify_global=notify_global)
	elif is_critical and was_critical and not has_open_alert(doctype, name, alert_type):
		dispatch_alert(alert_type, doctype, name, message, notify_global=notify_global)
