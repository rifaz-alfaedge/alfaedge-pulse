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

	io_latest = [rows[0] for rows in io_by_device.values() if rows]
	mariadb = diagnose_mariadb(monitored_host, memory_rows[0] if memory_rows else None, io_latest)
	redis = diagnose_redis(monitored_host, memory_rows[0] if memory_rows else None)
	findings = evaluate(memory_rows, io_by_device, int(kills or 0)) + mariadb["findings"] + redis["findings"]
	findings.sort(key=lambda f: SEVERITY_ORDER[f["severity"]])
	return {
		"findings": findings,
		"latest": memory_rows[0] if memory_rows else None,
		"io_latest": io_latest,
		"has_data": bool(memory_rows) or mariadb["has_data"] or redis["has_data"],
		"mariadb": {k: v for k, v in mariadb.items() if k != "findings"},
		"redis": {k: v for k, v in redis.items() if k != "findings"},
	}


# ---------------------------------------------------------------------------
# MariaDB
# ---------------------------------------------------------------------------

DB_MIN_UPTIME_SECONDS = 1800  # a freshly started server has a cold cache
DB_HIT_WARN_PERCENT = 99
DB_HIT_CRIT_PERCENT = 95
DB_MIN_DISK_READS_PER_S = 20  # below this a low hit ratio changes nothing
#: A burst (a heavy job for a minute or two) is not a cache problem, so the
#: hit ratio is judged over this many samples (about 5 minutes), most of which
#: must be bad.
DB_CACHE_WINDOW = 10
DB_CACHE_MIN_SHARE = 0.8
DB_CHECKPOINT_WARN_PERCENT = 75
DB_CHECKPOINT_CRIT_PERCENT = 90
DB_DIRTY_WARN_FRACTION = 0.75  # of innodb_max_dirty_pages_pct
DB_FLUSH_AT_CAPACITY = 0.9
DB_DISK_BUSY_OK_PERCENT = 60
DB_TMP_DISK_WARN_PERCENT = 25
DB_TMP_DISK_MIN_PER_S = 0.2
DB_CONNECTIONS_WARN_RATIO = 0.8
DB_CONNECTIONS_CRIT_RATIO = 0.95
DB_HISTORY_WARN = 1_000_000
DB_HISTORY_CRIT = 5_000_000
DB_SWAP_WARN_MB = 50
DB_SWAP_CRIT_MB = 500
DB_ROW_LOCK_WAITING_WARN = 5
DB_ROW_LOCK_AVG_WAIT_WARN_MS = 1000
DB_TABLE_OPENS_WARN_PER_S = 5
DB_OPEN_FILES_WARN_RATIO = 0.8
DB_SLOW_QUERIES_WARN_PER_S = 1
POOL_SHARE_OF_FREE_RAM = 0.7  # never suggest a pool bigger than this share of the guest's memory
RAM_RESERVED_MB = 1024


def _mb(value):
	return f"{flt(value):,.0f} MB" if flt(value) < 10240 else f"{flt(value) / 1024:,.1f} GB"


def _round_to(value, step=64):
	return int(round(flt(value) / step) * step)


def _min_over(rows, field):
	"""The lowest value across the rows (all must have it): a problem counts
	only when every recent sample shows it."""
	values = [flt(r.get(field)) for r in rows if r.get(field) is not None]
	return min(values) if len(values) == len(rows) and values else None


def _max_over(rows, field):
	"""The highest value across the rows (all must have it). A ratio that is
	LOW in every recent sample is one whose best sample is still low."""
	values = [flt(r.get(field)) for r in rows if r.get(field) is not None]
	return max(values) if len(values) == len(rows) and values else None


def evaluate_mariadb(
	rows: list[dict],
	config: dict | None,
	sizes: dict | None,
	memory: dict | None,
	io_latest: list[dict] | None,
	process_limit: int | None = None,
) -> list[dict]:
	"""``rows``: MariaDB Metric Log rows, newest first. ``config``: the
	variables of the latest snapshot. ``sizes``: the latest snapshot that read
	data sizes. ``memory``: the latest Memory Metric Log row. ``io_latest``:
	latest disk samples. Findings carry the numbers and a concrete next step."""
	findings: list[dict] = []
	valid = [r for r in rows if r.get("rates_valid")]
	if not rows:
		return findings
	latest = rows[0]
	recent = valid[:SUSTAINED_SAMPLES]
	have_recent = len(recent) >= SUSTAINED_SAMPLES
	config = config or {}
	sizes = sizes or {}
	memory = memory or {}
	pool_mb = flt(latest.get("bp_size_mb"))
	data_mb = flt(sizes.get("data_mb")) + flt(sizes.get("index_mb"))
	ram_mb = flt(memory.get("mem_total_mb"))
	uptime = flt(latest.get("uptime_s"))

	# Queries stalling because there is no free page to read into.
	if have_recent and sum(flt(r.get("bp_wait_free")) for r in recent) > 0:
		findings.append(
			_finding(
				"db_wait_free",
				"critical",
				"MariaDB ran out of free memory pages",
				f"Queries had to wait for the buffer pool to flush pages, {int(sum(flt(r.get('bp_wait_free')) for r in recent))} time(s) in the last {len(recent)} samples. The pool is {_mb(pool_mb)} and {flt(latest.get('bp_dirty_percent')):.0f}% of it is waiting to be written.",
				"Flushing cannot keep up with writes. Raise innodb_io_capacity if the disk has room (see the other findings), and enlarge innodb_buffer_pool_size if there is free memory.",
			)
		)

	# Cache misses reaching the disk, sustained over a longer window than the
	# other rules: a short burst of reads is normal for a heavy job.
	window = valid[:DB_CACHE_WINDOW]
	hit = disk_reads = None
	if len(window) >= DB_CACHE_WINDOW:
		bad = [
			r for r in window
			if r.get("bp_hit_percent") is not None
			and flt(r.get("bp_hit_percent")) < DB_HIT_WARN_PERCENT
			and flt(r.get("bp_disk_reads_per_s")) >= DB_MIN_DISK_READS_PER_S
		]
		if len(bad) >= len(window) * DB_CACHE_MIN_SHARE:
			hit = sum(flt(r.get("bp_hit_percent")) for r in bad) / len(bad)
			disk_reads = sum(flt(r.get("bp_disk_reads_per_s")) for r in bad) / len(bad)
	if hit is not None and uptime >= DB_MIN_UPTIME_SECONDS:
		suggest = None
		if data_mb and ram_mb:
			suggest = _round_to(min(data_mb * 1.1, max(ram_mb * POOL_SHARE_OF_FREE_RAM, ram_mb - RAM_RESERVED_MB * 2)))
		if suggest and suggest > pool_mb * 1.1:
			advice = f"The data is larger than the pool. Raise innodb_buffer_pool_size toward {_mb(suggest)} (about {_mb(ram_mb)} of memory on this guest, so leave room for everything else)."
		elif pool_mb and data_mb and pool_mb >= data_mb:
			advice = "The pool already holds all the data, so the misses come from queries scanning large tables. Look at slow queries and missing indexes rather than memory."
		else:
			advice = "Raise innodb_buffer_pool_size if the guest has free memory; otherwise look at what is scanning large tables."
		findings.append(
			_finding(
				"db_cache_misses",
				"critical" if hit < DB_HIT_CRIT_PERCENT else "warning",
				"MariaDB is reading from disk too often",
				f"Over the last {len(window)} samples (about {len(window) // 2} minutes) only {hit:.1f}% of page reads came from memory and about {flt(disk_reads):.0f} per second went to disk. The buffer pool is {_mb(pool_mb)}"
				+ (f" for about {_mb(data_mb)} of data and indexes on this guest" if data_mb else "")
				+ ".",
				advice,
			)
		)

	# The redo log filling up forces hard flushing.
	checkpoint = _min_over(recent, "checkpoint_age_percent") if have_recent else None
	if checkpoint is not None and checkpoint >= DB_CHECKPOINT_WARN_PERCENT:
		log_mb = flt(latest.get("log_file_size_mb"))
		findings.append(
			_finding(
				"db_checkpoint",
				"critical" if checkpoint >= DB_CHECKPOINT_CRIT_PERCENT else "warning",
				"MariaDB's redo log is nearly full",
				f"The redo log is {checkpoint:.0f}% full before InnoDB must flush hard, which stalls writes. innodb_log_file_size is {_mb(log_mb)}.",
				f"Raise innodb_log_file_size (for example to {_mb(log_mb * 2)}) so flushing can be spread out, and check innodb_io_capacity.",
			)
		)

	# Dirty pages piling up.
	max_dirty = flt(config.get("innodb_max_dirty_pages_pct")) or 90
	dirty = _min_over(recent, "bp_dirty_percent") if have_recent else None
	if dirty is not None and dirty >= max_dirty * DB_DIRTY_WARN_FRACTION:
		findings.append(
			_finding(
				"db_dirty_pages",
				"warning",
				"Many changed pages are waiting to be written",
				f"{dirty:.0f}% of the buffer pool is changed pages not yet on disk (the limit is {max_dirty:.0f}%).",
				"Writes are arriving faster than they are flushed. See whether flushing is held back by innodb_io_capacity or by the disk.",
			)
		)

	# InnoDB is throttled by its own setting, not by the disk.
	capacity = flt(latest.get("io_capacity"))
	flushed = _min_over(recent, "pages_flushed_per_s") if have_recent else None
	busiest = max((flt(d.get("util_percent")) for d in (io_latest or [])), default=None)
	under_pressure = (dirty or 0) >= 50 or (checkpoint or 0) >= 50
	if capacity and flushed is not None and flushed >= capacity * DB_FLUSH_AT_CAPACITY and under_pressure and (busiest is None or busiest < DB_DISK_BUSY_OK_PERCENT):
		cap_max = flt(latest.get("io_capacity_max"))
		target = min(capacity * 2, cap_max) if cap_max else capacity * 2
		findings.append(
			_finding(
				"db_io_capacity",
				"warning",
				"MariaDB is limited by its own I/O setting, not the disk",
				f"It is flushing about {flushed:.0f} pages per second, right at innodb_io_capacity ({capacity:.0f}), while changed pages and the redo log keep filling up"
				+ (f". The busiest disk is only {busiest:.0f}% busy." if busiest is not None else "."),
				f"Raise innodb_io_capacity to about {target:.0f} (innodb_io_capacity_max is {cap_max:.0f}) and watch the disk's busy percentage.",
			)
		)

	# Temporary tables spilling to disk.
	tmp_pct = _min_over(recent, "tmp_disk_percent") if have_recent else None
	tmp_rate = _min_over(recent, "tmp_disk_per_s") if have_recent else None
	if tmp_pct is not None and tmp_pct >= DB_TMP_DISK_WARN_PERCENT and flt(tmp_rate) >= DB_TMP_DISK_MIN_PER_S:
		tmp_size = flt(config.get("tmp_table_size")) / MB if config.get("tmp_table_size") else None
		findings.append(
			_finding(
				"db_tmp_tables",
				"warning",
				"Temporary tables are being written to disk",
				f"{tmp_pct:.0f}% of temporary tables spill to disk, about {flt(tmp_rate):.1f} per second."
				+ (f" tmp_table_size is {_mb(tmp_size)}." if tmp_size else ""),
				"Raise tmp_table_size and max_heap_table_size together (try double). If it persists, look for queries that sort or group large results without an index.",
			)
		)

	# Connection limit.
	limit = flt(latest.get("max_connections"))
	connected = flt(latest.get("threads_connected"))
	refused = sum(flt(r.get("connection_limit_errors")) for r in recent) if recent else 0
	if limit and (connected / limit >= DB_CONNECTIONS_WARN_RATIO or refused > 0):
		findings.append(
			_finding(
				"db_connections",
				"critical" if connected / limit >= DB_CONNECTIONS_CRIT_RATIO or refused > 0 else "warning",
				"MariaDB is close to its connection limit" if not refused else "MariaDB refused connections at its limit",
				f"{connected:.0f} of {limit:.0f} connections are in use ({flt(latest.get('threads_running')):.0f} running)."
				+ (f" {refused:.0f} connection(s) were refused for being over the limit." if refused else ""),
				"Find what holds connections open (idle web workers, long jobs) before raising max_connections; each connection uses memory.",
			)
		)

	# Purge lag from long transactions.
	history = flt(latest.get("history_list_length"))
	if history >= DB_HISTORY_WARN:
		oldest = latest.get("oldest_trx_s")
		findings.append(
			_finding(
				"db_history",
				"critical" if history >= DB_HISTORY_CRIT else "warning",
				"MariaDB cannot clean up old row versions",
				f"The history list holds {history:,.0f} entries, so a long transaction is keeping old versions alive."
				+ (f" The oldest open transaction has run {int(oldest)} seconds." if oldest else ""),
				"Find the long-running transaction or an idle connection with an open transaction and end it. Until then queries get slower and the data files grow."
				+ ("" if oldest is not None else " This login cannot see open transactions; grant PROCESS to see which one."),
			)
		)

	# The database itself in swap.
	swap = flt(latest.get("mariadbd_swap_mb"))
	if swap >= DB_SWAP_WARN_MB:
		findings.append(
			_finding(
				"db_swapped",
				"critical" if swap >= DB_SWAP_CRIT_MB else "warning",
				"Part of MariaDB's memory is in swap",
				f"{_mb(swap)} of MariaDB's {_mb(latest.get('mariadbd_rss_mb'))} in memory has been moved to swap. Reading it back from disk makes queries slow.",
				"Reduce innodb_buffer_pool_size or other memory use, or add memory to the guest.",
			)
		)

	# Waiting for the log buffer.
	waits = sum(flt(r.get("log_waits")) for r in recent) if have_recent else 0
	if waits > 0:
		buf = flt(config.get("innodb_log_buffer_size")) / MB if config.get("innodb_log_buffer_size") else None
		findings.append(
			_finding(
				"db_log_buffer",
				"warning",
				"Transactions waited for the log buffer",
				f"{int(waits)} wait(s) in the last {len(recent)} samples." + (f" innodb_log_buffer_size is {_mb(buf)}." if buf else ""),
				"Raise innodb_log_buffer_size (for example to double), especially if large transactions are common.",
			)
		)

	# Row lock contention.
	waiting = _min_over(recent, "row_lock_current_waits") if have_recent else None
	avg_wait = flt(latest.get("row_lock_avg_wait_ms"))
	if (waiting is not None and waiting >= DB_ROW_LOCK_WAITING_WARN) or (avg_wait >= DB_ROW_LOCK_AVG_WAIT_WARN_MS and flt(latest.get("row_lock_waits_per_s")) >= 0.1):
		findings.append(
			_finding(
				"db_row_locks",
				"warning",
				"Queries are waiting on row locks",
				f"{flt(latest.get('row_lock_current_waits')):.0f} waiting now, average wait {avg_wait:.0f} ms, {flt(latest.get('row_lock_waits_per_s')):.1f} waits per second.",
				"Look for long transactions or batch updates holding locks. Keep transactions short.",
			)
		)

	# Table cache too small.
	opens = _min_over(recent, "table_opens_per_s") if have_recent else None
	if opens is not None and opens >= DB_TABLE_OPENS_WARN_PER_S:
		cache = config.get("table_open_cache")
		findings.append(
			_finding(
				"db_table_cache",
				"warning",
				"MariaDB keeps reopening tables",
				f"About {opens:.1f} tables are reopened per second" + (f"; table_open_cache is {cache}." if cache else "."),
				"Raise table_open_cache (and table_definition_cache), and check the open-files limit allows it.",
			)
		)

	# Open files near the limit.
	files, files_limit = flt(latest.get("open_files")), flt(latest.get("open_files_limit"))
	if process_limit:
		files_limit = min(files_limit, flt(process_limit)) if files_limit else flt(process_limit)
	if files_limit and files / files_limit >= DB_OPEN_FILES_WARN_RATIO:
		findings.append(
			_finding(
				"db_open_files",
				"warning",
				"MariaDB is close to its open files limit",
				f"{files:.0f} of {files_limit:.0f} files are open.",
				"Raise LimitNOFILE for the mariadb service and open_files_limit, or lower table_open_cache.",
			)
		)

	# Slow queries.
	slow = _min_over(recent, "slow_queries_per_s") if have_recent else None
	if slow is not None and slow >= DB_SLOW_QUERIES_WARN_PER_S:
		findings.append(
			_finding(
				"db_slow_queries",
				"warning",
				"MariaDB is logging many slow queries",
				f"About {slow:.1f} queries per second take longer than long_query_time ({config.get('long_query_time', '?')} s).",
				"Turn on slow_query_log (it is " + str(config.get("slow_query_log", "unknown")) + ") and review the slowest queries for missing indexes.",
			)
		)

	return findings


MARIADB_LOG_FIELDS = [
	"collected_at", "rates_valid", "uptime_s", "threads_connected", "threads_running", "max_connections",
	"connections_per_s", "connection_limit_errors", "bp_size_mb", "bp_data_mb", "bp_dirty_percent",
	"bp_free_percent", "bp_hit_percent", "bp_disk_reads_per_s", "bp_wait_free", "data_reads_per_s",
	"data_writes_per_s", "data_fsyncs_per_s", "pages_flushed_per_s", "pending_reads", "pending_writes",
	"pending_fsyncs", "checkpoint_age_percent", "log_waits", "io_capacity", "io_capacity_max", "log_file_size_mb",
	"history_list_length", "oldest_trx_s", "trx_count", "row_lock_current_waits", "row_lock_waits_per_s",
	"row_lock_avg_wait_ms", "deadlocks", "queries_per_s", "slow_queries_per_s", "tmp_disk_percent",
	"tmp_disk_per_s", "full_joins_per_s", "sort_merge_passes_per_s", "table_opens_per_s",
	"table_locks_waited_per_s", "open_files", "open_files_limit", "open_tables", "mariadbd_rss_mb", "mariadbd_swap_mb",
]
MB = 1048576


def diagnose_mariadb(monitored_host: str, memory_latest: dict | None, io_latest: list[dict] | None) -> dict:
	"""Loads the recent MariaDB samples of one host and evaluates the rules."""
	rows = frappe.get_all(
		"MariaDB Metric Log",
		filters={"monitored_host": monitored_host},
		fields=MARIADB_LOG_FIELDS,
		order_by="collected_at desc",
		limit_page_length=DB_CACHE_WINDOW + 2,
		ignore_permissions=True,
	)
	snapshots = frappe.get_all(
		"MariaDB Config Snapshot",
		filters={"monitored_host": monitored_host},
		fields=["collected_at", "variables", "data_mb", "index_mb", "free_mb", "sites_counted", "sites_total", "by_site", "top_tables", "limited", "version"],
		order_by="collected_at desc",
		limit_page_length=20,
		ignore_permissions=True,
	)
	config = {}
	if snapshots and snapshots[0].get("variables"):
		try:
			config = frappe.parse_json(snapshots[0]["variables"])
		except Exception:
			config = {}
	sizes = next((s for s in snapshots if s.get("sites_counted")), None)
	if sizes:
		for key in ("by_site", "top_tables"):
			try:
				sizes[key] = frappe.parse_json(sizes[key]) if sizes.get(key) else []
			except Exception:
				sizes[key] = []
	return {
		"findings": evaluate_mariadb(rows, config, sizes, memory_latest, io_latest),
		"latest": rows[0] if rows else None,
		"config": config,
		"sizes": sizes,
		"limited": [x.strip() for x in (snapshots[0].get("limited") or "").split(",") if x.strip()] if snapshots else [],
		"has_data": bool(rows),
	}


# ---------------------------------------------------------------------------
# Redis
# ---------------------------------------------------------------------------

REDIS_MEMORY_WARN_RATIO = 0.90
REDIS_MEMORY_CRIT_RATIO = 0.97
REDIS_UNBOUNDED_WARN_RAM_SHARE = 0.40
REDIS_UNBOUNDED_CRIT_RAM_SHARE = 0.60
REDIS_FRAGMENTATION_WARN = 1.5
REDIS_FRAGMENTATION_MIN_RSS_MB = 200  # a ratio on a tiny instance means nothing
REDIS_SWAPPED_MIN_USED_MB = 50
REDIS_CACHE_HIT_WARN_PERCENT = 80
REDIS_CACHE_MIN_OPS_PER_S = 5
REDIS_CLIENTS_WARN_RATIO = 0.8
REDIS_FORK_WARN_MS = 1000
REDIS_SLOW_COMMAND_WARN_MS = 100

REDIS_LOG_FIELDS = [
	"collected_at", "rates_valid", "instance", "roles", "uptime_s", "maxmemory_policy", "used_mb", "rss_mb",
	"maxmemory_mb", "memory_percent", "fragmentation_ratio", "keys", "ops_per_s", "hit_percent", "evicted",
	"evicted_per_s", "expired_per_s", "connected_clients", "max_clients", "blocked_clients", "rejected",
	"persistence_ok", "fork_ms", "slowlog_len", "slowlog_recent_count", "slowlog_recent_max_ms", "slowlog_recent_commands",
]


def _redis_name(rows):
	latest = rows[0]
	roles = (latest.get("roles") or "").replace("+", " and ")
	return f"Redis {latest.get('instance')}" + (f" ({roles})" if roles else "")


def evaluate_redis(rows_by_instance: dict[str, list[dict]], memory: dict | None = None) -> list[dict]:
	"""``rows_by_instance`` maps an instance label to its Redis Metric Log rows,
	newest first. ``memory`` is the host's latest Memory Metric Log row, used to
	judge a Redis that has no memory limit against the guest's memory."""
	findings: list[dict] = []
	ram_mb = flt((memory or {}).get("mem_total_mb"))
	for label, rows in rows_by_instance.items():
		if not rows:
			continue
		latest = rows[0]
		name = _redis_name(rows)
		roles = set((latest.get("roles") or "").split("+"))
		is_queue = "queue" in roles
		valid = [r for r in rows if r.get("rates_valid")][:SUSTAINED_SAMPLES]
		sustained = len(valid) >= SUSTAINED_SAMPLES
		policy = latest.get("maxmemory_policy") or "unknown"
		limit_mb, used_mb, rss_mb = flt(latest.get("maxmemory_mb")), flt(latest.get("used_mb")), flt(latest.get("rss_mb"))
		key = label.replace(":", "_")

		evicted = sum(flt(r.get("evicted")) for r in valid) if sustained else 0
		hit = _max_over(valid, "hit_percent") if sustained else None  # low in EVERY sample
		ops = _min_over(valid, "ops_per_s") if sustained else None
		if evicted > 0 and is_queue:
			findings.append(
				_finding(
					f"redis_evict_queue_{key}",
					"critical",
					f"{name} is deleting queued data to free memory",
					f"{int(evicted)} key(s) were evicted in the last {len(valid)} samples on the instance that holds the job queue (policy {policy}, limit {_mb(limit_mb)}, in use {_mb(used_mb)}). Queued background jobs can be lost.",
					"Set maxmemory-policy to noeviction on this instance and raise its memory limit (or remove the limit), so a full queue refuses new jobs instead of dropping existing ones.",
				)
			)
		elif evicted > 0 and hit is not None and hit < REDIS_CACHE_HIT_WARN_PERCENT and flt(ops) >= REDIS_CACHE_MIN_OPS_PER_S:
			findings.append(
				_finding(
					f"redis_cache_small_{key}",
					"warning",
					f"{name} is too small for what it caches",
					f"It evicted {int(evicted)} key(s) in the last {len(valid)} samples and even the best of those samples found only {hit:.0f}% of the keys it was asked for. The limit is {_mb(limit_mb)}.",
					f"Raise maxmemory (try {_mb(limit_mb * 2) if limit_mb else 'a higher value'}) so hot keys stay cached.",
				)
			)
		elif hit is not None and hit < REDIS_CACHE_HIT_WARN_PERCENT and flt(ops) >= REDIS_CACHE_MIN_OPS_PER_S and not is_queue and flt(latest.get("uptime_s")) >= DB_MIN_UPTIME_SECONDS:
			findings.append(
				_finding(
					f"redis_cache_hits_{key}",
					"warning",
					f"{name} finds few of the keys it is asked for",
					f"At best {hit:.0f}% of lookups found their key in each of the last {len(valid)} samples, at about {flt(ops):.0f} commands per second.",
					"Expect this after a restart or a cache clear. If it persists, the application is asking for keys that are not being stored or that expire too soon.",
				)
			)

		if limit_mb:
			ratio = used_mb / limit_mb
			if ratio >= REDIS_MEMORY_WARN_RATIO:
				full_hurts = policy == "noeviction"
				findings.append(
					_finding(
						f"redis_memory_full_{key}",
						"critical" if ratio >= REDIS_MEMORY_CRIT_RATIO and full_hurts else "warning",
						f"{name} is nearly at its memory limit",
						f"{_mb(used_mb)} of {_mb(limit_mb)} is in use ({ratio * 100:.0f}%), policy {policy}.",
						"With noeviction, writes start failing when it is full; raise maxmemory now." if full_hurts else "It will start evicting keys; raise maxmemory if the data should stay cached.",
					)
				)
		elif ram_mb and rss_mb / ram_mb >= REDIS_UNBOUNDED_WARN_RAM_SHARE:
			share = rss_mb / ram_mb
			findings.append(
				_finding(
					f"redis_unbounded_{key}",
					"critical" if share >= REDIS_UNBOUNDED_CRIT_RAM_SHARE else "warning",
					f"{name} has no memory limit and is using a lot of the guest's memory",
					f"It holds {_mb(rss_mb)}, {share * 100:.0f}% of the guest's {_mb(ram_mb)}, and nothing stops it growing.",
					"Set maxmemory to a value that leaves room for MariaDB and the web workers (for a cache, also a policy such as allkeys-lru).",
				)
			)

		frag = flt(latest.get("fragmentation_ratio"))
		if frag and rss_mb >= REDIS_FRAGMENTATION_MIN_RSS_MB and frag >= REDIS_FRAGMENTATION_WARN:
			findings.append(
				_finding(
					f"redis_fragmentation_{key}",
					"warning",
					f"{name} wastes memory to fragmentation",
					f"The OS holds {_mb(rss_mb)} for {_mb(used_mb)} of data (ratio {frag:.1f}).",
					"A restart in a quiet period gives the memory back; enabling activedefrag does it gradually.",
				)
			)
		elif frag and 0 < frag < 1 and used_mb >= REDIS_SWAPPED_MIN_USED_MB:
			findings.append(
				_finding(
					f"redis_swapped_{key}",
					"critical",
					f"Part of {name} is in swap",
					f"Redis holds {_mb(used_mb)} but only {_mb(rss_mb)} is in memory (ratio {frag:.2f}); the rest was pushed to disk.",
					"Free memory on the guest or lower Redis's limit. Redis in swap is very slow.",
				)
			)

		if not latest.get("persistence_ok"):
			findings.append(
				_finding(
					f"redis_persistence_{key}",
					"critical",
					f"{name} cannot save to disk",
					"The last background save (or append-only write) failed, so a restart would lose recent data and Redis may refuse writes.",
					"Check the disk space and the permissions of Redis's data folder, and its log.",
				)
			)

		refused = sum(flt(r.get("rejected")) for r in rows[:SUSTAINED_SAMPLES])
		clients, max_clients = flt(latest.get("connected_clients")), flt(latest.get("max_clients"))
		if refused > 0:
			findings.append(_finding(f"redis_refused_{key}", "critical", f"{name} refused connections", f"{int(refused)} connection(s) were refused in the last samples.", "Raise maxclients and the system's open-files limit, or find what opens so many connections."))
		elif max_clients and clients / max_clients >= REDIS_CLIENTS_WARN_RATIO:
			findings.append(_finding(f"redis_clients_{key}", "warning", f"{name} is close to its client limit", f"{clients:.0f} of {max_clients:.0f} connections are in use.", "Raise maxclients or find what holds connections open."))

		if flt(latest.get("fork_ms")) >= REDIS_FORK_WARN_MS:
			findings.append(
				_finding(
					f"redis_fork_{key}",
					"warning",
					f"{name} stalls while it saves to disk",
					f"The last background save paused Redis for {flt(latest.get('fork_ms')):.0f} ms while it forked, and a large instance on a busy guest does this each time.",
					"Save less often (the save setting), or move to append-only mode. Make sure the guest has memory to spare for the fork.",
				)
			)

		slow = flt(latest.get("slowlog_recent_max_ms"))
		if slow >= REDIS_SLOW_COMMAND_WARN_MS:
			findings.append(
				_finding(
					f"redis_slow_{key}",
					"warning",
					f"{name} ran a slow command",
					f"A command took {slow:.0f} ms in the last two minutes ({latest.get('slowlog_recent_commands') or 'unknown'}). Redis handles one command at a time, so everything waited.",
					"Look for commands that scan large collections (KEYS, large SMEMBERS or LRANGE) and replace them with incremental ones.",
				)
			)
	findings.sort(key=lambda f: SEVERITY_ORDER[f["severity"]])
	return findings


def diagnose_redis(monitored_host: str, memory_latest: dict | None) -> dict:
	rows = frappe.get_all(
		"Redis Metric Log",
		filters={"monitored_host": monitored_host},
		fields=REDIS_LOG_FIELDS,
		order_by="collected_at desc",
		limit_page_length=40,
		ignore_permissions=True,
	)
	by_instance: dict[str, list[dict]] = {}
	for row in rows:
		bucket = by_instance.setdefault(row["instance"], [])
		if len(bucket) < SUSTAINED_SAMPLES + 2:
			bucket.append(row)
	return {
		"findings": evaluate_redis(by_instance, memory_latest),
		"instances": {label: r[0] for label, r in by_instance.items() if r},
		"has_data": bool(rows),
	}
