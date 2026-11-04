"""Read-only: how long has the scheduler been paused, and what is waiting?
Changes nothing and contacts nothing.

    cd /home/ubuntu/frappe-bench
    bench --site alfaedge execute 'exec(open("/home/ubuntu/diagnose_scheduler.py").read(), {"__name__": "t"})'
"""
import json
import os

import frappe
from frappe.utils import add_to_date, now_datetime

now = now_datetime()
print(f"now: {now}")

print("\n== where the pause is set")
bench = frappe.utils.get_bench_path()
for label, path in (
	("common_site_config.json", os.path.join(bench, "sites", "common_site_config.json")),
	(f"{frappe.local.site}/site_config.json", os.path.join(bench, "sites", frappe.local.site, "site_config.json")),
):
	try:
		cfg = json.load(open(path))
	except Exception as exc:
		print(f"- {label}: could not read ({type(exc).__name__})")
		continue
	print(f"- {label}: pause_scheduler={cfg.get('pause_scheduler', '(not set)')} | maintenance_mode={cfg.get('maintenance_mode', '(not set)')} | mute_emails={cfg.get('mute_emails', '(not set)')}")
print(f"System Settings > enable_scheduler: {frappe.db.get_single_value('System Settings', 'enable_scheduler')}")

print("\n== when did the scheduler last run something")
if frappe.db.exists("DocType", "Scheduled Job Log"):
	last = frappe.db.sql("select max(creation) from `tabScheduled Job Log`")[0][0]
	day = frappe.db.count("Scheduled Job Log", {"creation": [">", add_to_date(now, hours=-24)]})
	week = frappe.db.count("Scheduled Job Log", {"creation": [">", add_to_date(now, days=-7)]})
	print(f"newest Scheduled Job Log: {last} | runs in the last 24 h: {day} | in the last 7 days: {week}")
	newest = frappe.db.sql("select creation, scheduled_job_type, status from `tabScheduled Job Log` order by creation desc limit 3")
	for r in newest:
		print(f"  {r[0]}  {r[1]}  {r[2]}")

print("\n== email waiting to be sent (resuming the scheduler would send these)")
rows = frappe.db.sql("select status, count(*), min(creation), max(creation) from `tabEmail Queue` group by status")
for status, count, oldest, newest in rows:
	print(f"- {status}: {count} (oldest {oldest}, newest {newest})")
pending = frappe.db.sql("select count(*), min(creation) from `tabEmail Queue` where status in ('Not Sent', 'Sending')")[0]
print(f"WAITING TO SEND: {pending[0]} (oldest {pending[1]})")
if pending[0]:
	for r in frappe.db.sql("select creation, left(coalesce(sender, ''), 40), left(coalesce(reference_doctype, ''), 30) from `tabEmail Queue` where status in ('Not Sent', 'Sending') order by creation asc limit 5"):
		print(f"    {r[0]}  from {r[1]}  about {r[2]}")

print("\n== other queues that the scheduler empties")
for dt, field, values in (("WhatsApp Message", "status", ("Queued", "Pending", "Not Sent")),):
	if frappe.db.exists("DocType", dt) and frappe.get_meta(dt).has_field(field):
		n = frappe.db.count(dt, {field: ["in", values]})
		print(f"- {dt} waiting ({', '.join(values)}): {n}")
print(f"- Pulse alerts not yet sent to anyone (open, unresolved): {frappe.db.count('Proxmox Alert Log', {'resolved': 0})}")
