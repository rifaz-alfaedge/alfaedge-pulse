"""Set the Warpgate target on Monitored Hosts that have none, from their guest name.

Hosts created before SSH polling (by the old agent) have no Warpgate target, and
Pulse never guesses one, so Test Connection and polling refuse them. This fills
the empty ones in. It only LISTS unless PULSE_FILL_CONFIRM=yes, never changes a
host that already has a target, and does not switch any host on or off.

    cd /home/ubuntu/frappe-bench
    bench --site alfaedge execute 'exec(open("/home/ubuntu/fill_ssh_targets.py").read(), {"__name__": "t"})'
    PULSE_TARGET_PATTERN=title PULSE_FILL_CONFIRM=yes bench --site alfaedge execute 'exec(open("/home/ubuntu/fill_ssh_targets.py").read(), {"__name__": "t"})'

PULSE_TARGET_PATTERN picks how the Warpgate target name is built (default title):
    title  erp.alfaedge.org  ->  Erp           (first part of the guest name, capitalised)
    short  erp.alfaedge.org  ->  erp
    full   erp.alfaedge.org  ->  erp.alfaedge.org
    guest  the Proxmox guest id, e.g. Sarbath-1100
Use the pattern that matches how the targets are named in Warpgate.
"""
import os

import frappe

PATTERNS = {
	"title": lambda guest_name, guest_id=None: "-".join(part.capitalize() for part in guest_name.split(".")[0].split("-")),
	"short": lambda guest_name, guest_id=None: guest_name.split(".")[0],
	"full": lambda guest_name, guest_id=None: guest_name,
	"guest": lambda guest_name, guest_id=None: guest_id or guest_name,
}


def fill_targets(pattern="title", dry_run=True, say=print) -> dict:
	if pattern not in PATTERNS:
		say(f"Unknown pattern '{pattern}'. Use one of: {', '.join(PATTERNS)}.")
		return {"missing": 0, "updated": 0}
	rows = frappe.db.sql(
		"""SELECT h.name AS host, h.enabled, h.proxmox_guest AS guest, g.guest_name
		FROM `tabMonitored Host` h LEFT JOIN `tabProxmox Guest` g ON g.name = h.proxmox_guest
		WHERE COALESCE(h.ssh_target, '') = '' ORDER BY g.guest_name""",
		as_dict=True,
	)
	say(f"Hosts with no Warpgate target: {len(rows)} (pattern: {pattern})")
	for r in rows:
		say(f"  {(r.guest_name or r.host):38s} -> {PATTERNS[pattern](r.guest_name or r.host, r.guest)}   [{'enabled' if r.enabled else 'off'}]")
	if dry_run or not rows:
		say("Nothing was changed." if dry_run else "Nothing to fill in.")
		return {"missing": len(rows), "updated": 0}
	for r in rows:
		frappe.db.set_value("Monitored Host", r.host, "ssh_target", PATTERNS[pattern](r.guest_name or r.host, r.guest))
	frappe.db.commit()
	say(f"Set the target on {len(rows)} host(s). Create any that do not exist in Warpgate, then use Test Connection.")
	return {"missing": len(rows), "updated": len(rows)}


if __name__ == "t":  # `bench execute` runs this file with that name; tests import it under another
	fill_targets(os.environ.get("PULSE_TARGET_PATTERN", "title"), dry_run=os.environ.get("PULSE_FILL_CONFIRM") != "yes")
