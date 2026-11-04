"""MariaDB collection, storage, rates and diagnosis. Run with:
cd /home/ubuntu/frappe-bench && bench --site alfaedge execute 'exec(open("/home/ubuntu/frappe-bench/apps/alfaedge_pulse/tests/manual/test_mariadb.py").read(), {"__name__": "t"})'

Synthetic status counters on a synthetic Monitored Host, all rolled back. The
real collector is run once, read-only, against this server's own MariaDB.
"""
import json
import subprocess
from datetime import datetime, timedelta

import frappe

from alfaedge_pulse.host_health import api, diagnosis, ingest

results = []


def check(name, ok, detail=""):
	results.append((name, bool(ok), list(detail) if isinstance(detail, list) else detail))


T0 = datetime(2026, 11, 4, 12, 0, 0)
clock = {"now": T0}
orig_now, orig_commit = ingest.now_datetime, frappe.db.commit
ingest.now_datetime = lambda: clock["now"]
frappe.db.commit = lambda: None

VARS = {"version": "10.11.14", "innodb_buffer_pool_size": 134217728, "innodb_io_capacity": 200, "innodb_io_capacity_max": 2000,
        "innodb_log_file_size": 100663296, "innodb_log_buffer_size": 16777216, "innodb_max_dirty_pages_pct": 90.0,
        "max_connections": 151, "tmp_table_size": 16777216, "table_open_cache": 2000, "open_files_limit": 32186,
        "long_query_time": 10.0, "slow_query_log": "OFF"}


def status(**kw):
	base = {"Uptime": 100000, "Questions": 1000, "Slow_queries": 0, "Connections": 100, "Aborted_connects": 0,
	        "Threads_connected": 10, "Threads_running": 2, "Max_used_connections": 20,
	        "Innodb_buffer_pool_reads": 1000, "Innodb_buffer_pool_read_requests": 100000, "Innodb_buffer_pool_wait_free": 0,
	        "Innodb_buffer_pool_pages_total": 8192, "Innodb_buffer_pool_pages_free": 4096, "Innodb_buffer_pool_pages_dirty": 819,
	        "Innodb_buffer_pool_pages_flushed": 5000, "Innodb_buffer_pool_bytes_data": 100663296,
	        "Innodb_data_reads": 2000, "Innodb_data_writes": 3000, "Innodb_data_fsyncs": 500,
	        "Innodb_checkpoint_age": 8000000, "Innodb_checkpoint_max_age": 80000000, "Innodb_history_list_length": 50,
	        "Innodb_row_lock_waits": 10, "Innodb_row_lock_time": 100, "Innodb_row_lock_current_waits": 0, "Innodb_deadlocks": 0,
	        "Created_tmp_disk_tables": 10, "Created_tmp_tables": 100, "Select_full_join": 0, "Sort_merge_passes": 0,
	        "Opened_tables": 100, "Open_tables": 100, "Open_files": 50, "Table_locks_waited": 0, "Innodb_log_waits": 0,
	        "Connection_errors_max_connections": 0}
	base.update(kw)
	return base


try:
	guest = frappe.db.get_value("Proxmox Guest", {"name": ["not in", frappe.get_all("Monitored Host", pluck="proxmox_guest")]}, "name")
	host = frappe.get_doc({"doctype": "Monitored Host", "proxmox_guest": guest, "enabled": 0}).insert(ignore_permissions=True).name
	rows = lambda: frappe.get_all("MariaDB Metric Log", filters={"monitored_host": host}, fields=["*"], order_by="collected_at desc")

	# ---- first sample: gauges, no rates ----
	r = ingest.process_mariadb(host, "x", status=status(), variables=VARS, process={"rss_mb": 400, "swap_mb": 0})
	check("first sample stored with no warnings", r["logged"] == 1 and not r["warnings"], r)
	first = rows()[0]
	check("first sample is flagged as having no rates", first.rates_valid == 0)
	check("dirty pages % from pages", first.bp_dirty_percent == 10.0, first.bp_dirty_percent)
	check("free pages % from pages", first.bp_free_percent == 50.0, first.bp_free_percent)
	check("checkpoint age % of max", first.checkpoint_age_percent == 10.0, first.checkpoint_age_percent)
	check("pool size in MB from the variable", first.bp_size_mb == 128.0, first.bp_size_mb)
	check("redo log size in MB", first.log_file_size_mb == 96.0, first.log_file_size_mb)
	check("process memory stored", first.mariadbd_rss_mb == 400)
	check("raw counters kept for the next sample", "Questions" in json.loads(first.raw_counters))

	# ---- second sample 30 s later: rates ----
	clock["now"] = T0 + timedelta(seconds=30)
	ingest.process_mariadb(host, "x", status=status(
		Uptime=100030, Questions=4000, Slow_queries=60, Connections=130, Innodb_buffer_pool_reads=1900,
		Innodb_buffer_pool_read_requests=100000 + 90000, Innodb_buffer_pool_wait_free=7, Innodb_buffer_pool_pages_flushed=8000,
		Innodb_data_reads=2600, Innodb_data_writes=3900, Innodb_data_fsyncs=560, Innodb_row_lock_waits=40, Innodb_row_lock_time=3100,
		Created_tmp_disk_tables=40, Created_tmp_tables=130, Opened_tables=400, Innodb_log_waits=3, Innodb_deadlocks=2,
		Connection_errors_max_connections=5, Select_full_join=30), variables=VARS)
	m = rows()[0]
	check("rates are valid", m.rates_valid == 1)
	check("queries/s = 3000/30", m.queries_per_s == 100.0, m.queries_per_s)
	check("slow queries/s = 60/30", m.slow_queries_per_s == 2.0, m.slow_queries_per_s)
	check("hit ratio = 1 - 900/90000", m.bp_hit_percent == 99.0, m.bp_hit_percent)
	check("disk reads/s = 900/30", m.bp_disk_reads_per_s == 30.0, m.bp_disk_reads_per_s)
	check("no-free-page waits since last", m.bp_wait_free == 7)
	check("pages flushed/s = 3000/30", m.pages_flushed_per_s == 100.0, m.pages_flushed_per_s)
	check("data writes/s", m.data_writes_per_s == 30.0, m.data_writes_per_s)
	check("avg row lock wait = 3000 ms / 30 waits", m.row_lock_avg_wait_ms == 100.0, m.row_lock_avg_wait_ms)
	check("tmp tables on disk % = 30/30", m.tmp_disk_percent == 100.0, m.tmp_disk_percent)
	check("table opens/s = 300/30", m.table_opens_per_s == 10.0, m.table_opens_per_s)
	check("joins without index/s", m.full_joins_per_s == 1.0, m.full_joins_per_s)
	check("log waits, deadlocks, refused connections since last", (m.log_waits, m.deadlocks, m.connection_limit_errors) == (3, 2, 5), (m.log_waits, m.deadlocks, m.connection_limit_errors))

	# ---- MariaDB restart: counters go back, no negative rates ----
	clock["now"] = T0 + timedelta(seconds=60)
	ingest.process_mariadb(host, "x", status=status(Uptime=20, Questions=50), variables=VARS)
	m = rows()[0]
	check("restart is flagged and shows no rates", m.rates_valid == 0 and m.queries_per_s == 0)
	# ---- long gap ----
	clock["now"] = T0 + timedelta(seconds=60 + 3000)
	ingest.process_mariadb(host, "x", status=status(Uptime=3100, Questions=90000), variables=VARS)
	check("gap over 15 min is flagged", rows()[0].rates_valid == 0)

	# ---- snapshots ----
	snaps = lambda: frappe.get_all("MariaDB Config Snapshot", filters={"monitored_host": host}, fields=["*"], order_by="collected_at desc")
	check("one snapshot while settings are unchanged and no sizes", len(snaps()) == 1, len(snaps()))
	changed = dict(VARS, innodb_io_capacity=1000)
	clock["now"] += timedelta(seconds=30)
	ingest.process_mariadb(host, "x", status=status(Uptime=3130), variables=changed)
	check("a changed setting writes a new snapshot", len(snaps()) == 2)
	data = {"data_mb": 1818.5, "index_mb": 211.9, "free_mb": 169.6, "sites_counted": 1, "by_site": [{"site": "a", "mb": 2030}], "top_tables": [{"site": "a", "table": "t", "mb": 700}]}
	clock["now"] += timedelta(seconds=30)
	ingest.process_mariadb(host, "x", status=status(Uptime=3160), variables=changed, data=data, limited=["transactions"], sites_total=1)
	check("sizes are not written again within the hour", len(snaps()) == 2)
	clock["now"] += timedelta(seconds=3700)
	ingest.process_mariadb(host, "x", status=status(Uptime=6860), variables=changed, data=data, limited=["transactions"], sites_total=1)
	s = snaps()[0]
	check("sizes are written after the hour, with what could not be read", len(snaps()) == 3 and s.data_mb == 1818.5 and s.limited == "transactions", (len(snaps()), s.data_mb, s.limited))

	# ---- diagnosis rules (pure) ----
	def row(**kw):
		base = dict(rates_valid=1, uptime_s=100000, threads_connected=10, threads_running=2, max_connections=151, bp_size_mb=128, bp_dirty_percent=10,
		            bp_hit_percent=99.9, bp_disk_reads_per_s=1, bp_wait_free=0, checkpoint_age_percent=10, log_file_size_mb=96, io_capacity=200,
		            io_capacity_max=2000, pages_flushed_per_s=5, tmp_disk_percent=0, tmp_disk_per_s=0, history_list_length=10, mariadbd_swap_mb=0,
		            mariadbd_rss_mb=400, log_waits=0, row_lock_current_waits=0, row_lock_avg_wait_ms=0, row_lock_waits_per_s=0, table_opens_per_s=0,
		            open_files=50, open_files_limit=32000, slow_queries_per_s=0, connection_limit_errors=0)
		base.update(kw)
		return base

	ids = lambda rows_, **k: {f["id"] for f in diagnosis.evaluate_mariadb(rows_, k.get("config", VARS), k.get("sizes"), k.get("memory"), k.get("io"), k.get("limit"))}
	SIZES = {"data_mb": 1800, "index_mb": 230}
	MEM = {"mem_total_mb": 8192}
	check("healthy server has no findings", ids([row()] * 3) == set())
	check("no rows, no findings", ids([]) == set())

	bad = ids([row(bp_hit_percent=90, bp_disk_reads_per_s=300)] * 10, sizes=SIZES, memory=MEM)
	check("cache misses flagged", "db_cache_misses" in bad, bad)
	f = [x for x in diagnosis.evaluate_mariadb([row(bp_hit_percent=90, bp_disk_reads_per_s=300)] * 10, VARS, SIZES, MEM, None) if x["id"] == "db_cache_misses"][0]
	check("below 95% hit ratio is critical", f["severity"] == "critical")
	check("it suggests a pool sized to the data, with the number", "2,240 MB" in f["advice"], f["advice"])
	check("it states the evidence", "90.0%" in f["detail"] and "128 MB" in f["detail"], f["detail"])
	check("a young server is not blamed for a cold cache", "db_cache_misses" not in ids([row(bp_hit_percent=90, bp_disk_reads_per_s=300, uptime_s=300)] * 10, sizes=SIZES, memory=MEM))
	check("a low ratio with almost no disk reads is ignored", "db_cache_misses" not in ids([row(bp_hit_percent=90, bp_disk_reads_per_s=2)] * 10))
	roomy = [x for x in diagnosis.evaluate_mariadb([row(bp_hit_percent=95, bp_disk_reads_per_s=300, bp_size_mb=4096)] * 10, VARS, SIZES, MEM, None) if x["id"] == "db_cache_misses"][0]
	check("a pool that already holds the data points at queries, not memory", "scanning" in roomy["advice"], roomy["advice"])
	one_bad = [row(bp_hit_percent=90, bp_disk_reads_per_s=300), row(), row()]
	check("one bad sample is not enough", "db_cache_misses" not in ids(one_bad, sizes=SIZES, memory=MEM))
	burst = [row(bp_hit_percent=94, bp_disk_reads_per_s=5000)] * 3 + [row()] * 7  # a heavy job for ~90 seconds
	check("a short burst of reads is not a cache problem", "db_cache_misses" not in ids(burst, sizes=SIZES, memory=MEM))
	mostly = [row(bp_hit_percent=94, bp_disk_reads_per_s=900)] * 8 + [row()] * 2
	check("reads that persist for most of 5 minutes are", "db_cache_misses" in ids(mostly, sizes=SIZES, memory=MEM))
	check("too few samples to judge yet", "db_cache_misses" not in ids([row(bp_hit_percent=90, bp_disk_reads_per_s=300)] * 5, sizes=SIZES, memory=MEM))
	check("samples without valid rates are ignored", "db_cache_misses" not in ids([row(rates_valid=0, bp_hit_percent=50, bp_disk_reads_per_s=900)] * 10, sizes=SIZES, memory=MEM))

	check("waits for free pages are critical", diagnosis.evaluate_mariadb([row(bp_wait_free=4)] * 3, VARS, None, None, None)[0]["id"] == "db_wait_free")
	check("checkpoint at 80% warns", "db_checkpoint" in ids([row(checkpoint_age_percent=80)] * 3))
	check("checkpoint at 95% is critical", [x for x in diagnosis.evaluate_mariadb([row(checkpoint_age_percent=95)] * 3, VARS, None, None, None) if x["id"] == "db_checkpoint"][0]["severity"] == "critical")
	check("many dirty pages warn", "db_dirty_pages" in ids([row(bp_dirty_percent=80)] * 3))

	cap = dict(bp_dirty_percent=60, pages_flushed_per_s=195)
	check("flushing at io_capacity with an idle disk: the setting is the limit", "db_io_capacity" in ids([row(**cap)] * 3, io=[{"util_percent": 20}]))
	check("flushing at io_capacity with a busy disk: the disk is the limit", "db_io_capacity" not in ids([row(**cap)] * 3, io=[{"util_percent": 85}]))
	cf = [x for x in diagnosis.evaluate_mariadb([row(**cap)] * 3, VARS, None, None, [{"util_percent": 20}]) if x["id"] == "db_io_capacity"][0]
	check("it suggests doubling, up to the maximum", "about 400" in cf["advice"], cf["advice"])
	check("nothing to flush, nothing to raise", "db_io_capacity" not in ids([row(pages_flushed_per_s=195)] * 3, io=[{"util_percent": 20}]))

	check("temp tables on disk flagged", "db_tmp_tables" in ids([row(tmp_disk_percent=60, tmp_disk_per_s=2)] * 3))
	check("a few disk temp tables are ignored", "db_tmp_tables" not in ids([row(tmp_disk_percent=60, tmp_disk_per_s=0.01)] * 3))
	check("connections at 85% warn", "db_connections" in ids([row(threads_connected=130)] * 3))
	check("refused connections are critical", [x for x in diagnosis.evaluate_mariadb([row(connection_limit_errors=3)] * 3, VARS, None, None, None) if x["id"] == "db_connections"][0]["severity"] == "critical")
	check("history list over 1M warns", "db_history" in ids([row(history_list_length=2_000_000)] * 3))
	check("history list over 5M is critical", [x for x in diagnosis.evaluate_mariadb([row(history_list_length=9_000_000)] * 3, VARS, None, None, None) if x["id"] == "db_history"][0]["severity"] == "critical")
	hist_limited = [x for x in diagnosis.evaluate_mariadb([row(history_list_length=2_000_000)] * 3, VARS, None, None, None) if x["id"] == "db_history"][0]
	check("without PROCESS it says so", "grant PROCESS" in hist_limited["advice"])
	hist_known = [x for x in diagnosis.evaluate_mariadb([row(history_list_length=2_000_000, oldest_trx_s=5400)] * 3, VARS, None, None, None) if x["id"] == "db_history"][0]
	check("with PROCESS it names the oldest transaction", "5400 seconds" in hist_known["detail"])
	check("database in swap flagged", "db_swapped" in ids([row(mariadbd_swap_mb=120)]))
	check("log buffer waits flagged", "db_log_buffer" in ids([row(log_waits=2)] * 3))
	check("row lock pile-up flagged", "db_row_locks" in ids([row(row_lock_current_waits=9)] * 3))
	check("table cache misses flagged", "db_table_cache" in ids([row(table_opens_per_s=12)] * 3))
	check("open files near the limit flagged", "db_open_files" in ids([row(open_files=900, open_files_limit=1000)]))
	check("the lower OS limit is used when it is lower", "db_open_files" in ids([row(open_files=900, open_files_limit=32000)], limit=1000))
	check("slow queries flagged", "db_slow_queries" in ids([row(slow_queries_per_s=3)] * 3))

	# ---- the whole path: diagnose_host and the API ----
	out = api.get_host_diagnosis(host, hours=6)
	check("api returns the MariaDB section", out["mariadb"]["has_data"] and "trend" in out["mariadb"] and out["mariadb"]["config"], list(out["mariadb"]))
	check("api blanks rates on rows without valid rates", all(r["queries_per_s"] is None for r in out["mariadb"]["trend"] if not r["rates_valid"]))
	check("api reports what could not be read", out["mariadb"]["limited"] == ["transactions"], out["mariadb"]["limited"])
	check("sizes come back with the largest tables", out["mariadb"]["sizes"] and out["mariadb"]["sizes"]["top_tables"][0]["table"] == "t")

	# ---- the real collector against this server's own MariaDB ----
	site_cfg = json.load(open(frappe.get_site_path("site_config.json")))
	proc = subprocess.run(
		["/home/ubuntu/frappe-bench/env/bin/python", frappe.get_app_path("alfaedge_pulse", "host_health", "collector", "collect.py"),
		 json.dumps({"services": [], "bench_path": "/home/ubuntu/frappe-bench", "slow": True})],
		capture_output=True, text=True, timeout=90)
	rep = json.loads(proc.stdout)
	check("collector reports MariaDB", rep["collector_version"] >= 3 and rep.get("mariadb", {}).get("status"), rep["errors"])
	check("collector read the settings and the data size", len(rep["mariadb"]["variables"]) > 20 and rep["mariadb"]["data"]["sites_counted"] >= 1)
	check("output never contains the database password", site_cfg["db_password"] not in proc.stdout and site_cfg["db_password"] not in proc.stderr)
	check("without PROCESS it is reported, not raised as an error", "transactions" in rep["mariadb"]["limited"] and not any("transactions" in e for e in rep["errors"]), rep["errors"])
	clock["now"] = datetime(2026, 11, 5, 12, 0, 0)
	r = ingest.process_mariadb(host, **rep["mariadb"])
	check("real report ingests cleanly", r["logged"] == 1 and not r["warnings"], r)
	fast = json.loads(subprocess.run(["/home/ubuntu/frappe-bench/env/bin/python", frappe.get_app_path("alfaedge_pulse", "host_health", "collector", "collect.py"),
	                                  json.dumps({"services": [], "bench_path": "/home/ubuntu/frappe-bench", "slow": False})], capture_output=True, text=True, timeout=90).stdout)
	check("a fast poll skips the data size", fast["mariadb"].get("data") is None and fast["mariadb"]["status"])
finally:
	ingest.now_datetime = orig_now
	frappe.db.commit = orig_commit
	frappe.db.rollback()

width = max(len(n) for n, _, _ in results)
for n, ok, d in results:
	print(("PASS " if ok else "FAIL ") + n.ljust(width), "" if ok else f"-> {d}")
print("ALL PASS" if all(ok for _, ok, _ in results) else "SOME FAILED")
