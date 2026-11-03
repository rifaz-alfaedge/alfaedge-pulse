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

Output (stdout, one JSON object):
	{"collector_version": 1, "collected_at": "...", "errors": [...],
	 "status": {"reported_at", "services", "benches", "sites"},
	 "resources": {"reported_at", "load", "swap", "disks"}}

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

COLLECTOR_VERSION = 1
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

	load = attempt("load", collect_load)
	if load is not None:
		resources["load"] = load
	swap = attempt("swap", collect_swap)
	if swap is not None:
		resources["swap"] = swap
	disks = attempt("disks", collect_disks)
	if disks is not None:
		resources["disks"] = disks

	json.dump(
		{
			"collector_version": COLLECTOR_VERSION,
			"collected_at": now,
			"errors": errors,
			"status": status,
			"resources": resources,
		},
		sys.stdout,
	)


main()
