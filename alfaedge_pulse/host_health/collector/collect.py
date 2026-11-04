# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Read-only host collector. Pulse sends this file over SSH on stdin and
reads one JSON document back on stdout; nothing is installed on the host.

Run as ``<bench>/env/bin/python - '<args-json>'`` when the bench exists (so
``redis``/``rq``/``pymysql`` are importable), otherwise as ``python3 -``.
Standard library only, apart from those three optional imports. It never
writes anything and never prints credentials.

Args (JSON, argv[1]):
	services:   [{"service_name": str, "unit_name": str}, ...]
	bench_path: str | ""      absolute path of the bench on this host
	slow:       bool          also do the heavier, slower-changing reads
	                          (database sizes per site); the rest runs every time

Output (stdout, one JSON object):
	{"collector_version": 1, "collected_at": "...", "errors": [...],
	 "status": {"reported_at", "services", "benches", "sites"},
	 "resources": {"reported_at", "load", "swap", "disks"},
	 "performance": {"reported_at", "memory", "pressure", "cgroup", "counters",
	                 "top_processes", "io"},
	 "mariadb": {"reported_at", "via", "status", "variables", "data", "process",
	             "oldest_trx_s", "trx_count", "limited", "sites_total"},
	 "redis": {"reported_at", "instances": [{"label", "roles", "info", "config", "slowlog"}]}}

``status`` and ``resources`` use exactly the shapes that
``host_health/ingest.py`` processes, so a section that cannot be collected
is simply left out and noted in ``errors``.
"""

import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone

COLLECTOR_VERSION = 4
MAX_FAILED_JOBS_PER_QUEUE = 50
EXC_INFO_LIMIT = 4000
REAL_FS = {"ext2", "ext3", "ext4", "xfs", "btrfs", "zfs", "f2fs", "vfat", "ntfs", "exfat", "jfs", "reiserfs"}
ALLOWED_STATES = {"active", "inactive", "failed", "activating", "deactivating"}

errors = []


def utc_now():
	return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def iso_utc(value):
	"""RQ hands back naive-UTC or aware datetimes depending on version."""
	if not value:
		return None
	if value.tzinfo is None:
		value = value.replace(tzinfo=timezone.utc)
	return value.isoformat(sep=" ")


def run(cmd, timeout=5):
	return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


# --------------------------------------------------------------------------
# services
# --------------------------------------------------------------------------


def collect_services(wanted):
	out = []
	for svc in wanted:
		unit = svc.get("unit_name")
		if not unit:
			continue
		state = "unknown"
		try:
			res = run(["systemctl", "show", unit, "--property=ActiveState", "--value"])
			value = res.stdout.strip()
			state = value if value in ALLOWED_STATES else "unknown"
		except Exception as exc:
			errors.append(f"service {unit}: {exc}")
		out.append(
			{"service_name": svc.get("service_name") or unit, "unit_name": unit, "current_state": state}
		)
	return out


# --------------------------------------------------------------------------
# bench: sites, RQ, scheduler
# --------------------------------------------------------------------------


def read_json(path):
	with open(path) as handle:
		return json.load(handle)


def collect_sites(bench_path):
	sites_dir = os.path.join(bench_path, "sites")
	bench_name = os.path.basename(bench_path.rstrip("/"))
	rows = []
	for name in sorted(os.listdir(sites_dir)):
		if name == "assets" or name.startswith("."):
			continue
		cfg_path = os.path.join(sites_dir, name, "site_config.json")
		if not os.path.isfile(cfg_path):
			continue
		try:
			host_name = read_json(cfg_path).get("host_name")
		except Exception:
			host_name = None
		rows.append({"bench_name": bench_name, "site_name": name, "site_url": host_name or ""})
	return rows


def pid_alive(pid):
	try:
		os.kill(pid, 0)
	except ProcessLookupError:
		return False
	except PermissionError:
		return True  # exists, owned by another user
	return True


def scheduler_last_run(bench_path, common):
	"""Newest Scheduled Job Type execution across this bench's sites.
	Credentials are read locally and used locally; they never leave the host."""
	try:
		import pymysql
	except Exception:
		return None
	newest = None
	sites_dir = os.path.join(bench_path, "sites")
	for name in os.listdir(sites_dir):
		cfg_path = os.path.join(sites_dir, name, "site_config.json")
		if name == "assets" or not os.path.isfile(cfg_path):
			continue
		try:
			cfg = read_json(cfg_path)
			conn = pymysql.connect(
				host=cfg.get("db_host") or common.get("db_host") or "127.0.0.1",
				port=int(cfg.get("db_port") or common.get("db_port") or 3306),
				user=cfg.get("db_user") or cfg["db_name"],
				password=cfg["db_password"],
				database=cfg["db_name"],
				connect_timeout=5,
				read_timeout=5,
			)
			try:
				with conn.cursor() as cur:
					cur.execute("select max(last_execution) from `tabScheduled Job Type` where stopped = 0")
					row = cur.fetchone()
			finally:
				conn.close()
			if row and row[0] and (newest is None or row[0] > newest):
				newest = row[0]
		except Exception as exc:
			errors.append(f"scheduler_last_run {name}: {type(exc).__name__}")
	# Naive datetime from the site's own DB, i.e. the same clock Pulse's
	# Monitored Host.scheduler_last_run has always been compared against.
	return str(newest) if newest else None


def collect_bench(bench_path):
	import redis
	from rq import Queue, Worker
	from rq.job import Job

	common = read_json(os.path.join(bench_path, "sites", "common_site_config.json"))
	url = common.get("redis_queue")
	if not url:
		raise RuntimeError("redis_queue not set in common_site_config.json")
	conn = redis.from_url(url)

	hostname = socket.gethostname()
	workers = [w for w in Worker.all(connection=conn)]
	live = [w for w in workers if (w.hostname in (None, hostname)) and w.pid and pid_alive(w.pid)]
	live_pids = sorted(w.pid for w in live)

	queue_depths = {}
	failed_jobs = []
	failed_total = 0
	for queue in Queue.all(connection=conn):
		short = queue.name.split(":")[-1]
		queue_depths[short] = queue.count
		registry = queue.failed_job_registry
		failed_total += registry.count
		for job_id in registry.get_job_ids()[:MAX_FAILED_JOBS_PER_QUEUE]:
			try:
				job = Job.fetch(job_id, connection=conn)
			except Exception:
				continue
			exc_info = getattr(job, "exc_info", None)
			if not exc_info:
				try:
					result = job.latest_result()
					exc_info = result.exc_string if result else None
				except Exception:
					exc_info = None
			kwargs = job.kwargs or {}
			failed_jobs.append(
				{
					"rq_job_id": job_id,
					"queue_name": short,
					"failed_at": iso_utc(job.ended_at),
					"exc_info": (exc_info or "")[:EXC_INFO_LIMIT],
					"job_name": kwargs.get("job_name") or kwargs.get("method") or job.func_name,
				}
			)

	active_jobs = []
	for worker in workers:
		try:
			job = worker.get_current_job()
		except Exception:
			continue
		if not job:
			continue
		kwargs = job.kwargs or {}
		active_jobs.append(
			{
				"rq_job_id": job.id,
				"queue_name": (job.origin or "").split(":")[-1],
				"job_name": kwargs.get("job_name") or kwargs.get("method") or job.func_name,
				"worker_pid": worker.pid,
				"started_at": iso_utc(job.started_at),
			}
		)

	return {
		"bench_name": os.path.basename(bench_path.rstrip("/")),
		"registered_workers": len(workers),
		"live_worker_count": len(live),
		"orphan_worker_count": len(workers) - len(live),
		"live_worker_pids": live_pids,
		"queue_depths": queue_depths,
		"failed_job_count": failed_total,
		"failed_jobs": failed_jobs,
		"active_job_count": len(active_jobs),
		"active_jobs": active_jobs,
		"scheduler_last_run": scheduler_last_run(bench_path, common),
	}


# --------------------------------------------------------------------------
# resources
# --------------------------------------------------------------------------


def collect_load():
	one, five, fifteen = os.getloadavg()
	return {
		"cpu_core_count": os.cpu_count() or 1,
		"load_avg_1min": one,
		"load_avg_5min": five,
		"load_avg_15min": fifteen,
	}


def collect_swap():
	info = {}
	with open("/proc/meminfo") as handle:
		for line in handle:
			key, _, rest = line.partition(":")
			if key in ("SwapTotal", "SwapFree"):
				info[key] = int(rest.split()[0]) * 1024  # kB -> bytes
	total = info.get("SwapTotal", 0)
	used = total - info.get("SwapFree", 0)
	return {"total_gb": total / 1e9, "used_gb": used / 1e9}


def collect_disks():
	seen = set()
	rows = []
	with open("/proc/mounts") as handle:
		for line in handle:
			parts = line.split()
			if len(parts) < 3:
				continue
			device, mount, fstype = parts[0], parts[1].replace("\\040", " "), parts[2]
			if fstype not in REAL_FS or device in seen:
				continue
			seen.add(device)
			try:
				st = os.statvfs(mount)
			except OSError:
				continue
			if not st.f_blocks:
				continue
			used = (st.f_blocks - st.f_bfree) * st.f_frsize
			total = used + st.f_bavail * st.f_frsize  # same basis as `df`
			rows.append(
				{
					"mount_point": mount,
					"fstype": fstype,
					"used_bytes": used,
					"total_bytes": total,
					"inodes_used": (st.f_files - st.f_ffree) if st.f_files else None,
					"inodes_total": st.f_files or None,
				}
			)
	return rows



# --------------------------------------------------------------------------
# performance: memory, pressure (PSI), container limits, disk I/O
# --------------------------------------------------------------------------

TOP_PROCESSES = 5
MAX_IO_DEVICES = 24
DB_PROCESS_NAMES = ("mariadbd", "mysqld")
SKIP_DEVICE_PREFIXES = ("loop", "ram", "sr", "fd", "nbd")


def read_kv_file(path):
	out = {}
	with open(path) as handle:
		for line in handle:
			parts = line.split()
			if len(parts) >= 2:
				out[parts[0].rstrip(":")] = parts[1]
	return out


def collect_memory():
	info = read_kv_file("/proc/meminfo")
	mb = lambda key: round(int(info.get(key, 0)) / 1024, 1)  # kB -> MB
	return {
		"total_mb": mb("MemTotal"),
		"available_mb": mb("MemAvailable"),
		"cached_mb": mb("Cached"),
		"buffers_mb": mb("Buffers"),
		"slab_mb": mb("Slab"),
		"dirty_mb": mb("Dirty"),
		"writeback_mb": mb("Writeback"),
		"swap_total_mb": mb("SwapTotal"),
		"swap_used_mb": round(mb("SwapTotal") - mb("SwapFree"), 1),
	}


def collect_counters():
	vm = read_kv_file("/proc/vmstat")
	out = {}
	for key in ("pswpin", "pswpout", "oom_kill"):
		if key in vm:
			out[key] = int(vm[key])
	return out


def collect_pressure():
	"""PSI: share of time tasks were stalled waiting for a resource (avg10 =
	last 10 s). Missing on kernels without PSI, which is fine."""
	out = {}
	for name in ("cpu", "memory", "io"):
		path = f"/proc/pressure/{name}"
		if not os.path.exists(path):
			continue
		with open(path) as handle:
			for line in handle:
				kind, _, rest = line.partition(" ")
				fields = dict(item.split("=") for item in rest.split() if "=" in item)
				if "avg10" in fields:
					out[f"{name}_{kind}_avg10"] = float(fields["avg10"])
	return out


def cgroup_dir():
	try:
		with open("/proc/self/cgroup") as handle:
			for line in handle:
				if line.startswith("0::"):
					path = "/sys/fs/cgroup" + line.strip().split("::", 1)[1]
					if os.path.isdir(path):
						return path
	except OSError:
		pass
	return "/sys/fs/cgroup" if os.path.isdir("/sys/fs/cgroup") else None


def collect_cgroup():
	"""Container limits (cgroup v2): memory ceiling and use, OOM kills inside
	the limit, and how much the CPU limit is throttling this container."""
	base = cgroup_dir()
	if not base:
		return None
	out = {}

	def read(name):
		try:
			with open(os.path.join(base, name)) as handle:
				return handle.read().strip()
		except OSError:
			return None

	limit = read("memory.max")
	if limit and limit != "max":
		out["mem_limit_mb"] = round(int(limit) / 1048576, 1)
	current = read("memory.current")
	if current:
		out["mem_current_mb"] = round(int(current) / 1048576, 1)
	events = read("memory.events")
	if events:
		kv = dict(line.split() for line in events.splitlines() if len(line.split()) == 2)
		out["oom_kill_total"] = int(kv.get("oom_kill", 0))
	cpu = read("cpu.stat")
	if cpu:
		kv = dict(line.split() for line in cpu.splitlines() if len(line.split()) == 2)
		if "nr_throttled" in kv:
			out["nr_throttled_total"] = int(kv["nr_throttled"])
			out["throttled_usec_total"] = int(kv.get("throttled_usec", 0))
	cpu_max = read("cpu.max")
	if cpu_max:
		quota, _, period = cpu_max.partition(" ")
		if quota != "max" and period:
			out["cpu_limit_cores"] = round(int(quota) / int(period), 2)
	return out or None


def collect_top_processes():
	rows = []
	for entry in os.listdir("/proc"):
		if not entry.isdigit():
			continue
		try:
			fields = read_kv_file(f"/proc/{entry}/status")
			with open(f"/proc/{entry}/status") as handle:
				name = next(line.split(":", 1)[1].strip() for line in handle if line.startswith("Name:"))
		except (OSError, StopIteration):
			continue
		rss = int(fields.get("VmRSS", 0))
		if rss:
			rows.append(
				{
					"pid": int(entry),
					"name": name,
					"rss_mb": round(rss / 1024, 1),
					"swap_mb": round(int(fields.get("VmSwap", 0)) / 1024, 1),
				}
			)
	rows.sort(key=lambda r: r["rss_mb"], reverse=True)
	top = rows[:TOP_PROCESSES]
	# The database matters most when diagnosing, so keep it listed even
	# when something else is bigger.
	if not any(r["name"] in DB_PROCESS_NAMES for r in top):
		top += [r for r in rows if r["name"] in DB_PROCESS_NAMES][:1]
	return top


def collect_io():
	"""Raw cumulative counters per block device. Pulse turns two samples into
	rates, so a restart of this script loses nothing."""
	has_sysfs = os.path.isdir("/sys/block")
	rows = []
	with open("/proc/diskstats") as handle:
		for line in handle:
			f = line.split()
			if len(f) < 14:
				continue
			name = f[2]
			if name.startswith(SKIP_DEVICE_PREFIXES):
				continue
			if has_sysfs and not os.path.exists(f"/sys/block/{name}"):
				continue  # a partition; its whole disk is already listed
			reads, writes = int(f[3]), int(f[7])
			if not reads and not writes:
				continue
			rows.append(
				{
					"device": name,
					"reads": reads,
					"writes": writes,
					"sectors_read": int(f[5]),
					"sectors_written": int(f[9]),
					"read_ms": int(f[6]),
					"write_ms": int(f[10]),
					"in_flight": int(f[11]),
					"io_ms": int(f[12]),
					"weighted_ms": int(f[13]),
				}
			)
	rows.sort(key=lambda r: r["io_ms"], reverse=True)
	return rows[:MAX_IO_DEVICES]


# --------------------------------------------------------------------------
# MariaDB: status counters, configuration, data size, the server process
# --------------------------------------------------------------------------

#: Read with each site's own database login, taken from site_config.json on
#: this host and used here only: the password is never printed or sent.
#: SHOW GLOBAL STATUS/VARIABLES need no privilege; the engine status, open
#: transactions and other users' queries need PROCESS, so those are tried
#: and reported as "limited" when refused.
MARIADB_STATUS_KEYS = (
	"Uptime", "Questions", "Slow_queries", "Connections", "Aborted_connects", "Aborted_clients",
	"Threads_connected", "Threads_running", "Max_used_connections", "Connection_errors_max_connections",
	"Innodb_buffer_pool_reads", "Innodb_buffer_pool_read_requests", "Innodb_buffer_pool_wait_free",
	"Innodb_buffer_pool_pages_total", "Innodb_buffer_pool_pages_free", "Innodb_buffer_pool_pages_dirty",
	"Innodb_buffer_pool_pages_flushed", "Innodb_buffer_pool_pages_data", "Innodb_buffer_pool_bytes_data",
	"Innodb_buffer_pool_bytes_dirty", "Innodb_buffer_pool_read_ahead", "Innodb_buffer_pool_read_ahead_evicted",
	"Innodb_data_reads", "Innodb_data_writes", "Innodb_data_fsyncs", "Innodb_data_read", "Innodb_data_written",
	"Innodb_data_pending_reads", "Innodb_data_pending_writes", "Innodb_data_pending_fsyncs",
	"Innodb_log_waits", "Innodb_log_writes", "Innodb_checkpoint_age", "Innodb_checkpoint_max_age",
	"Innodb_history_list_length", "Innodb_row_lock_waits", "Innodb_row_lock_time", "Innodb_row_lock_current_waits",
	"Innodb_deadlocks", "Innodb_pages_read", "Innodb_pages_written",
	"Created_tmp_disk_tables", "Created_tmp_tables", "Select_full_join", "Sort_merge_passes",
	"Handler_read_rnd_next", "Opened_tables", "Open_tables", "Open_files", "Table_locks_waited",
	"Table_locks_immediate", "Binlog_cache_disk_use", "Bytes_received", "Bytes_sent",
)
MARIADB_VARIABLE_KEYS = (
	"version", "innodb_buffer_pool_size", "innodb_buffer_pool_instances", "innodb_io_capacity",
	"innodb_io_capacity_max", "innodb_log_file_size", "innodb_log_buffer_size",
	"innodb_flush_log_at_trx_commit", "innodb_flush_method", "innodb_read_io_threads",
	"innodb_write_io_threads", "innodb_doublewrite", "innodb_file_per_table", "innodb_adaptive_hash_index",
	"innodb_lru_scan_depth", "innodb_max_dirty_pages_pct", "innodb_max_dirty_pages_pct_lwm",
	"max_connections", "thread_cache_size", "tmp_table_size", "max_heap_table_size", "table_open_cache",
	"table_definition_cache", "open_files_limit", "sort_buffer_size", "join_buffer_size",
	"read_rnd_buffer_size", "slow_query_log", "long_query_time", "log_bin", "sync_binlog",
	"binlog_cache_size", "key_buffer_size", "performance_schema",
)
MAX_DB_SITES = 60
MARIADB_CONNECT_TIMEOUT = 5
TOP_TABLES = 10


def short_error(exc):
	"""Class name and MySQL error number only: driver messages can carry the
	user and host, and must never carry anything secret."""
	code = exc.args[0] if getattr(exc, "args", None) and isinstance(exc.args[0], int) else ""
	return f"{type(exc).__name__} {code}".strip()


def number(value):
	try:
		return int(value)
	except (TypeError, ValueError):
		try:
			return float(value)
		except (TypeError, ValueError):
			return value


def mariadb_site_configs(bench_path, common):
	sites_dir = os.path.join(bench_path, "sites")
	found = []
	for name in sorted(os.listdir(sites_dir)):
		cfg_path = os.path.join(sites_dir, name, "site_config.json")
		if name == "assets" or not os.path.isfile(cfg_path):
			continue
		try:
			cfg = read_json(cfg_path)
		except Exception:
			continue
		if cfg.get("db_name") and cfg.get("db_password"):
			found.append((name, cfg))
	return found[:MAX_DB_SITES]


def mariadb_connect(pymysql, cfg, common):
	return pymysql.connect(
		host=cfg.get("db_host") or common.get("db_host") or "127.0.0.1",
		port=int(cfg.get("db_port") or common.get("db_port") or 3306),
		user=cfg.get("db_user") or cfg["db_name"],
		password=cfg["db_password"],
		database=cfg["db_name"],
		connect_timeout=MARIADB_CONNECT_TIMEOUT,
		read_timeout=10,
	)


def mariadb_process():
	"""The server process itself: how much memory it holds, whether any of it
	is swapped out, and the open-files limit it actually runs under."""
	for entry in os.listdir("/proc"):
		if not entry.isdigit():
			continue
		try:
			with open(f"/proc/{entry}/comm") as handle:
				comm = handle.read().strip()
		except OSError:
			continue
		if comm not in DB_PROCESS_NAMES:
			continue
		out = {"pid": int(entry), "name": comm}
		try:
			status = read_kv_file(f"/proc/{entry}/status")
			out["rss_mb"] = round(int(status.get("VmRSS", 0)) / 1024, 1)
			out["swap_mb"] = round(int(status.get("VmSwap", 0)) / 1024, 1)
			out["threads"] = int(status.get("Threads", 0))
		except OSError:
			pass
		try:
			with open(f"/proc/{entry}/limits") as handle:
				for line in handle:
					if line.startswith("Max open files"):
						out["os_open_files_limit"] = int(line.split()[3])
		except (OSError, ValueError, IndexError):
			pass
		try:
			out["open_fds"] = len(os.listdir(f"/proc/{entry}/fd"))
		except OSError:
			pass
		return out
	return None


def collect_mariadb(bench_path, slow):
	import pymysql

	common = read_json(os.path.join(bench_path, "sites", "common_site_config.json"))
	sites = mariadb_site_configs(bench_path, common)
	if not sites:
		return None
	out = {"limited": [], "sites_total": len(sites), "via": None}

	conn = None
	for name, cfg in sites:
		try:
			conn = mariadb_connect(pymysql, cfg, common)
			out["via"] = name
			break
		except Exception as exc:
			errors.append(f"mariadb connect {name}: {short_error(exc)}")
	if conn is None:
		return out
	try:
		with conn.cursor() as cur:
			cur.execute("SHOW GLOBAL STATUS")
			status = {k: v for k, v in cur.fetchall() if k in MARIADB_STATUS_KEYS}
			out["status"] = {k: number(v) for k, v in status.items()}
			cur.execute("SHOW GLOBAL VARIABLES")
			variables = {k: v for k, v in cur.fetchall() if k in MARIADB_VARIABLE_KEYS}
			out["variables"] = {k: number(v) for k, v in variables.items()}
			try:
				cur.execute(
					"SELECT COUNT(*), COALESCE(MAX(TIMESTAMPDIFF(SECOND, trx_started, NOW())), 0) FROM information_schema.INNODB_TRX"
				)
				count, oldest = cur.fetchone()
				out["trx_count"], out["oldest_trx_s"] = int(count), int(oldest)
			except Exception as exc:
				# 1227: needs the PROCESS privilege; not an error, a known limit.
				out["limited"].append("transactions")
				if not (exc.args and exc.args[0] == 1227):
					errors.append(f"mariadb transactions: {short_error(exc)}")
	finally:
		conn.close()

	out["process"] = attempt_quiet(mariadb_process)

	if slow:
		out["data"] = mariadb_data_sizes(pymysql, sites, common)
	return out


def attempt_quiet(fn):
	try:
		return fn()
	except Exception as exc:
		errors.append(f"{fn.__name__}: {type(exc).__name__}")
		return None


def mariadb_data_sizes(pymysql, sites, common):
	"""Each site's login only sees its own schema, so sizes are summed over
	the sites it can connect to. Heavier than the status read, hence slow tier."""
	total = {"data_mb": 0.0, "index_mb": 0.0, "free_mb": 0.0}
	by_site, tables, counted = [], [], 0
	for name, cfg in sites:
		try:
			conn = mariadb_connect(pymysql, cfg, common)
		except Exception as exc:
			errors.append(f"mariadb size {name}: {short_error(exc)}")
			continue
		try:
			with conn.cursor() as cur:
				cur.execute(
					"SELECT COALESCE(SUM(data_length),0), COALESCE(SUM(index_length),0), COALESCE(SUM(data_free),0) "
					"FROM information_schema.TABLES WHERE table_schema = %s",
					(cfg["db_name"],),
				)
				data, index, free = (float(v) / 1048576 for v in cur.fetchone())
				cur.execute(
					"SELECT table_name, data_length + index_length FROM information_schema.TABLES "
					"WHERE table_schema = %s ORDER BY 2 DESC LIMIT %s",
					(cfg["db_name"], TOP_TABLES),
				)
				for table, size in cur.fetchall():
					tables.append({"site": name, "table": table, "mb": round(float(size) / 1048576, 1)})
		except Exception as exc:
			errors.append(f"mariadb size {name}: {short_error(exc)}")
			continue
		finally:
			conn.close()
		counted += 1
		total["data_mb"] += data
		total["index_mb"] += index
		total["free_mb"] += free
		by_site.append({"site": name, "mb": round(data + index, 1)})
	tables.sort(key=lambda t: t["mb"], reverse=True)
	by_site.sort(key=lambda t: t["mb"], reverse=True)
	return {
		"data_mb": round(total["data_mb"], 1),
		"index_mb": round(total["index_mb"], 1),
		"free_mb": round(total["free_mb"], 1),
		"sites_counted": counted,
		"by_site": by_site[:10],
		"top_tables": tables[:TOP_TABLES],
	}


# --------------------------------------------------------------------------
# Redis: the cache, queue and socketio instances named in common_site_config.json
# --------------------------------------------------------------------------

REDIS_INFO_KEYS = (
	"redis_version", "role", "uptime_in_seconds", "used_memory", "used_memory_rss", "used_memory_peak",
	"maxmemory", "maxmemory_policy", "mem_fragmentation_ratio", "evicted_keys", "expired_keys",
	"keyspace_hits", "keyspace_misses", "connected_clients", "blocked_clients", "rejected_connections",
	"instantaneous_ops_per_sec", "total_commands_processed", "total_connections_received",
	"latest_fork_usec", "rdb_last_bgsave_status", "rdb_changes_since_last_save", "aof_enabled",
	"aof_last_write_status", "loading",
)
REDIS_TIMEOUT = 3
SLOWLOG_WINDOW_SECONDS = 120


def collect_redis(bench_path):
	"""Read-only INFO, CONFIG GET maxclients and SLOWLOG of each distinct Redis
	instance. Only the command NAME of a slow command is reported, never its
	arguments, and connection URLs (which may carry a password) are never
	printed: an instance is labelled host:port."""
	import redis

	common = read_json(os.path.join(bench_path, "sites", "common_site_config.json"))
	groups = {}
	for key, role in (("redis_cache", "cache"), ("redis_queue", "queue"), ("redis_socketio", "socketio")):
		if common.get(key):
			groups.setdefault(common[key], []).append(role)
	instances = []
	now = time.time()
	for url, roles in groups.items():
		hostport = url.split("@")[-1].split("//")[-1].rstrip("/")
		entry = {"label": hostport, "roles": sorted(roles)}
		try:
			client = redis.from_url(url, socket_timeout=REDIS_TIMEOUT, socket_connect_timeout=REDIS_TIMEOUT)
			info = client.info()
			entry["info"] = {k: info[k] for k in REDIS_INFO_KEYS if k in info}
			entry["info"]["keys"] = sum(v.get("keys", 0) for k, v in info.items() if k.startswith("db") and isinstance(v, dict))
			try:
				entry["config"] = {"maxclients": int(client.config_get("maxclients").get("maxclients", 0))}
			except Exception:
				entry["config"] = {}
			try:
				recent = [
					e for e in client.slowlog_get(10)
					if now - int(e.get("start_time", 0)) <= SLOWLOG_WINDOW_SECONDS
				]
				entry["slowlog"] = {
					"len": client.slowlog_len(),
					"recent_count": len(recent),
					"recent_max_ms": round(max((e["duration"] for e in recent), default=0) / 1000, 1),
					"recent_commands": sorted({(e.get("command") or b"?").split(b" ")[0].decode("ascii", "replace")[:20] for e in recent}),
				}
			except Exception:
				entry["slowlog"] = None
		except Exception as exc:
			entry["error"] = type(exc).__name__
			errors.append(f"redis {hostport}: {type(exc).__name__}")
		instances.append(entry)
	return {"instances": instances} if instances else None

# --------------------------------------------------------------------------


def main():
	args = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
	bench_path = args.get("bench_path") or ""
	now = utc_now()

	status = {"reported_at": now}
	resources = {"reported_at": now}

	def attempt(label, fn):
		try:
			return fn()
		except Exception as exc:
			errors.append(f"{label}: {type(exc).__name__}: {exc}")
			return None

	services = attempt("services", lambda: collect_services(args.get("services") or []))
	if services is not None:
		status["services"] = services

	if bench_path and os.path.isdir(os.path.join(bench_path, "sites")):
		sites = attempt("sites", lambda: collect_sites(bench_path))
		if sites is not None:
			status["sites"] = sites
		bench = attempt("bench", lambda: collect_bench(bench_path))
		if bench is not None:
			status["benches"] = [bench]

	if bench_path and os.path.isdir(os.path.join(bench_path, "sites")):
		mariadb = attempt("mariadb", lambda: collect_mariadb(bench_path, bool(args.get("slow"))))
		if mariadb is not None:
			mariadb["reported_at"] = now
			mariadb_report = mariadb
		else:
			mariadb_report = None
	else:
		mariadb_report = None

	redis_report = None
	if bench_path and os.path.isdir(os.path.join(bench_path, "sites")):
		redis_report = attempt("redis", lambda: collect_redis(bench_path))
		if redis_report is not None:
			redis_report["reported_at"] = now

	load = attempt("load", collect_load)
	if load is not None:
		resources["load"] = load
	swap = attempt("swap", collect_swap)
	if swap is not None:
		resources["swap"] = swap
	disks = attempt("disks", collect_disks)
	if disks is not None:
		resources["disks"] = disks

	performance = {"reported_at": now}
	for label, fn in (
		("memory", collect_memory),
		("counters", collect_counters),
		("pressure", collect_pressure),
		("cgroup", collect_cgroup),
		("top_processes", collect_top_processes),
		("io", collect_io),
	):
		value = attempt(label, fn)
		if value is not None:
			performance[label] = value

	json.dump(
		{
			"collector_version": COLLECTOR_VERSION,
			"collected_at": now,
			"errors": errors,
			"status": status,
			"resources": resources,
			"performance": performance,
			**({"mariadb": mariadb_report} if mariadb_report else {}),
			**({"redis": redis_report} if redis_report else {}),
		},
		sys.stdout,
	)


main()
