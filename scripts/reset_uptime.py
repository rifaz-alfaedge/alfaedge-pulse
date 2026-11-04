"""Remove Uptime Kuma instances and their sites from Pulse, for a clean start.

Run it on the server with `bench execute` (see the bottom). By default it only
LISTS what it would remove. It deletes only when PULSE_RESET_CONFIRM=yes.

    cd /home/ubuntu/frappe-bench
    bench --site alfaedge backup                 # always take a backup first
    bench --site alfaedge execute 'exec(open("/home/ubuntu/reset_uptime.py").read(), {"__name__": "t"})'
    PULSE_RESET_CONFIRM=yes bench --site alfaedge execute 'exec(open("/home/ubuntu/reset_uptime.py").read(), {"__name__": "t"})'

Why it exists: deleting a site in the dashboard also deletes the monitor on
Uptime Kuma, and if Kuma cannot be reached the delete is rolled back, so
nothing can be removed while Kuma is down. This script switches the instances
off first, so Pulse never contacts Kuma, and removes only Pulse's own records.

What it does, in order (for every instance, or just the ones named):
  1. switches the instances off (nothing is contacted, the poller idles)
  2. marks their open alerts resolved (no message is sent)
  3. deletes their check history in batches
  4. deletes the maintenance windows of those sites
  5. deletes the sites, then the instances

It does NOT touch Uptime Kuma itself: monitors that exist there stay there, and
"Import existing monitors" will bring them back once an instance is added again.
"""

import os
import time

import frappe

BATCH = 5000
SETTLE_SECONDS = 5  # let a poll cycle that was already running finish
ALERT_REFERENCES = ("Uptime Site", "Uptime Kuma Instance")


def _plan(instances=None):
	every = frappe.get_all("Uptime Kuma Instance", pluck="name")
	chosen = [n for n in every if instances is None or n in set(instances)]
	if not chosen:
		return {"instances": [], "sites": [], "site_names": [], "logs": 0, "windows": 0, "alerts": 0}
	sites = frappe.get_all("Uptime Site", filters={"instance": ["in", chosen]}, fields=["name", "site_name"])
	site_ids = [s.name for s in sites]
	kept = {s.site_name for s in frappe.get_all("Uptime Site", filters={"instance": ["not in", chosen]}, fields=["site_name"])}
	site_names = sorted({s.site_name for s in sites} - kept)
	refs = site_ids + chosen
	return {
		"instances": chosen,
		"sites": site_ids,
		"site_names": site_names,
		"logs": frappe.db.count("Uptime Check Log", {"site": ["in", site_ids]}) if site_ids else 0,
		"windows": frappe.db.count("Uptime Maintenance Window", {"site_name": ["in", site_names]}) if site_names else 0,
		"alerts": frappe.db.count("Proxmox Alert Log", {"reference_doctype": ["in", ALERT_REFERENCES], "reference_name": ["in", refs], "resolved": 0}),
	}


def reset_uptime(instances=None, dry_run=True, settle_seconds=SETTLE_SECONDS, say=print) -> dict:
	plan = _plan(instances)
	say(f"Instances ({len(plan['instances'])}): {', '.join(plan['instances']) or 'none'}")
	say(f"Sites: {len(plan['sites'])} ({len(plan['site_names'])} distinct names) | check logs: {plan['logs']:,} | maintenance windows: {plan['windows']} | open alerts to resolve: {plan['alerts']}")
	if dry_run or not plan["instances"]:
		say("Nothing was changed." if dry_run else "Nothing to remove.")
		return plan

	from alfaedge_pulse.alerts.dispatch import resolve_alert

	# 1. Off first: Pulse never contacts a disabled instance.
	for name in plan["instances"]:
		frappe.db.set_value("Uptime Kuma Instance", name, "enabled", 0)
	frappe.db.commit()
	time.sleep(settle_seconds)

	# 2. Close open alerts, without sending anything.
	refs = plan["sites"] + plan["instances"]
	for alert in frappe.get_all(
		"Proxmox Alert Log",
		filters={"reference_doctype": ["in", ALERT_REFERENCES], "reference_name": ["in", refs], "resolved": 0},
		fields=["reference_doctype", "reference_name", "alert_type"],
	):
		resolve_alert(alert.reference_doctype, alert.reference_name, alert.alert_type)
	frappe.db.commit()

	# 3. Check history, in batches so a large table never holds a long lock.
	removed = 0
	while plan["sites"]:
		names = frappe.get_all("Uptime Check Log", filters={"site": ["in", plan["sites"]]}, limit_page_length=BATCH, pluck="name")
		if not names:
			break
		frappe.db.delete("Uptime Check Log", {"name": ["in", names]})
		frappe.db.commit()
		removed += len(names)
		say(f"  check logs removed: {removed:,}")

	# 4. Maintenance windows of sites that no instance outside this run still has.
	if plan["site_names"]:
		frappe.db.delete("Uptime Maintenance Window", {"site_name": ["in", plan["site_names"]]})
		frappe.db.commit()

	# 5. Sites, then instances (a site links to its instance).
	for site in plan["sites"]:
		frappe.delete_doc("Uptime Site", site, ignore_permissions=True, force=True)
	frappe.db.commit()
	for name in plan["instances"]:
		frappe.delete_doc("Uptime Kuma Instance", name, ignore_permissions=True, force=True)
	frappe.db.commit()
	frappe.clear_cache()
	say("Done. Add the instances again in Desk (Uptime Kuma Instance), then use Import existing monitors.")
	return plan


if __name__ == "t":  # `bench execute` runs this file with that name; tests import it under another
	reset_uptime(dry_run=os.environ.get("PULSE_RESET_CONFIRM") != "yes")
