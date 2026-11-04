"""scripts/reset_uptime.py on synthetic records, scoped to a synthetic
instance, all rolled back. Run with:
cd /home/ubuntu/frappe-bench && bench --site alfaedge execute 'exec(open("/home/ubuntu/frappe-bench/apps/alfaedge_pulse/tests/manual/test_reset_uptime.py").read(), {"__name__": "t"})'
"""
from datetime import datetime, timedelta

import frappe

ns = {"__name__": "lib"}  # defines the functions without running the script
exec(open("/home/ubuntu/frappe-bench/apps/alfaedge_pulse/scripts/reset_uptime.py").read(), ns)
reset_uptime = ns["reset_uptime"]

results = []


def check(name, ok, detail=""):
	results.append((name, bool(ok), detail))


import alfaedge_pulse.uptime_monitor.api as uptime_api

orig_commit, orig_client = frappe.db.commit, uptime_api._get_client
contacted = []
uptime_api._get_client = lambda instance: contacted.append(instance) or (_ for _ in ()).throw(RuntimeError("Kuma must not be contacted"))
frappe.db.commit = lambda: None
try:
	before = {dt: frappe.db.count(dt) for dt in ("Uptime Kuma Instance", "Uptime Site", "Uptime Check Log", "Uptime Maintenance Window", "Proxmox Alert Log")}

	def instance(name):
		return frappe.get_doc({"doctype": "Uptime Kuma Instance", "instance_name": name, "base_url": "https://127.0.0.1:9", "username": "u", "password": "p", "enabled": 1}).insert(ignore_permissions=True).name

	def site(inst, site_name, n):
		return frappe.get_doc({"doctype": "Uptime Site", "instance": inst, "site_name": site_name, "monitor_type": "HTTP(s)", "kuma_monitor_id": n}).insert(ignore_permissions=True).name

	a, b = instance("ZZ-reset-a"), instance("ZZ-reset-b")
	a1, a2, a3 = site(a, "zz-one.example", 901), site(a, "zz-two.example", 902), site(a, "zz-shared.example", 903)
	b1 = site(b, "zz-shared.example", 904)  # same site name on the instance that stays
	frappe.get_doc({"doctype": "Uptime Maintenance Window", "site_name": "zz-one.example", "window_type": "Daily", "start_time": "01:00:00", "end_time": "02:00:00"}).insert(ignore_permissions=True)
	frappe.get_doc({"doctype": "Uptime Maintenance Window", "site_name": "zz-shared.example", "window_type": "Daily", "start_time": "01:00:00", "end_time": "02:00:00"}).insert(ignore_permissions=True)
	alert = frappe.get_doc({"doctype": "Proxmox Alert Log", "alert_type": "Site Down", "reference_doctype": "Uptime Site", "reference_name": a1, "message": "synthetic", "resolved": 0}).insert(ignore_permissions=True)
	keep_alert = frappe.get_doc({"doctype": "Proxmox Alert Log", "alert_type": "Site Down", "reference_doctype": "Uptime Site", "reference_name": b1, "message": "synthetic", "resolved": 0}).insert(ignore_permissions=True)

	cols = ["name", "creation", "modified", "modified_by", "owner", "docstatus", "idx", "site"]
	rows = []
	now = datetime.now()
	for i in range(12000):  # more than one batch
		ts = now - timedelta(seconds=i)
		target = (a1, a2, a3)[i % 3]
		rows.append([f"zzlog{i}", ts, ts, "Administrator", "Administrator", 0, 0, target])
	rows.append(["zzlog-keep", now, now, "Administrator", "Administrator", 0, 0, b1])
	frappe.db.bulk_insert("Uptime Check Log", cols, rows, chunk_size=5000)

	said = []
	plan = reset_uptime([a], dry_run=True, say=said.append)
	check("dry run reports the instance, 3 sites, 12,000 logs, 1 window, 1 alert", plan["instances"] == [a] and len(plan["sites"]) == 3 and plan["logs"] == 12000 and plan["windows"] == 1 and plan["alerts"] == 1, plan)
	check("a shared site name is not counted as removable", "zz-shared.example" not in plan["site_names"], plan["site_names"])
	check("dry run changes nothing", frappe.db.get_value("Uptime Kuma Instance", a, "enabled") == 1 and frappe.db.exists("Uptime Site", a1) and "Nothing was changed" in said[-1], said[-1])

	said.clear()
	reset_uptime([a], dry_run=False, settle_seconds=0, say=said.append)
	check("the instance is gone", not frappe.db.exists("Uptime Kuma Instance", a))
	check("its sites are gone", not any(frappe.db.exists("Uptime Site", s) for s in (a1, a2, a3)))
	check("its check logs are gone, in more than one batch", frappe.db.count("Uptime Check Log", {"site": ["in", [a1, a2, a3]]}) == 0 and sum("check logs removed" in m for m in said) >= 3, said)
	check("its maintenance window is gone", not frappe.db.exists("Uptime Maintenance Window", {"site_name": "zz-one.example"}))
	check("a window for a name another instance still has is kept", frappe.db.exists("Uptime Maintenance Window", {"site_name": "zz-shared.example"}))
	check("its open alert was resolved, not deleted", frappe.db.get_value("Proxmox Alert Log", alert.name, "resolved") == 1)
	check("the other instance's alert was left open", frappe.db.get_value("Proxmox Alert Log", keep_alert.name, "resolved") == 0)
	check("the other instance, its site and its log are untouched", frappe.db.exists("Uptime Kuma Instance", b) and frappe.db.exists("Uptime Site", b1) and frappe.db.exists("Uptime Check Log", "zzlog-keep"))
	check("the Kuma password is gone with the instance", not frappe.db.exists("__Auth", {"doctype": "Uptime Kuma Instance", "name": a}))
	check("Kuma was never contacted", contacted == [], contacted)

	# remove the rest, then confirm real data is exactly as it was
	reset_uptime([b], dry_run=False, settle_seconds=0, say=lambda m: None)
	after = {dt: frappe.db.count(dt) for dt in before}
	check("the real records are exactly as before", after["Uptime Kuma Instance"] == before["Uptime Kuma Instance"] and after["Uptime Site"] == before["Uptime Site"] and after["Uptime Check Log"] == before["Uptime Check Log"] and after["Uptime Maintenance Window"] == before["Uptime Maintenance Window"], (before, after))
	none = reset_uptime(["no-such-instance"], dry_run=False, settle_seconds=0, say=lambda m: None)
	check("an unknown instance removes nothing", none["instances"] == [])
finally:
	frappe.db.commit, uptime_api._get_client = orig_commit, orig_client
	frappe.db.rollback()

width = max(len(n) for n, _, _ in results)
for n, ok, d in results:
	print(("PASS " if ok else "FAIL ") + n.ljust(width), "" if ok else f"-> {d}")
print("ALL PASS" if all(ok for _, ok, _ in results) else "SOME FAILED")
