"""Redis collection, rates and diagnosis. Run with:
cd /home/ubuntu/frappe-bench && bench --site alfaedge execute 'exec(open("/home/ubuntu/frappe-bench/apps/alfaedge_pulse/tests/manual/test_redis.py").read(), {"__name__": "t"})'
Synthetic data on a synthetic host, rolled back; the real collector is run once, read-only, against this server's own Redis.
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
MB = 1048576


def info(**kw):
	base = {"uptime_in_seconds": 100000, "used_memory": 100 * MB, "used_memory_rss": 110 * MB, "maxmemory": 1000 * MB, "maxmemory_policy": "allkeys-lru",
	        "mem_fragmentation_ratio": 1.1, "evicted_keys": 0, "expired_keys": 100, "keyspace_hits": 1000, "keyspace_misses": 10,
	        "connected_clients": 20, "blocked_clients": 0, "rejected_connections": 0, "total_commands_processed": 5000,
	        "total_connections_received": 300, "latest_fork_usec": 5000, "rdb_last_bgsave_status": "ok", "aof_last_write_status": "ok", "keys": 4200}
	base.update(kw)
	return base


def entry(label="127.0.0.1:13000", roles=("cache",), slow=None, **kw):
	return {"label": label, "roles": list(roles), "info": info(**kw), "config": {"maxclients": 10000},
	        "slowlog": slow or {"len": 0, "recent_count": 0, "recent_max_ms": 0.0, "recent_commands": []}}


try:
	guest = frappe.db.get_value("Proxmox Guest", {"name": ["not in", frappe.get_all("Monitored Host", pluck="proxmox_guest")]}, "name")
	host = frappe.get_doc({"doctype": "Monitored Host", "proxmox_guest": guest, "enabled": 0}).insert(ignore_permissions=True).name
	rows = lambda inst: frappe.get_all("Redis Metric Log", filters={"monitored_host": host, "instance": inst}, fields=["*"], order_by="collected_at desc")
	cache, queue = "127.0.0.1:13000", "127.0.0.1:11000"

	r = ingest.process_redis(host, "x", instances=[entry(), entry(label=queue, roles=("queue",), maxmemory=0, maxmemory_policy="noeviction")])
	check("two instances stored, no warnings", r["logged"] == 2 and not r["warnings"], r)
	first = rows(cache)[0]
	check("first sample is flagged as having no rates", first.rates_valid == 0)
	check("memory in MB and % of limit", first.used_mb == 100.0 and first.memory_percent == 10.0, (first.used_mb, first.memory_percent))
	check("roles are kept", first.roles == "cache")
	check("an unlimited instance has no % of limit", rows(queue)[0].memory_percent == 0 and rows(queue)[0].maxmemory_mb == 0)
	check("fork time in ms", first.fork_ms == 5.0, first.fork_ms)

	clock["now"] = T0 + timedelta(seconds=30)
	ingest.process_redis(host, "x", instances=[
		entry(uptime_in_seconds=100030, total_commands_processed=8000, keyspace_hits=1900, keyspace_misses=110, evicted_keys=60, expired_keys=400, rejected_connections=2,
		      slow={"len": 3, "recent_count": 1, "recent_max_ms": 250.0, "recent_commands": ["KEYS"]}),
		entry(label=queue, roles=("queue",), maxmemory=0, maxmemory_policy="noeviction", total_commands_processed=5600)])
	m, q = rows(cache)[0], rows(queue)[0]
	check("rates are valid", m.rates_valid == 1 and q.rates_valid == 1)
	check("commands/s = 3000/30", m.ops_per_s == 100.0, m.ops_per_s)
	check("hit ratio over the interval = 900/1000", m.hit_percent == 90.0, m.hit_percent)
	check("evicted since last and per second", m.evicted == 60 and m.evicted_per_s == 2.0, (m.evicted, m.evicted_per_s))
	check("expired/s = 300/30", m.expired_per_s == 10.0, m.expired_per_s)
	check("refused since last", m.rejected == 2)
	check("slow commands recorded without arguments", m.slowlog_recent_max_ms == 250.0 and m.slowlog_recent_commands == "KEYS", (m.slowlog_recent_max_ms, m.slowlog_recent_commands))
	check("instances keep separate histories", q.ops_per_s == 20.0 and q.hit_percent == 0, (q.ops_per_s, q.hit_percent))

	clock["now"] = T0 + timedelta(seconds=60)
	ingest.process_redis(host, "x", instances=[entry(uptime_in_seconds=20, total_commands_processed=10, keyspace_hits=5)])
	m = rows(cache)[0]
	check("a Redis restart is flagged, no negative rates", m.rates_valid == 0 and m.ops_per_s == 0 and m.evicted == 0)
	clock["now"] = T0 + timedelta(seconds=60 + 3000)
	ingest.process_redis(host, "x", instances=[entry(uptime_in_seconds=3100)])
	check("a long gap is flagged", rows(cache)[0].rates_valid == 0)
	r = ingest.process_redis(host, "x", instances=[{"label": "127.0.0.1:1", "roles": ["cache"], "error": "ConnectionError"}])
	check("an instance that did not answer is left out", r["logged"] == 0)
	bad = ingest.process_redis(host, "x", instances=[{"label": "z", "roles": [], "info": {"uptime_in_seconds": "oops", "keys": object()}}])
	check("a malformed entry never breaks the report", bad["status"] == "ok")

	# ---- rules (pure) ----
	def row(**kw):
		base = dict(rates_valid=1, instance=cache, roles="cache", uptime_s=100000, maxmemory_policy="allkeys-lru", used_mb=100, rss_mb=110, maxmemory_mb=1000,
		            memory_percent=10, fragmentation_ratio=1.1, ops_per_s=50, hit_percent=99, evicted=0, connected_clients=20, max_clients=10000,
		            rejected=0, persistence_ok=1, fork_ms=5, slowlog_recent_max_ms=0, slowlog_recent_commands="")
		base.update(kw)
		return base

	ids = lambda rows_, mem=None, label=cache: {f["id"] for f in diagnosis.evaluate_redis({label: rows_}, mem)}
	check("a healthy instance has no findings", ids([row()] * 3) == set())
	check("no rows, no findings", diagnosis.evaluate_redis({}, None) == [])

	f = diagnosis.evaluate_redis({queue: [row(instance=queue, roles="queue", maxmemory_policy="allkeys-lru", evicted=5)] * 3}, None)[0]
	check("evictions on the queue instance are critical", f["severity"] == "critical" and "queue" in f["id"], f)
	check("and say what to set", "noeviction" in f["advice"])
	check("evictions on a cache with a poor hit ratio warn", "redis_cache_small_127.0.0.1_13000" in ids([row(evicted=40, hit_percent=60)] * 3))
	check("evictions on a cache that still hits well are normal", ids([row(evicted=40, hit_percent=98)] * 3) == set())
	check("a low hit ratio alone warns on a cache with traffic", any(i.startswith("redis_cache_hits") for i in ids([row(hit_percent=40)] * 3)))
	check("a low hit ratio with no traffic is ignored", ids([row(hit_percent=40, ops_per_s=1)] * 3) == set())
	check("a low hit ratio just after a restart is ignored", ids([row(hit_percent=40, uptime_s=300)] * 3) == set())
	check("the queue is never judged by its hit ratio", ids([row(roles="queue", hit_percent=10)] * 3) == set())
	check("one bad sample is not enough", ids([row(evicted=40, hit_percent=10), row(), row()]) == set())
	check("samples without computed rates are ignored", ids([row(rates_valid=0, evicted=40, hit_percent=10)] * 3) == set())

	full_q = diagnosis.evaluate_redis({queue: [row(instance=queue, roles="queue", maxmemory_policy="noeviction", used_mb=990, memory_percent=99)]}, None)[0]
	check("a full noeviction instance is critical", full_q["severity"] == "critical" and "writes start failing" in full_q["advice"], full_q)
	full_c = diagnosis.evaluate_redis({cache: [row(used_mb=950)]}, None)[0]
	check("a nearly full cache that evicts is only a warning", full_c["severity"] == "warning", full_c)
	unb = diagnosis.evaluate_redis({cache: [row(maxmemory_mb=0, rss_mb=3500)]}, {"mem_total_mb": 8192})[0]
	check("no limit and 43% of the guest's memory warns", unb["id"].startswith("redis_unbounded") and unb["severity"] == "warning", unb)
	check("no limit and 70% of the guest's memory is critical", diagnosis.evaluate_redis({cache: [row(maxmemory_mb=0, rss_mb=5800)]}, {"mem_total_mb": 8192})[0]["severity"] == "critical")
	check("no limit but small is fine", ids([row(maxmemory_mb=0, rss_mb=300)], {"mem_total_mb": 8192}) == set())
	check("fragmentation of a tiny instance is ignored", ids([row(fragmentation_ratio=5.2, rss_mb=12, used_mb=2)]) == set())
	check("fragmentation of a big instance warns", any(i.startswith("redis_fragmentation") for i in ids([row(fragmentation_ratio=2.0, rss_mb=900, used_mb=450)])))
	check("an instance in swap is critical", diagnosis.evaluate_redis({cache: [row(fragmentation_ratio=0.6, rss_mb=60, used_mb=100)]}, None)[0]["severity"] == "critical")
	check("failing saves are critical", any(i.startswith("redis_persistence") for i in ids([row(persistence_ok=0)])))
	check("refused connections are critical", diagnosis.evaluate_redis({cache: [row(rejected=3)] * 3}, None)[0]["severity"] == "critical")
	check("clients near the limit warn", any(i.startswith("redis_clients") for i in ids([row(connected_clients=9000)])))
	check("a long fork warns", any(i.startswith("redis_fork") for i in ids([row(fork_ms=2300)])))
	slow = diagnosis.evaluate_redis({cache: [row(slowlog_recent_max_ms=450, slowlog_recent_commands="KEYS")]}, None)[0]
	check("a slow command warns and names it", "KEYS" in slow["detail"] and "450 ms" in slow["detail"], slow)
	check("a fast one does not", ids([row(slowlog_recent_max_ms=20)]) == set())

	# ---- the whole path ----
	out = api.get_host_diagnosis(host, hours=6)
	check("api returns the Redis section", out["redis"]["has_data"] and {cache, queue} <= set(out["redis"]["instances"]), list(out["redis"]["instances"]))
	check("api returns a trend per instance", {cache, queue} <= set(out["redis"]["trend"]) and all(len(out["redis"]["trend"][i]) >= 1 for i in (cache, queue)))
	# the buckets average only samples whose rates were computed, so a bucket made only of
	# invalid samples (the first sample, a restart) comes back as null, not as a false zero
	check("an average ignores samples without computed rates", all(r["ops_per_s"] is None or r["ops_per_s"] > 0 for r in out["redis"]["trend"][cache]), out["redis"]["trend"][cache][:3])

	# ---- the real collector against this server's Redis ----
	proc = subprocess.run(["/home/ubuntu/frappe-bench/env/bin/python", frappe.get_app_path("alfaedge_pulse", "host_health", "collector", "collect.py"),
	                       json.dumps({"services": [], "bench_path": "/home/ubuntu/frappe-bench", "slow": False})], capture_output=True, text=True, timeout=90)
	rep = json.loads(proc.stdout)
	labels = {i["label"] for i in rep["redis"]["instances"]}
	check("collector v4 reports both Redis instances", rep["collector_version"] >= 4 and labels == {cache, queue}, labels)
	check("collector had no errors", not rep["errors"], rep["errors"])
	check("output never contains a Redis URL or password", "redis://" not in proc.stdout and "password" not in proc.stdout.lower())
	clock["now"] = datetime(2026, 11, 5, 12, 0, 0)
	r = ingest.process_redis(host, **rep["redis"])
	check("a real report ingests cleanly", r["logged"] == 2 and not r["warnings"], r)
finally:
	ingest.now_datetime = orig_now
	frappe.db.commit = orig_commit
	frappe.db.rollback()

width = max(len(n) for n, _, _ in results)
for n, ok, d in results:
	print(("PASS " if ok else "FAIL ") + n.ljust(width), "" if ok else f"-> {d}")
print("ALL PASS" if all(ok for _, ok, _ in results) else "SOME FAILED")
