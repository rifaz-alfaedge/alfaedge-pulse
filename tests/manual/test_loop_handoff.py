"""Poll-loop handoff and watchdog checks. Run with:
cd /home/ubuntu/frappe-bench && bench --site alfaedge execute 'exec(open("/home/ubuntu/frappe-bench/apps/alfaedge_pulse/tests/manual/test_loop_handoff.py").read(), {"__name__": "t"})'

Nothing is enqueued for real and no host is contacted: enqueue, job lookups
and the cycles themselves are replaced.
"""
import os
import signal
import types

import frappe
import rq
import frappe.utils.background_jobs as bj

from alfaedge_pulse.tasks import host_poller, jobs, poller, uptime_kuma_poller

results = []


def check(name, ok, detail=""):
	results.append((name, bool(ok), list(detail) if isinstance(detail, list) else detail))


enq, cleared = [], []
orig = {
	"enqueue": frappe.enqueue, "is_enq": bj.is_job_enqueued, "clear": jobs.clear_stale_job,
	"get_current_job": rq.get_current_job, "get": frappe.cache().get_value, "queue": jobs.poller_queue,
}
frappe.enqueue = lambda method, **k: enq.append((method, k.get("job_id"), k.get("queue")))
jobs.clear_stale_job = lambda job_id, *a, **k: cleared.append(job_id)
jobs.poller_queue = lambda: "pulse"
enqueued_ids = set()
bj.is_job_enqueued = lambda job_id: job_id in enqueued_ids
frappe.cache().get_value = lambda key, *a, **k: "1" if "FRESH" in str(key) else (orig["get"](key, *a, **k) if "heartbeat" not in str(key) else None)

M = "alfaedge_pulse.tasks.x.run"
try:
	# ---- hand_off_loop alternates ids ----
	rq.get_current_job = lambda: types.SimpleNamespace(id="alfaedge||my_loop")
	jobs.hand_off_loop(M, "my_loop", 420)
	check("run under the first id queues the second", enq[-1] == (M, "my_loop_b", "pulse"), enq[-1])
	rq.get_current_job = lambda: types.SimpleNamespace(id="alfaedge||my_loop_b")
	jobs.hand_off_loop(M, "my_loop", 420)
	check("run under the second id queues the first", enq[-1] == (M, "my_loop", "pulse"), enq[-1])
	rq.get_current_job = lambda: None
	jobs.hand_off_loop(M, "my_loop", 420)
	check("no current job (called by hand) queues the second", enq[-1][1] == "my_loop_b", enq[-1])

	# ---- watchdog ----
	enq.clear(); cleared.clear()
	check("fresh heartbeat: nothing started", jobs.start_loop_if_needed(M, "my_loop", "FRESH", 420, 600, "t") is False and enq == [])
	enqueued_ids.add("my_loop_b")
	check("stale heartbeat but the successor is queued: no duplicate", jobs.start_loop_if_needed(M, "my_loop", "stale", 420, 600, "t") is False and enq == [], enq)
	enqueued_ids.clear(); enqueued_ids.add("my_loop")
	check("stale heartbeat but the loop is running: no duplicate", jobs.start_loop_if_needed(M, "my_loop", "stale", 420, 600, "t") is False and enq == [], enq)
	enqueued_ids.clear()
	check("stale heartbeat and nothing queued: starts the loop", jobs.start_loop_if_needed(M, "my_loop", "stale", 420, 600, "t") is True and enq == [(M, "my_loop", "pulse")], enq)
	check("stale leftovers of both ids are cleared first", set(cleared) == {"my_loop", "my_loop_b"}, cleared)

	# ---- the three loops hand off after running to their time bound ----
	handed = []
	for mod, fn, attr in ((poller, "run_poll_loop", "MAX_LOOP_SECONDS"), (uptime_kuma_poller, "run_uptime_poll_loop", "MAX_LOOP_SECONDS"), (host_poller, "run_host_poll_loop", "MAX_LOOP_SECONDS")):
		saved_max, saved_hand = getattr(mod, attr), mod.hand_off_loop
		setattr(mod, attr, 0)
		mod.hand_off_loop = lambda method, job_id, timeout, _n=fn: handed.append((_n, method.rsplit(".", 1)[-1], timeout))
		try:
			getattr(mod, fn)()
		finally:
			setattr(mod, attr, saved_max)
			mod.hand_off_loop = saved_hand
	check("every loop hands off at its time bound", [h[0] for h in handed] == ["run_poll_loop", "run_uptime_poll_loop", "run_host_poll_loop"], handed)
	# the time bound is patched to 0 for this run, so the timeout passed on is bound + 120 = 120
	check("successor gets the same method and bound + 120 as its timeout", all(h[1] == h[0] and h[2] == 120 for h in handed), list(handed))

	# ---- and not when there is nothing to do, or when told to stop ----
	handed.clear()
	saved = (uptime_kuma_poller.hand_off_loop, uptime_kuma_poller.poll_all_instances, uptime_kuma_poller._any_instance_enabled)
	uptime_kuma_poller.hand_off_loop = lambda *a, **k: handed.append("uptime")
	uptime_kuma_poller.poll_all_instances = lambda: None
	uptime_kuma_poller._any_instance_enabled = lambda: False
	uptime_kuma_poller.run_uptime_poll_loop()
	uptime_kuma_poller.hand_off_loop, uptime_kuma_poller.poll_all_instances, uptime_kuma_poller._any_instance_enabled = saved
	check("uptime loop with nothing enabled does not hand off", handed == [], handed)

	saved = (host_poller.hand_off_loop, host_poller._enabled)
	host_poller.hand_off_loop = lambda *a, **k: handed.append("host")
	host_poller._enabled = lambda: False
	host_poller.run_host_poll_loop()
	check("host loop with polling switched off does not hand off", handed == [], handed)

	saved_poll = host_poller.poll_all_hosts
	host_poller._enabled = lambda: True
	host_poller.poll_all_hosts = lambda: os.kill(os.getpid(), signal.SIGTERM)  # a stop request arrives mid-cycle
	host_poller.run_host_poll_loop()
	host_poller.poll_all_hosts = saved_poll
	host_poller.hand_off_loop, host_poller._enabled = saved
	check("a loop told to stop does not hand off", handed == [], handed)
finally:
	frappe.enqueue, bj.is_job_enqueued, jobs.clear_stale_job = orig["enqueue"], orig["is_enq"], orig["clear"]
	rq.get_current_job, frappe.cache().get_value, jobs.poller_queue = orig["get_current_job"], orig["get"], orig["queue"]
	frappe.db.rollback()

width = max(len(n) for n, _, _ in results)
for n, ok, d in results:
	print(("PASS " if ok else "FAIL ") + n.ljust(width), "" if ok else f"-> {d}")
print("ALL PASS" if all(ok for _, ok, _ in results) else "SOME FAILED")
