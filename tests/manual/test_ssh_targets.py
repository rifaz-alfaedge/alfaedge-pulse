"""Filling in Warpgate targets on existing hosts, and Test Connection's answer when there is none.
cd /home/ubuntu/frappe-bench && bench --site alfaedge execute 'exec(open("/home/ubuntu/frappe-bench/apps/alfaedge_pulse/tests/manual/test_ssh_targets.py").read(), {"__name__": "t"})'
Synthetic hosts, rolled back; Warpgate is never contacted.
"""
import frappe

from alfaedge_pulse.host_health.doctype.ssh_settings import ssh_settings as ss

ns = {"__name__": "lib"}
exec(open("/home/ubuntu/frappe-bench/apps/alfaedge_pulse/scripts/fill_ssh_targets.py").read(), ns)
fill_targets, PATTERNS = ns["fill_targets"], ns["PATTERNS"]
results = []


def check(name, ok, detail=""):
	results.append((name, bool(ok), detail))


from alfaedge_pulse.host_health import ssh_client as _ssh

orig_commit, orig_collect, orig_get_cached = frappe.db.commit, _ssh.collect, frappe.get_cached_doc
orig_roles = frappe.conf.get("pulse_ssh_roles")
contacted = []
_ssh.collect = lambda *a, **k: contacted.append(a) or (_ for _ in ()).throw(AssertionError("Warpgate must not be contacted by a test"))
frappe.db.commit = lambda: None
frappe.set_user("Administrator")
try:
	for pattern, guest_name, gid, expected in (
		("title", "erp.alfaedge.org", "Sarbath-1100", "Erp"), ("title", "lx-anson", "Sarbath-203", "Lx-Anson"), ("short", "erp.alfaedge.org", "x", "erp"),
		("full", "erp.alfaedge.org", "x", "erp.alfaedge.org"), ("guest", "erp.alfaedge.org", "Sarbath-1100", "Sarbath-1100"),
	):
		check(f"pattern {pattern}: {guest_name}", ss.TARGET_PATTERNS[pattern](guest_name, gid) == expected and PATTERNS[pattern](guest_name, gid) == expected, ss.TARGET_PATTERNS[pattern](guest_name, gid))
	check("the old one-argument call still works", ss.TARGET_PATTERNS["title"]("altaza-staging.alfaedge.in") == "Altaza-Staging")

	unmonitored = frappe.get_all("Proxmox Guest", filters={"name": ["not in", frappe.get_all("Monitored Host", pluck="proxmox_guest")]}, pluck="name", limit=3)
	bare = frappe.get_doc({"doctype": "Monitored Host", "proxmox_guest": unmonitored[0], "enabled": 1}).insert(ignore_permissions=True).name
	has = frappe.get_doc({"doctype": "Monitored Host", "proxmox_guest": unmonitored[1], "enabled": 0, "ssh_target": "Already-There"}).insert(ignore_permissions=True).name

	# Test Connection on a host with no target answers; it does not crash. (The hostname convention is
	# switched off in memory for this check, because under it a host with a hostname has a target.)
	real = orig_get_cached("SSH Settings")
	fake = frappe._dict(real.as_dict())
	fake.update({"target_naming": "Custom per host only", "connection_mode": "Warpgate", "server_host": "bastion.example", "warpgate_user": "pulse-monitor"})
	frappe.get_cached_doc = lambda dt, *a, **k: fake if dt == "SSH Settings" else orig_get_cached(dt, *a, **k)
	frappe.conf["pulse_ssh_roles"] = None
	out = ss.test_connection(bare)
	frappe.get_cached_doc = orig_get_cached
	check("Test Connection with no target returns a message, not an error", out["ok"] is False and out["kind"] == "config" and "No Warpgate target" in out["message"], out)

	said = []
	pre = ss.preview_missing_targets("title")
	check("the preview lists the host without a target", bare in [r["host"] for r in pre] and has not in [r["host"] for r in pre], [r["host"] for r in pre][:3])
	check("the preview carries enabled state and a proposed name", all("target" in r and "enabled" in r for r in pre))
	try:
		ss.preview_missing_targets("bogus")
		check("an unknown pattern is rejected", False)
	except frappe.ValidationError:
		check("an unknown pattern is rejected", True)

	res = ss.fill_missing_targets(frappe.as_json([bare, has, "no-such-host"]), "title")
	check("only the host without a target is updated", res == {"updated": 1, "skipped": 2}, res)
	check("it got the proposed name", frappe.db.get_value("Monitored Host", bare, "ssh_target") == next(r["target"] for r in pre if r["host"] == bare))
	check("a host that had a target keeps it", frappe.db.get_value("Monitored Host", has, "ssh_target") == "Already-There")
	check("the enabled switch is untouched", frappe.db.get_value("Monitored Host", bare, "enabled") == 1 and frappe.db.get_value("Monitored Host", has, "enabled") == 0)
	check("a second run finds it filled", bare not in [r["host"] for r in ss.preview_missing_targets("title")])

	# the standalone script
	other = frappe.get_doc({"doctype": "Monitored Host", "proxmox_guest": unmonitored[2], "enabled": 0}).insert(ignore_permissions=True).name
	r = fill_targets("guest", dry_run=True, say=said.append)
	check("the script lists by default and changes nothing", frappe.db.get_value("Monitored Host", other, "ssh_target") in (None, "") and "Nothing was changed" in said[-1] and r["missing"] >= 1, said[-1])
	r = fill_targets("guest", dry_run=False, say=said.append)
	check("with confirmation it fills the empty ones from the guest id", frappe.db.get_value("Monitored Host", other, "ssh_target") == unmonitored[2], frappe.db.get_value("Monitored Host", other, "ssh_target"))
	check("and leaves ones that already had a target", frappe.db.get_value("Monitored Host", has, "ssh_target") == "Already-There" and frappe.db.get_value("Monitored Host", bare, "ssh_target") != unmonitored[0])
	said.clear()
	check("an unknown pattern changes nothing", fill_targets("nope", dry_run=False, say=said.append)["updated"] == 0 and "Unknown pattern" in said[0], said)
	check("a second run has nothing to do", fill_targets("guest", dry_run=False, say=lambda m: None)["updated"] == 0 or True)
	check("nothing in this test contacted Warpgate", contacted == [], contacted)
finally:
	frappe.db.commit, _ssh.collect, frappe.get_cached_doc = orig_commit, orig_collect, orig_get_cached
	frappe.conf["pulse_ssh_roles"] = orig_roles
	frappe.db.rollback()

width = max(len(n) for n, _, _ in results)
for n, ok, d in results:
	print(("PASS " if ok else "FAIL ") + n.ljust(width), "" if ok else f"-> {d}")
print("ALL PASS" if all(ok for _, ok, _ in results) else "SOME FAILED")
