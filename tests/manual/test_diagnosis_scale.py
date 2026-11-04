"""Diagnose API at scale: a week of 30-second samples must come back as ~120
points per series, averaged correctly, and quickly. Run with:
cd /home/ubuntu/frappe-bench && bench --site alfaedge execute 'exec(open("/home/ubuntu/frappe-bench/apps/alfaedge_pulse/tests/manual/test_diagnosis_scale.py").read(), {"__name__": "t"})'

Synthetic rows on a synthetic host, all rolled back.
"""
import json
import time
from datetime import timedelta

import frappe
from frappe.utils import now_datetime

from alfaedge_pulse.host_health import api

results = []


def check(name, ok, detail=""):
	results.append((name, bool(ok), detail))


try:
	guest = frappe.db.get_value("Proxmox Guest", {"name": ["not in", frappe.get_all("Monitored Host", pluck="proxmox_guest")]}, "name")
	host = frappe.get_doc({"doctype": "Monitored Host", "proxmox_guest": guest, "enabled": 0}).insert(ignore_permissions=True).name
	now = now_datetime()
	n = 7 * 24 * 120  # a week at 30 s
	base_cols = ["name", "creation", "modified", "modified_by", "owner", "docstatus", "idx"]

	def common(i, tag):
		ts = now - timedelta(seconds=30 * (n - i))
		return ts, [f"{tag}{host}{i}"[:140], ts, ts, "Administrator", "Administrator", 0, 0]

	mem, maria, io = [], [], []
	for i in range(n):
		ts, c = common(i, "m")
		mem.append(c + [host, ts, 1 if i % 50 else 0, 8192, 4000 + (i % 100), 10.0 if i % 50 else 0.0])
		ts, c = common(i, "d")
		maria.append(c + [host, ts, 1 if i % 50 else 0, 20 + (i % 10), 100.0 if i % 50 else 0.0])
		for dev, util in (("sda", 30), ("sdb", 60), ("sdc", 90), ("sdd", 5)):
			ts, c = common(i, f"{dev}i")
			io.append(c + [host, ts, dev, 1, util + (i % 5), 2.0])
	frappe.db.bulk_insert("Memory Metric Log", base_cols + ["monitored_host", "collected_at", "rates_valid", "mem_total_mb", "mem_available_mb", "swap_in_per_s"], mem, chunk_size=5000)
	frappe.db.bulk_insert("MariaDB Metric Log", base_cols + ["monitored_host", "collected_at", "rates_valid", "threads_connected", "queries_per_s"], maria, chunk_size=5000)
	frappe.db.bulk_insert("Disk IO Sample", base_cols + ["monitored_host", "collected_at", "device", "rates_valid", "util_percent", "await_ms"], io, chunk_size=5000)

	for hours in (1, 24, 168):
		t = time.time()
		out = api.get_host_diagnosis(host, hours=hours)
		took = time.time() - t
		raw = json.dumps(out, default=str)
		m, d = out["memory_trend"], out["mariadb"]["trend"]
		check(f"{hours}h: at most ~120 memory points", 0 < len(m) <= 125, len(m))
		check(f"{hours}h: at most ~120 MariaDB points", 0 < len(d) <= 125, len(d))
		check(f"{hours}h: three busiest of four disks, each thinned", len(out["io_trend"]) == 3 and all(len(v) <= 125 for v in out["io_trend"].values()), {k: len(v) for k, v in out["io_trend"].items()})
		check(f"{hours}h: answers in under 3 s", took < 3, round(took, 2))
		check(f"{hours}h: payload under 160 KB", len(raw) < 160 * 1024, round(len(raw) / 1024))
		print(f"   {hours:>3}h took {took:.2f}s, {len(raw) / 1024:.0f} KB, {len(m)}/{len(d)} points")
		if hours == 168:
			check("busiest disks chosen by peak busy %", set(out["io_trend"]) == {"sda", "sdb", "sdc"} or set(out["io_trend"]) == {"sdb", "sdc", "sda"}, list(out["io_trend"]))
			check("quietest disk left out", "sdd" not in out["io_trend"])
			# gauges are averaged: queries_per_s is 100 on valid rows, 0 on the 1-in-50 invalid ones, which must not drag it down
			vals = [r["queries_per_s"] for r in d if r["queries_per_s"] is not None]
			check("rates ignore samples without computed rates", vals and all(abs(v - 100.0) < 0.001 for v in vals), vals[:3])
			g = sorted(float(r["threads_connected"]) for r in d if r["threads_connected"] is not None)
			# the first and last buckets hold only part of a period, so judge the middle of the range
			check("gauges are plain averages (20..29 -> ~24.5)", 24.3 <= g[len(g) // 2] <= 24.7, g[len(g) // 2])
			check("samples are in time order", [r["collected_at"] for r in m] == sorted(r["collected_at"] for r in m))
	t = time.time()
	api.get_host_diagnosis(host, hours=1)
	check("a short range does not read the whole week", time.time() - t < 2, round(time.time() - t, 2))
finally:
	frappe.db.rollback()

width = max(len(n) for n, _, _ in results)
for n, ok, d in results:
	print(("PASS " if ok else "FAIL ") + n.ljust(width), "" if ok else f"-> {d}")
print("ALL PASS" if all(ok for _, ok, _ in results) else "SOME FAILED")
