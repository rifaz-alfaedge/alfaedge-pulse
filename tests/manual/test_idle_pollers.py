import frappe
from alfaedge_pulse.tasks import uptime_kuma_poller as up, host_poller as hp
res = []
def check(n, ok, d=""): res.append((n, bool(ok), d))

enq = []
orig_enqueue, orig_count, orig_get = frappe.enqueue, frappe.db.count, frappe.cache().get_value
frappe.enqueue = lambda *a, **k: enq.append(a[0])
frappe.cache().get_value = lambda key, *a, **k: None if "heartbeat" in str(key) else orig_get(key, *a, **k)   # only the heartbeat looks stale
try:
	frappe.db.count = lambda dt, f=None, *a, **k: 0 if dt == "Uptime Kuma Instance" else orig_count(dt, f, *a, **k)
	up.ensure_uptime_poller_running()
	check("no enabled instance: watchdog does not start the loop", enq == [], enq)
	frappe.db.count = lambda dt, f=None, *a, **k: 2 if dt == "Uptime Kuma Instance" else orig_count(dt, f, *a, **k)
	up.ensure_uptime_poller_running()
	check("enabled instance: watchdog starts the loop", enq == ["alfaedge_pulse.tasks.uptime_kuma_poller.run_uptime_poll_loop"], enq)

	# the loop ends itself after one cycle when nothing is enabled
	cycles = []
	orig_poll = up.poll_all_instances
	up.poll_all_instances = lambda: cycles.append(1)
	frappe.db.count = lambda dt, f=None, *a, **k: 0 if dt == "Uptime Kuma Instance" else orig_count(dt, f, *a, **k)
	up.run_uptime_poll_loop()
	check("loop runs one cycle then ends when nothing is enabled", cycles == [1], cycles)
	up.poll_all_instances = orig_poll
finally:
	frappe.enqueue, frappe.db.count = orig_enqueue, orig_count
	frappe.cache().get_value = orig_get

# unchanged host failure is not rewritten
host = frappe.get_all("Monitored Host", pluck="name", limit=1)[0]
writes = []
orig_set, orig_commit = frappe.db.set_value, frappe.db.commit
frappe.db.set_value = lambda *a, **k: writes.append(a)
frappe.db.commit = lambda: None
try:
	frappe.db.sql("update `tabMonitored Host` set last_poll_kind=%s, last_poll_error=%s where name=%s", ("config", "No Warpgate target set on this host (SSH Access section).", host))
	hp._record_failure(host, "config", "No Warpgate target set on this host (SSH Access section).", 0)
	check("same failure again writes nothing", writes == [], writes)
	hp._record_failure(host, "timeout", "Timed out after 60s.", 5)
	check("a different failure is stored", len(writes) == 1, writes)
finally:
	frappe.db.set_value, frappe.db.commit = orig_set, orig_commit
	frappe.db.rollback()
for n, ok, d in res: print(("PASS " if ok else "FAIL ") + n, "" if ok else f"-> {d}")
print("ALL PASS" if all(ok for _, ok, _ in res) else "SOME FAILED")
