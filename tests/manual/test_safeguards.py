"""Safeguard checks: site-config opt-in, queue fallback, Warpgate outage alert,
bulk host creation and retention. Run with:
cd /home/ubuntu/frappe-bench && bench --site alfaedge execute 'exec(open("/home/ubuntu/frappe-bench/apps/alfaedge_pulse/tests/manual/test_safeguards.py").read(), {"__name__": "t"})'

Everything is rolled back; alert sends are captured; no remote host is contacted.
"""
from datetime import timedelta

import frappe
import rq
from frappe.utils import add_to_date, now_datetime

import alfaedge_pulse.tasks.poller as poller
from alfaedge_pulse.host_health import ssh_client
from alfaedge_pulse.host_health.doctype.ssh_settings import ssh_settings
from alfaedge_pulse.tasks import host_health_watchdog, host_poller, jobs, resource_monitor

results = []


def check(name, ok, detail=""):
	results.append((name, bool(ok), list(detail) if isinstance(detail, list) else detail))


sent = []
orig = (poller.dispatch_alert, poller.dispatch_recovery, frappe.db.commit, frappe.conf.get("pulse_ssh_polling"))
open_alerts = set()
orig_extra = (poller.has_open_alert, poller.resolve_alert)
poller.dispatch_alert = lambda *a, **k: (sent.append(("alert", a[0])), open_alerts.add(a[0]))
poller.dispatch_recovery = lambda *a, **k: sent.append(("recovery", a[0]))
# dispatch is replaced, so the Proxmox Alert Log row a real alert leaves behind
# has to be modelled, or _handle_transition's "steady state with no open
# incident" branch would re-alert every cycle.
poller.has_open_alert = lambda doctype, name, alert_type: alert_type in open_alerts
poller.resolve_alert = lambda doctype, name, alert_type: open_alerts.discard(alert_type)
frappe.db.commit = lambda: None
orig_all = rq.Worker.all

try:
	# ---- 1. site_config opt-in ----
	frappe.db.set_single_value("SSH Settings", "enabled", 1)
	frappe.conf["pulse_ssh_polling"] = False
	check("polling blocked without the site_config flag, even with the switch on", host_poller._enabled() is False)
	frappe.conf["pulse_ssh_polling"] = True
	check("polling allowed with both", host_poller._enabled() is True)
	frappe.db.set_single_value("SSH Settings", "enabled", 0)
	check("the switch alone still matters", host_poller._enabled() is False)

	# ---- 2. queue fallback ----
	orig_workers = frappe.conf.get("workers")
	frappe.conf["workers"] = {}
	check("no 'pulse' entry falls back to long", jobs.poller_queue() == "long")
	frappe.conf["workers"] = {"pulse": {"timeout": 420, "background_workers": 3}}
	rq.Worker.all = classmethod(lambda cls, *a, **k: [])
	check("'pulse' with no worker falls back to long", jobs.poller_queue() == "long")
	rq.Worker.all = classmethod(lambda cls, *a, **k: ["one worker"])
	check("'pulse' with a worker is used", jobs.poller_queue() == "pulse")
	rq.Worker.all = orig_all
	frappe.conf["workers"] = orig_workers

	# ---- 3. Warpgate outage ----
	frappe.db.set_single_value("SSH Settings", {"bastion_unreachable": 0, "bastion_failure_streak": 0})
	state = lambda: frappe.db.get_value("SSH Settings", None, ["bastion_unreachable", "bastion_failure_streak"], as_dict=True)
	host_poller.update_bastion_state(3, 0, 3)
	host_poller.update_bastion_state(3, 0, 3)
	check("two failed cycles are not enough", not state().bastion_unreachable and state().bastion_failure_streak == 2, state())
	check("no alert yet", sent == [], sent)
	host_poller.update_bastion_state(3, 0, 3)
	check("third failed cycle declares Warpgate down", state().bastion_unreachable == 1)
	check("exactly one alert for it", sent == [("alert", "Warpgate Unreachable")], sent)
	host_poller.update_bastion_state(3, 0, 3)
	check("staying down raises no second alert", sent == [("alert", "Warpgate Unreachable")], sent)

	touched = []
	host_guest = frappe.db.get_value("Monitored Host", {}, "name")
	was_online = frappe.db.get_value("Monitored Host", host_guest, "is_online")
	host_health_watchdog.check_host_heartbeats()
	check("heartbeat check leaves hosts alone while Warpgate is down", frappe.db.get_value("Monitored Host", host_guest, "is_online") == was_online)
	check("and raises no per-host alert", sent == [("alert", "Warpgate Unreachable")], sent)

	host_poller.update_bastion_state(3, 1, 2)
	check("one success brings it back", not state().bastion_unreachable and state().bastion_failure_streak == 0, state())
	check("with a recovery message", sent[-1] == ("recovery", "Warpgate Unreachable"), sent)

	sent.clear()
	for _ in range(5):
		host_poller.update_bastion_state(3, 0, 2)  # one host failed for another reason (e.g. refused login)
	check("mixed failures never count as a Warpgate outage", not state().bastion_unreachable and sent == [], (state(), sent))
	for _ in range(5):
		host_poller.update_bastion_state(0, 0, 0)
	check("an empty cycle never counts", not state().bastion_unreachable)

	# ---- 4. bulk host creation ----
	for name, expected in (("altaza-staging.alfaedge.in", "Altaza-Staging"), ("docker", "Docker"), ("lx-anson", "Lx-Anson")):
		check(f"target name 'title': {name}", ssh_settings.TARGET_PATTERNS["title"](name) == expected, ssh_settings.TARGET_PATTERNS["title"](name))
	check("target name 'short'", ssh_settings.TARGET_PATTERNS["short"]("altaza-staging.alfaedge.in") == "altaza-staging")
	check("target name 'full'", ssh_settings.TARGET_PATTERNS["full"]("altaza-staging.alfaedge.in") == "altaza-staging.alfaedge.in")

	candidates = ssh_settings._candidate_guests("Staging", True, "title")
	monitored = set(frappe.get_all("Monitored Host", pluck="proxmox_guest"))
	check("candidates exist", len(candidates) > 0)
	check("already monitored guests are excluded", not any(c["guest"] in monitored for c in candidates))
	check("only running guests by default", all(c["status"] == "running" for c in candidates))
	pick = candidates[0]
	frappe.set_user("Administrator")
	r = ssh_settings.create_hosts_from_guests(frappe.as_json([pick["guest"], "does-not-exist"]), "Staging", 1, "title")
	check("creates the chosen guest, skips the unknown", r == {"created": 1, "skipped": 1}, r)
	made = frappe.db.get_value("Monitored Host", {"proxmox_guest": pick["guest"]}, ["enabled", "ssh_target"], as_dict=True)
	check("new host is switched off with the target filled in", made and made.enabled == 0 and made.ssh_target == pick["target"], made)
	r = ssh_settings.create_hosts_from_guests(frappe.as_json([pick["guest"]]), "Staging", 1, "title")
	check("running it again creates nothing", r["created"] == 0, r)
	try:
		ssh_settings.preview_hosts_from_guests("Staging", 1, "bogus")
		check("unknown pattern rejected", False)
	except frappe.ValidationError:
		check("unknown pattern rejected", True)

	# ---- 5. retention ----
	guest = frappe.db.get_value("Proxmox Guest", {"name": ["not in", frappe.get_all("Monitored Host", pluck="proxmox_guest")]}, "name")
	host = frappe.get_doc({"doctype": "Monitored Host", "proxmox_guest": guest, "enabled": 0}).insert(ignore_permissions=True).name
	old, new = add_to_date(now_datetime(), days=-200), add_to_date(now_datetime(), days=-1)

	def worker_row(ts):
		return frappe.get_doc({"doctype": "Frappe Worker Health Log", "monitored_host": host, "bench_name": "b", "timestamp": ts}).insert(ignore_permissions=True).name

	def failed_row(job, resolved, when):
		return frappe.get_doc(
			{"doctype": "Frappe Failed Job Log", "monitored_host": host, "rq_job_id": job, "bench_name": "b", "queue_name": "q", "failed_at": when,
			 "first_seen": when, "last_seen": when, "resolved": resolved, "resolved_at": when if resolved else None}
		).insert(ignore_permissions=True).name

	w_old, w_new = worker_row(old), worker_row(new)
	f_old_resolved, f_new_resolved, f_old_open = failed_row("j1", 1, old), failed_row("j2", 1, new), failed_row("j3", 0, old)
	# Scope every query the purge makes to the synthetic host, so the test can
	# never read or delete (even inside a rolled-back transaction) the real
	# history tables, which are large.
	real_get_all = frappe.get_all

	def scoped_get_all(doctype, *args, **kwargs):
		kwargs["filters"] = {**(kwargs.get("filters") or {}), "monitored_host": host}
		return real_get_all(doctype, *args, **kwargs)

	frappe.get_all = scoped_get_all
	try:
		resource_monitor.purge_old_resource_logs()
	finally:
		frappe.get_all = real_get_all
	check("old worker health row purged", not frappe.db.exists("Frappe Worker Health Log", w_old))
	check("recent worker health row kept", frappe.db.exists("Frappe Worker Health Log", w_new))
	check("old resolved failed job purged", not frappe.db.exists("Frappe Failed Job Log", f_old_resolved))
	check("recent resolved failed job kept", frappe.db.exists("Frappe Failed Job Log", f_new_resolved))
	check("old but still open failed job kept", frappe.db.exists("Frappe Failed Job Log", f_old_open))
finally:
	rq.Worker.all = orig_all
	poller.dispatch_alert, poller.dispatch_recovery, frappe.db.commit = orig[0], orig[1], orig[2]
	poller.has_open_alert, poller.resolve_alert = orig_extra
	frappe.conf["pulse_ssh_polling"] = orig[3]
	frappe.db.rollback()

width = max(len(n) for n, _, _ in results)
for n, ok, d in results:
	print(("PASS " if ok else "FAIL ") + n.ljust(width), "" if ok else f"-> {d}")
print("ALL PASS" if all(ok for _, ok, _ in results) else "SOME FAILED")
