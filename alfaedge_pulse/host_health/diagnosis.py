# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Turns the stored performance samples of one host into plain-language
findings: what is wrong, the numbers behind it, and what to do about it.

``evaluate`` is a pure function over already-loaded rows (so the rules can be
tested with synthetic numbers); ``diagnose_host`` loads the rows. Findings
are read on demand by the Diagnose view and do not raise alerts by
themselves; alerting stays with the existing Host Health alert types.

Thresholds are constants for now. They move into a settings doctype together
with the MariaDB rules, which need the same kind of tuning.
"""

from __future__ import annotations

import frappe
from frappe.utils import add_to_date, flt, now_datetime

# Memory
MEM_AVAILABLE_WARN_PERCENT = 10
MEM_AVAILABLE_CRIT_PERCENT = 5
SWAP_USED_WARN_PERCENT = 80
SWAP_RATE_WARN_PAGES_S = 100
SWAP_RATE_CRIT_PAGES_S = 1000
CGROUP_MEM_WARN_RATIO = 0.90
CGROUP_MEM_CRIT_RATIO = 0.97
OOM_LOOKBACK_MINUTES = 60
# Container CPU limit
THROTTLE_WARN_PERCENT = 10
THROTTLE_CRIT_PERCENT = 30
# Pressure (PSI avg10, percent of time tasks were stalled)
PSI_IO_SOME_WARN = 25
PSI_IO_FULL_CRIT = 10
PSI_MEMORY_SOME_WARN = 20
PSI_MEMORY_FULL_CRIT = 5
PSI_CPU_SOME_WARN = 50
# Disk
DISK_UTIL_WARN_PERCENT = 90
DISK_AWAIT_WARN_MS = 30
#: A condition has to hold over this many consecutive samples, so one noisy
#: reading does not produce a finding.
SUSTAINED_SAMPLES = 3

SEVERITY_ORDER = {"critical": 0, "warning": 1}


def _valid(rows):
	"""Rows whose per-second figures were really computed (see rates_valid)."""
	return [r for r in rows if r.get("rates_valid", 1)]


def _sustained(rows, field):
	"""The lowest value over the last SUSTAINED_SAMPLES rows: a condition only
	counts when every one of them reaches it. None until there are enough."""
	values = [flt(r.get(field)) for r in rows if r.get(field) is not None][:SUSTAINED_SAMPLES]
	return min(values) if len(values) >= SUSTAINED_SAMPLES else None


def _all(rows, field, test):
	"""True when at least SUSTAINED_SAMPLES rows have the field and all pass."""
	values = [r.get(field) for r in rows if r.get(field) is not None]
	return len(values) >= SUSTAINED_SAMPLES and all(test(flt(v)) for v in values[:SUSTAINED_SAMPLES])


def _finding(fid, severity, title, detail, advice):
	return {"id": fid, "severity": severity, "title": title, "detail": detail, "advice": advice}


def _busiest_device(io_latest):
	return max(io_latest, key=lambda d: flt(d.get("util_percent")), default=None)


def evaluate(memory_rows: list[dict], io_by_device: dict[str, list[dict]], oom_kills_recent: int = 0) -> list[dict]:
	"""``memory_rows`` newest first. ``io_by_device`` maps a device to its
	rows newest first."""
	findings: list[dict] = []
	if not memory_rows:
		return findings
	latest = memory_rows[0]
	recent = memory_rows[:SUSTAINED_SAMPLES]

	# Out-of-memory kills are the clearest sign of all.
	if oom_kills_recent:
		findings.append(
			_finding(
				"oom_kill",
				"critical",
				"Processes were killed for lack of memory",
				f"Linux killed a process {oom_kills_recent} time(s) in the last {OOM_LOOKBACK_MINUTES} minutes.",
				"Give the guest more memory or reduce what the largest processes use. Check the kernel log to see which process was killed.",
			)
		)

	total, available = flt(latest.get("mem_total_mb")), flt(latest.get("mem_available_mb"))
	if total:
		pct_available = available / total * 100
		tops = latest.get("top_processes") or []
		biggest = f" Largest process: {tops[0]['name']} using {tops[0]['rss_mb']:.0f} MB." if tops else ""
		if pct_available < MEM_AVAILABLE_CRIT_PERCENT or pct_available < MEM_AVAILABLE_WARN_PERCENT:
			severity = "critical" if pct_available < MEM_AVAILABLE_CRIT_PERCENT else "warning"
			findings.append(
				_finding(
					"memory_low",
					severity,
					"Little memory left",
					f"{available:.0f} MB of {total:.0f} MB is available ({pct_available:.0f}%).{biggest}",
					"Add memory, or lower what the largest processes use (for a database host, check the buffer pool size).",
				)
			)

	swap_rates = [flt(r.get("swap_in_per_s")) + flt(r.get("swap_out_per_s")) for r in _valid(recent)]
	if len(swap_rates) >= SUSTAINED_SAMPLES:
		rate = min(swap_rates)
		if rate >= SWAP_RATE_WARN_PAGES_S:
			findings.append(
				_finding(
					"swapping",
					"critical" if rate >= SWAP_RATE_CRIT_PAGES_S else "warning",
					"The guest is swapping",
					f"At least {rate:.0f} pages per second have been moving in and out of swap for the last {SUSTAINED_SAMPLES} samples. Memory that was pushed out has to be read back from disk, which slows everything.",
					"Memory is too small for the load. Add memory or reduce usage; swapping on a database host usually shows up as slow queries.",
				)
			)
	swap_total = flt(latest.get("swap_total_mb"))
	if swap_total and flt(latest.get("swap_used_mb")) / swap_total * 100 >= SWAP_USED_WARN_PERCENT:
		findings.append(
			_finding(
				"swap_full",
				"warning",
				"Swap is nearly full",
				f"{flt(latest.get('swap_used_mb')):.0f} MB of {swap_total:.0f} MB swap is in use.",
				"Free memory or add swap. A full swap leads to out-of-memory kills.",
			)
		)

	limit, current = flt(latest.get("cgroup_mem_limit_mb")), flt(latest.get("cgroup_mem_current_mb"))
	if limit and current / limit >= CGROUP_MEM_WARN_RATIO:
		findings.append(
			_finding(
				"container_memory",
				"critical" if current / limit >= CGROUP_MEM_CRIT_RATIO else "warning",
				"Container memory limit nearly reached",
				f"{current:.0f} MB of the {limit:.0f} MB limit set for this container is in use.",
				"Raise the container's memory limit in Proxmox, or lower usage inside it.",
			)
		)

	throttled = _sustained(_valid(recent), "cpu_throttled_percent")
	if throttled is not None and throttled >= THROTTLE_WARN_PERCENT:
		cores = latest.get("cpu_limit_cores")
		findings.append(
			_finding(
				"cpu_throttled",
				"critical" if throttled >= THROTTLE_CRIT_PERCENT else "warning",
				"The container's CPU limit is holding it back",
				f"For the last {SUSTAINED_SAMPLES} samples the CPU was held back at least {throttled:.0f}% of the time" + (f" by a {cores} core limit." if cores else "."),
				"Raise the container's CPU limit in Proxmox if the machine is slow.",
			)
		)

	io_latest = [rows[0] for rows in io_by_device.values() if rows]
	busiest = _busiest_device(io_latest)
	where = (
		f" Busiest disk: {busiest['device']}, {flt(busiest.get('util_percent')):.0f}% busy with {flt(busiest.get('await_ms')):.0f} ms average wait."
		if busiest
		else ""
	)
	io_full = _sustained(recent, "psi_io_full")
	io_some = _sustained(recent, "psi_io_some")
	if io_full is not None and io_full >= PSI_IO_FULL_CRIT:
		findings.append(
			_finding(
				"io_stall",
				"critical",
				"Everything is waiting on the disk",
				f"For the last {SUSTAINED_SAMPLES} samples, all tasks on this guest were stalled waiting for disk at least {io_full:.0f}% of the time.{where}",
				"Storage is the bottleneck. Check which guest on the node is using the disk, and whether a disk or I/O limit applies. For MariaDB see the database section.",
			)
		)
	elif io_some is not None and io_some >= PSI_IO_SOME_WARN:
		findings.append(
			_finding(
				"io_waiting",
				"warning",
				"Tasks are waiting on the disk",
				f"For the last {SUSTAINED_SAMPLES} samples, some tasks were stalled waiting for disk at least {io_some:.0f}% of the time.{where}",
				"Disk is slowing things down. Compare with the busiest disk below and with other guests on the same node.",
			)
		)

	for device, rows in io_by_device.items():
		if len(rows) >= SUSTAINED_SAMPLES and _all(rows, "util_percent", lambda v: v >= DISK_UTIL_WARN_PERCENT) and _all(
			rows, "await_ms", lambda v: v >= DISK_AWAIT_WARN_MS
		):
			findings.append(
				_finding(
					f"disk_saturated_{device}",
					"warning",
					f"Disk {device} is saturated",
					f"{flt(rows[0].get('util_percent')):.0f}% busy with {flt(rows[0].get('await_ms')):.0f} ms average wait over the last {SUSTAINED_SAMPLES} samples. This disk is shared with other guests on the node.",
					"Find the heaviest user of this disk (another guest, a backup, a scrub) or move the load.",
				)
			)

	mem_full = _sustained(recent, "psi_memory_full")
	mem_some = _sustained(recent, "psi_memory_some")
	if mem_full is not None and mem_full >= PSI_MEMORY_FULL_CRIT:
		findings.append(
			_finding("memory_stall", "critical", "Everything is waiting on memory", f"All tasks were stalled for memory {mem_full:.0f}% of the time.", "Add memory or reduce usage.")
		)
	elif mem_some is not None and mem_some >= PSI_MEMORY_SOME_WARN:
		findings.append(
			_finding("memory_waiting", "warning", "Tasks are waiting on memory", f"Some tasks were stalled for memory {mem_some:.0f}% of the time.", "Memory is tight. Check swapping and the largest processes.")
		)

	cpu_some = _sustained(recent, "psi_cpu_some")
	if cpu_some is not None and cpu_some >= PSI_CPU_SOME_WARN:
		findings.append(
			_finding("cpu_contention", "warning", "Tasks are waiting for CPU", f"Some tasks waited for CPU {cpu_some:.0f}% of the time.", "The guest has more work than CPU. Check the container's CPU limit and the node's load.")
		)

	findings.sort(key=lambda f: SEVERITY_ORDER[f["severity"]])
	return findings


def _load_top_processes(row: dict) -> None:
	raw = row.get("top_processes")
	if isinstance(raw, str) and raw:
		try:
			row["top_processes"] = frappe.parse_json(raw)
		except Exception:
			row["top_processes"] = []


MEMORY_FIELDS = [
	"collected_at",
	"rates_valid",
	"mem_total_mb",
	"mem_available_mb",
	"mem_used_percent",
	"cached_mb",
	"slab_mb",
	"swap_total_mb",
	"swap_used_mb",
	"swap_in_per_s",
	"swap_out_per_s",
	"oom_kills_since_last",
	"cgroup_oom_kills_since_last",
	"cgroup_mem_limit_mb",
	"cgroup_mem_current_mb",
	"cpu_limit_cores",
	"cpu_throttled_percent",
	"psi_cpu_some",
	"psi_memory_some",
	"psi_memory_full",
	"psi_io_some",
	"psi_io_full",
	"top_processes",
]
IO_FIELDS = ["device", "collected_at", "rates_valid", "read_iops", "write_iops", "read_mb_s", "write_mb_s", "await_ms", "util_percent", "avg_queue"]


def diagnose_host(monitored_host: str) -> dict:
	"""Loads the recent samples of one host and evaluates the rules."""
	memory_rows = frappe.get_all(
		"Memory Metric Log",
		filters={"monitored_host": monitored_host},
		fields=MEMORY_FIELDS,
		order_by="collected_at desc",
		limit_page_length=SUSTAINED_SAMPLES,
		ignore_permissions=True,
	)
	for row in memory_rows:
		_load_top_processes(row)

	since = add_to_date(now_datetime(), minutes=-OOM_LOOKBACK_MINUTES)
	kills = frappe.db.sql(
		"""SELECT COALESCE(SUM(oom_kills_since_last), 0) + COALESCE(SUM(cgroup_oom_kills_since_last), 0)
		FROM `tabMemory Metric Log` WHERE monitored_host = %s AND collected_at >= %s""",
		(monitored_host, since),
	)[0][0]

	io_rows = frappe.get_all(
		"Disk IO Sample",
		filters={
			"monitored_host": monitored_host,
			"rates_valid": 1,
			"collected_at": [">=", add_to_date(now_datetime(), minutes=-10)],
		},
		fields=IO_FIELDS,
		order_by="collected_at desc",
		limit_page_length=0,
		ignore_permissions=True,
	)
	io_by_device: dict[str, list[dict]] = {}
	for row in io_rows:
		rows = io_by_device.setdefault(row["device"], [])
		if len(rows) < SUSTAINED_SAMPLES:
			rows.append(row)

	return {
		"findings": evaluate(memory_rows, io_by_device, int(kills or 0)),
		"latest": memory_rows[0] if memory_rows else None,
		"io_latest": [rows[0] for rows in io_by_device.values() if rows],
		"has_data": bool(memory_rows),
	}
