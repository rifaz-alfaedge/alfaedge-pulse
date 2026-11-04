"""Saving an enabled Uptime Kuma Instance starts the poll loop at once. Run with:
cd /home/ubuntu/frappe-bench && bench --site alfaedge execute 'exec(open("/home/ubuntu/frappe-bench/apps/alfaedge_pulse/tests/manual/test_uptime_instance_start.py").read(), {"__name__": "t"})'
Nothing is enqueued for real; rolled back.
"""
import frappe

import alfaedge_pulse.tasks.uptime_kuma_poller as poller

results = []


def check(name, ok, detail=""):
	results.append((name, bool(ok), detail))


started = []
orig = poller.ensure_uptime_poller_running
poller.ensure_uptime_poller_running = lambda: started.append(1)
try:
	def make(name, enabled):
		return frappe.get_doc({"doctype": "Uptime Kuma Instance", "instance_name": name, "base_url": "https://127.0.0.1:9", "username": "u", "password": "p", "enabled": enabled}).insert(ignore_permissions=True)

	off = make("ZZ-start-off", 0)
	frappe.db.after_commit.run()
	check("saving a disabled instance does not start the loop", started == [], started)

	on = make("ZZ-start-on", 1)
	check("nothing starts before the save is committed", started == [])
	frappe.db.after_commit.run()
	check("saving an enabled instance starts it after the commit", started == [1], started)

	off.enabled = 1
	off.save(ignore_permissions=True)
	frappe.db.after_commit.run()
	check("enabling an existing instance starts it", started == [1, 1], started)

	def boom():
		raise RuntimeError("queue down")

	poller.ensure_uptime_poller_running = boom
	on.base_url = "https://127.0.0.1:10"
	on.save(ignore_permissions=True)
	frappe.db.after_commit.run()
	check("a failure to start never fails the save", frappe.db.get_value("Uptime Kuma Instance", on.name, "base_url") == "https://127.0.0.1:10")
finally:
	poller.ensure_uptime_poller_running = orig
	frappe.db.rollback()

width = max(len(n) for n, _, _ in results)
for n, ok, d in results:
	print(("PASS " if ok else "FAIL ") + n.ljust(width), "" if ok else f"-> {d}")
print("ALL PASS" if all(ok for _, ok, _ in results) else "SOME FAILED")
