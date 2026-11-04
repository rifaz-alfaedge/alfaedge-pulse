"""Phase 2 checks (memory, pressure, disk I/O, diagnosis). Run with:
cd /home/ubuntu/frappe-bench && bench --site alfaedge execute 'exec(open("<this file>").read(), {"__name__": "t"})'

Everything runs in one transaction that is rolled back: a synthetic Monitored
Host, controlled clocks, commit disabled. No remote host is contacted.
"""
import json
import subprocess
from datetime import datetime, timedelta

import frappe

from alfaedge_pulse.host_health import api, diagnosis, ingest

results = []


def check(name, ok, detail=""):
	results.append((name, bool(ok), detail))


T0 = datetime(2026, 11, 4, 12, 0, 0)
clock = {"now": T0}
orig_now, orig_commit = ingest.now_datetime, frappe.db.commit
ingest.now_datetime = lambda: clock["now"]
frappe.db.commit = lambda: None

MEM = {"total_mb": 8000, "available_mb": 4000, "cached_mb": 1000, "buffers_mb": 10, "slab_mb": 80,
       "dirty_mb": 1, "writeback_mb": 0, "swap_total_mb": 1000, "swap_used_mb": 100}


def dev(name, reads, writes, io_ms, read_ms=0, write_ms=0, sr=0, sw=0, wms=0):
	return {"device": name, "reads": reads, "writes": writes, "sectors_read": sr, "sectors_written": sw,
	        "read_ms": read_ms, "write_ms": write_ms, "in_flight": 0, "io_ms": io_ms, "weighted_ms": wms}


try:
	guest = frappe.db.get_value("Proxmox Guest", {"name": ["not in", frappe.get_all("Monitored Host", pluck="proxmox_guest")]}, "name")
	host = frappe.get_doc({"doctype": "Monitored Host", "proxmox_guest": guest, "enabled": 0}).insert(ignore_permissions=True).name

	# ---- first sample: no previous, so no rates ----
	r = ingest.process_performance(host, "x", memory=MEM,
		pressure={"io_some_avg10": 2.0}, cgroup={"mem_limit_mb": 4096, "mem_current_mb": 1000, "oom_kill_total": 3,
		"throttled_usec_total": 1_000_000, "nr_throttled_total": 10, "cpu_limit_cores": 2.0},
		counters={"pswpin": 1000, "pswpout": 2000, "oom_kill": 5},
		top_processes=[{"pid": 1, "name": "mariadbd", "rss_mb": 900, "swap_mb": 0}],
		io=[dev("sda", 1000, 2000, 10_000, 500, 800, 100_000, 200_000, 20_000)])
	check("first sample stored", r["memory_recorded"] == 1 and r["io_recorded"] == 1, r)
	first = frappe.get_all("Memory Metric Log", filters={"monitored_host": host}, fields=["*"])[0]
	check("first sample is flagged as having no rates", first.rates_valid == 0)
	check("used % computed", first.mem_used_percent == 50.0, first.mem_used_percent)
	check("top processes stored as JSON", json.loads(first.top_processes)[0]["name"] == "mariadbd")

	# ---- second sample 30 s later: rates ----
	clock["now"] = T0 + timedelta(seconds=30)
	ingest.process_performance(host, "x", memory=MEM,
		pressure={"io_some_avg10": 40.0, "io_full_avg10": 12.0},
		cgroup={"mem_limit_mb": 4096, "mem_current_mb": 4000, "oom_kill_total": 4,
		"throttled_usec_total": 4_000_000, "nr_throttled_total": 40, "cpu_limit_cores": 2.0},
		counters={"pswpin": 1300, "pswpout": 2600, "oom_kill": 6},
		io=[dev("sda", 1600, 2900, 25_000, 1700, 3200, 100_000 + 20_480, 200_000 + 40_960, 80_000)])
	rows = frappe.get_all("Memory Metric Log", filters={"monitored_host": host}, fields=["*"], order_by="collected_at desc")
	m = rows[0]
	check("swap in/s = 300/30", m.swap_in_per_s == 10.0, m.swap_in_per_s)
	check("swap out/s = 600/30", m.swap_out_per_s == 20.0, m.swap_out_per_s)
	check("oom kills since last", m.oom_kills_since_last == 1 and m.cgroup_oom_kills_since_last == 1)
	check("cpu throttled % = 3s/30s", m.cpu_throttled_percent == 10.0, m.cpu_throttled_percent)
	s = frappe.get_all("Disk IO Sample", filters={"monitored_host": host, "device": "sda"}, fields=["*"], order_by="collected_at desc")[0]
	check("read iops = 600/30", s.read_iops == 20.0, s.read_iops)
	check("write iops = 900/30", s.write_iops == 30.0, s.write_iops)
	check("read MB/s", abs(s.read_mb_s - 0.35) < 0.01, s.read_mb_s)
	check("await = (1200+2400)/1500", s.await_ms == 2.4, s.await_ms)
	check("busy % = 15000ms/30s", s.util_percent == 50.0, s.util_percent)
	check("avg queue = 60000/30000", s.avg_queue == 2.0, s.avg_queue)

	# ---- counter reset: no rate, no negative ----
	clock["now"] = T0 + timedelta(seconds=60)
	ingest.process_performance(host, "x", memory=MEM, counters={"pswpin": 10, "pswpout": 20, "oom_kill": 0},
		io=[dev("sda", 50, 60, 100)])
	m = frappe.get_all("Memory Metric Log", filters={"monitored_host": host}, fields=["*"], order_by="collected_at desc")[0]
	check("reset is flagged, no negative rate", m.rates_valid == 0 and m.oom_kills_since_last == 0 and m.swap_in_per_s == 0)
	s = frappe.get_all("Disk IO Sample", filters={"monitored_host": host, "device": "sda"}, fields=["*"], order_by="collected_at desc")[0]
	check("I/O reset is flagged, raw kept", s.rates_valid == 0 and s.reads_total == 50)

	# ---- long gap: no rate ----
	clock["now"] = T0 + timedelta(seconds=60 + 2000)
	ingest.process_performance(host, "x", memory=MEM, counters={"pswpin": 500, "pswpout": 500, "oom_kill": 0}, io=[])
	m = frappe.get_all("Memory Metric Log", filters={"monitored_host": host}, fields=["*"], order_by="collected_at desc")[0]
	check("gap over 15 min is flagged", m.rates_valid == 0)

	# ---- idle devices skipped; at most 6 stored, busiest first ----
	base = clock["now"]
	devs = [dev(f"d{i}", 100 + i, 100, 1000 + i) for i in range(10)]
	ingest.process_performance(host, "x", io=devs)  # first sight of d0..d9
	stored = frappe.get_all("Disk IO Sample", filters={"monitored_host": host, "collected_at": base}, pluck="device")
	check("first sight capped at 6 devices", len(stored) == 6, stored)
	clock["now"] = base + timedelta(seconds=30)
	devs2 = [dev(f"d{i}", 100 + i + (0 if i < 4 else i * 10), 100, 1000 + i + (0 if i < 4 else i * 100)) for i in range(10)]
	ingest.process_performance(host, "x", io=devs2)
	stored = frappe.get_all("Disk IO Sample", filters={"monitored_host": host, "collected_at": clock["now"]}, pluck="device")
	check("idle devices skipped", not ({"d0", "d1", "d2", "d3"} & set(stored)), stored)
	check("busiest six kept", set(stored) == {"d4", "d5", "d6", "d7", "d8", "d9"}, stored)

	# ---- diagnosis rules (pure) ----
	def mem(**kw):
		row = dict(mem_total_mb=8000, mem_available_mb=4000, swap_total_mb=1000, swap_used_mb=0, top_processes=[])
		row.update(kw)
		return row

	def ids(rows, io=None, oom=0):
		return {f["id"] for f in diagnosis.evaluate(rows, io or {}, oom)}

	check("healthy host has no findings", ids([mem()] * 3) == set())
	check("low memory flagged", "memory_low" in ids([mem(mem_available_mb=600)]))
	crit = diagnosis.evaluate([mem(mem_available_mb=200)], {}, 0)[0]
	check("very low memory is critical", crit["severity"] == "critical" and crit["id"] == "memory_low")
	check("sustained swapping flagged", "swapping" in ids([mem(swap_in_per_s=200, swap_out_per_s=100)] * 3))
	check("one swap spike is ignored", "swapping" not in ids([mem(swap_in_per_s=5000, swap_out_per_s=0), mem(swap_in_per_s=0, swap_out_per_s=0), mem(swap_in_per_s=0, swap_out_per_s=0)]))
	check("oom kill flagged critical", diagnosis.evaluate([mem()], {}, 2)[0]["id"] == "oom_kill")
	check("container memory limit flagged", "container_memory" in ids([mem(cgroup_mem_limit_mb=1000, cgroup_mem_current_mb=950)]))
	check("cpu throttling flagged", "cpu_throttled" in ids([mem(cpu_throttled_percent=15)] * 3))
	check("io stall critical", diagnosis.evaluate([mem(psi_io_full=20, psi_io_some=60)] * 3, {}, 0)[0]["id"] == "io_stall")
	check("io waiting warning", "io_waiting" in ids([mem(psi_io_some=40, psi_io_full=1)] * 3))
	busy = {"sda": [dict(device="sda", util_percent=97, await_ms=80)] * 3}
	check("saturated disk flagged", "disk_saturated_sda" in ids([mem()] * 3, busy))
	notbusy = {"sda": [dict(device="sda", util_percent=97, await_ms=2)] * 3}
	check("busy but fast disk not flagged", "disk_saturated_sda" not in ids([mem()] * 3, notbusy))
	f = diagnosis.evaluate([mem(psi_io_full=20)] * 3, busy, 0)
	check("io finding names busiest disk", any("sda" in x["detail"] for x in f if x["id"] == "io_stall"))
	order = [x["severity"] for x in diagnosis.evaluate([mem(mem_available_mb=900, psi_io_full=20)] * 3, {}, 1)]
	check("critical findings sort first", order == sorted(order, key=lambda s: {"critical": 0, "warning": 1}[s]), order)

	# ---- API ----
	out = api.get_host_diagnosis(host, hours=6)
	check("api blanks rates on flagged rows", all(r["swap_in_per_s"] is None for r in out["memory_trend"] if not r["rates_valid"]))
	check("api io trend has only computed rates", all(r["rates_valid"] for rows in out["io_trend"].values() for r in rows))
	check("api returns findings, latest and trends", all(k in out for k in ("findings", "latest", "memory_trend", "io_trend", "has_data")), list(out))
	check("api io trend limited to busiest devices", len(out["io_trend"]) <= 3)
	check("api hours clamped", api.get_host_diagnosis(host, hours=9999)["hours"] == 168)

	# ---- real collector, run locally ----
	proc = subprocess.run(["python3", frappe.get_app_path("alfaedge_pulse", "host_health", "collector", "collect.py"), '{"services":[],"bench_path":""}'],
	                      capture_output=True, text=True, timeout=60)
	rep = json.loads(proc.stdout)
	perf = rep["performance"]
	check("collector v2 reports performance", rep["collector_version"] >= 2 and all(k in perf for k in ("memory", "counters", "pressure", "io", "top_processes")), list(perf))
	check("collector had no errors", not rep["errors"], rep["errors"])
	ingest.now_datetime = orig_now
	r = ingest.process_performance(host, **perf)
	check("real collector report ingests cleanly", r["status"] == "ok" and not r["warnings"], r)
finally:
	ingest.now_datetime = orig_now
	frappe.db.commit = orig_commit
	frappe.db.rollback()

width = max(len(n) for n, _, _ in results)
for n, ok, d in results:
	print(("PASS " if ok else "FAIL ") + n.ljust(width), "" if ok else f"-> {d}")
print("ALL PASS" if all(ok for _, ok, _ in results) else "SOME FAILED")
