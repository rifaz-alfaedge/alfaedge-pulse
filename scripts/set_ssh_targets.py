"""Set each host's Warpgate target from a list you write, for targets that do not follow one naming pattern.

Step 1: run it with PULSE_TARGETS_FILE pointing at a file that does not exist yet. It writes a
ready-to-edit list of every host, with the target column empty (or the current target).
Step 2: open the file and type the exact Warpgate target name beside each host.
Step 3: run it again to preview, then once more with PULSE_TARGETS_CONFIRM=yes to apply.

    cd /home/ubuntu/frappe-bench
    export PULSE_TARGETS_FILE=/home/ubuntu/ssh_targets.csv
    bench --site alfaedge execute 'exec(open("/home/ubuntu/set_ssh_targets.py").read(), {"__name__": "t"})'
    PULSE_TARGETS_CONFIRM=yes bench --site alfaedge execute 'exec(open("/home/ubuntu/set_ssh_targets.py").read(), {"__name__": "t"})'

The first column is how a host is recognised: its guest name (erp.alfaedge.org), its Proxmox guest id
(Sarbath-1100) or the Monitored Host name. The second column is the Warpgate target, written exactly as
Warpgate has it (ERP is not Erp). Other columns are ignored. Leave the target empty to skip a host.

By default it only fills hosts that have NO target. A host that already has one is left alone unless
PULSE_TARGETS_OVERWRITE=yes. Nothing else about a host changes, and no host is switched on or off.
"""
import csv
import os

import frappe


def _hosts():
	return frappe.db.sql(
		"""SELECT h.name AS host, h.enabled, h.proxmox_guest AS guest, COALESCE(h.ssh_target, '') AS target, g.guest_name
		FROM `tabMonitored Host` h LEFT JOIN `tabProxmox Guest` g ON g.name = h.proxmox_guest ORDER BY g.guest_name""",
		as_dict=True,
	)


def write_template(path, say=print):
	rows = _hosts()
	with open(path, "w", newline="", encoding="utf-8") as handle:
		out = csv.writer(handle)
		out.writerow(["host", "warpgate_target", "proxmox_guest", "enabled"])
		for r in rows:
			out.writerow([r.guest_name or r.host, r.target, r.guest, "yes" if r.enabled else "no"])
	say(f"Wrote {len(rows)} hosts to {path}. Fill in the warpgate_target column, then run this again to preview.")
	return len(rows)


def _clean_target(value):
	value = (value or "").strip()
	if not value:
		return None, None
	if any(ch.isspace() for ch in value) or ":" in value:
		return None, f"'{value}' has a space or a colon; a Warpgate target is used as user:target, so it cannot"
	return value, None


def plan(text, overwrite=False):
	"""Match each line to exactly one host. Returns (to_set, skipped, problems)."""
	hosts = _hosts()
	index = {}
	for h in hosts:
		for key in {h.host, h.guest, h.guest_name}:
			if key:
				index.setdefault(key.strip().lower(), set()).add(h.host)
	by_name = {h.host: h for h in hosts}
	to_set, skipped, problems, seen = [], [], [], set()
	rows = list(csv.reader(text.splitlines()))
	for number, row in enumerate(rows, start=1):
		if not row or not "".join(row).strip() or row[0].strip().startswith("#"):
			continue
		if number == 1 and row[0].strip().lower() in ("host", "name", "guest", "guest_name"):
			continue
		key = row[0].strip()
		target, why = _clean_target(row[1] if len(row) > 1 else "")
		if why:
			problems.append((number, key, why))
			continue
		if not target:
			skipped.append((number, key, "no target given"))
			continue
		matches = index.get(key.lower(), set())
		if not matches:
			problems.append((number, key, "no host matches this name"))
			continue
		if len(matches) > 1:
			problems.append((number, key, f"matches {len(matches)} hosts; use the Monitored Host name or the Proxmox guest id"))
			continue
		host = by_name[next(iter(matches))]
		if host.host in seen:
			problems.append((number, key, "this host is listed twice"))
			continue
		seen.add(host.host)
		if host.target and not overwrite:
			skipped.append((number, key, f"already has the target '{host.target}'"))
			continue
		if host.target == target:
			skipped.append((number, key, "already set to this target"))
			continue
		to_set.append((number, host, target))
	return to_set, skipped, problems


def apply(text, dry_run=True, overwrite=False, say=print):
	to_set, skipped, problems = plan(text, overwrite)
	say(f"To set: {len(to_set)} | skipped: {len(skipped)} | problems: {len(problems)}")
	for number, host, target in to_set:
		say(f"  line {number:>3}  SET   {(host.guest_name or host.host):34s} -> {target}" + (f"   (was {host.target})" if host.target else ""))
	for number, key, why in skipped:
		say(f"  line {number:>3}  SKIP  {key}  ({why})")
	for number, key, why in problems:
		say(f"  line {number:>3}  BAD   {key}  ({why})")
	still = [h for h in _hosts() if not h.target and h.host not in {t[1].host for t in to_set}]
	if still:
		say(f"Hosts that will still have no target: {', '.join((h.guest_name or h.host) for h in still)}")
	if dry_run:
		say("Nothing was changed. Run again with PULSE_TARGETS_CONFIRM=yes to apply the lines marked SET.")
		return {"set": 0, "skipped": len(skipped), "problems": len(problems)}
	for _, host, target in to_set:
		frappe.db.set_value("Monitored Host", host.host, "ssh_target", target)
	frappe.db.commit()
	say(f"Set the target on {len(to_set)} host(s). Check each exists in Warpgate, then use Test Connection on one.")
	return {"set": len(to_set), "skipped": len(skipped), "problems": len(problems)}


if __name__ == "t":  # `bench execute` runs this file with that name; tests import it under another
	path = os.environ.get("PULSE_TARGETS_FILE", "")
	if not path:
		print("Set PULSE_TARGETS_FILE to the path of your list (see the top of this script).")
	elif not os.path.isfile(path):
		write_template(path)
	else:
		apply(open(path, encoding="utf-8-sig").read(), dry_run=os.environ.get("PULSE_TARGETS_CONFIRM") != "yes", overwrite=os.environ.get("PULSE_TARGETS_OVERWRITE") == "yes")
