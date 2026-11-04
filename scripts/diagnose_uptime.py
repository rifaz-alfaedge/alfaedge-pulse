"""Read-only: why is an Uptime Kuma instance 'stale never' in Pulse?
Changes nothing. The one outside call is a GET of each ENABLED instance's
/metrics, the same request Pulse's poller makes; disabled instances are not contacted.

    cd /home/ubuntu/frappe-bench
    bench --site alfaedge execute 'exec(open("/home/ubuntu/diagnose_uptime.py").read(), {"__name__": "t"})'
"""
import frappe
from frappe.utils import add_to_date, now_datetime
from frappe.utils.background_jobs import get_queues, is_job_enqueued
from frappe.utils.password import get_decrypted_password
from rq.registry import StartedJobRegistry

now = now_datetime()
s = frappe.get_cached_doc("Uptime Monitor Settings")
print(f"now: {now} | poll interval: {s.poll_interval_seconds}s | heartbeat interval: {s.heartbeat_interval_seconds}s")

print("\n== instances")
instances = frappe.get_all("Uptime Kuma Instance", fields=["name", "enabled", "base_url", "verify_ssl", "last_synced", "last_error"])
for i in instances:
	print(f"- {i.name}: enabled={i.enabled} url={i.base_url} verify_ssl={i.verify_ssl} last_synced={i.last_synced or 'never'}")
	print(f"    last_error: {(i.last_error or 'none')[:300]}")
	print(f"    sites on it: {frappe.db.count('Uptime Site', {'instance': i.name})}")

print("\n== the poll loop")
heartbeat = frappe.cache().get_value("alfaedge_pulse:uptime_poller_heartbeat")
print(f"heartbeat key present: {bool(heartbeat)}")
for job_id in ("alfaedge_pulse_uptime_poller", "alfaedge_pulse_uptime_poller_b"):
	print(f"job {job_id}: queued or running = {is_job_enqueued(job_id)}")
for q in get_queues():
	name = q.name.split(":")[-1]
	running = [jid.split("||")[-1] for jid in StartedJobRegistry(queue=q).get_job_ids()]
	print(f"queue {name:8s} running: {running} | waiting: {[j.id.split('||')[-1] for j in q.jobs]}")
print("workers config has 'pulse':", "pulse" in (frappe.conf.get("workers") or {}))

print("\n== what Kuma returns (the request Pulse makes), enabled instances only")
from alfaedge_pulse.uptime_kuma_client.metrics_client import fetch_metrics

for i in instances:
	if not i.enabled:
		print(f"- {i.name}: disabled, not contacted")
		continue
	doc = frappe.get_doc("Uptime Kuma Instance", i.name)
	password = get_decrypted_password("Uptime Kuma Instance", i.name, "password", raise_exception=False)
	api_key = get_decrypted_password("Uptime Kuma Instance", i.name, "api_key", raise_exception=False)
	print(f"- {i.name}: using {'an API key' if api_key else 'username and password'}")
	try:
		metrics = fetch_metrics(doc.base_url, doc.username, password, api_key=api_key or None, verify_ssl=bool(doc.verify_ssl))
		sites = {x.site_name for x in frappe.get_all("Uptime Site", filters={"instance": i.name}, fields=["site_name"])}
		print(f"    /metrics answered with {len(metrics)} monitors")
		print(f"    Pulse sites found in it: {len(sites & set(metrics))} of {len(sites)}")
		missing = sorted(sites - set(metrics))
		if missing:
			print(f"    not in /metrics (names must match exactly): {missing[:10]}")
	except Exception as e:
		print(f"    FAILED: {str(e)[:300]}")

print("\n== recent Uptime errors in the last 3 hours")
rows = frappe.db.sql("select creation, left(method, 90) from `tabError Log` where creation > %s and (method like %s or error like %s) order by creation desc limit 8", (add_to_date(now, hours=-3), "%Uptime%", "%Uptime Monitor%"))
for r in rows:
	print(f"- {r[0]} {r[1]}")
if not rows:
	print("none")
