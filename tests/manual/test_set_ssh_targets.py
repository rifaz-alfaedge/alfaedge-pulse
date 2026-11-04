"""scripts/set_ssh_targets.py on synthetic hosts, rolled back. Run with:
cd /home/ubuntu/frappe-bench && bench --site alfaedge execute 'exec(open("/home/ubuntu/frappe-bench/apps/alfaedge_pulse/tests/manual/test_set_ssh_targets.py").read(), {"__name__": "t"})'
"""
import os
import tempfile

import frappe

ns = {"__name__": "lib"}
exec(open("/home/ubuntu/frappe-bench/apps/alfaedge_pulse/scripts/set_ssh_targets.py").read(), ns)
plan, apply, write_template, _clean_target = ns["plan"], ns["apply"], ns["write_template"], ns["_clean_target"]
results = []


def check(name, ok, detail=""):
	results.append((name, bool(ok), detail))


orig_commit = frappe.db.commit
frappe.db.commit = lambda: None
try:
	free = frappe.get_all("Proxmox Guest", filters={"name": ["not in", frappe.get_all("Monitored Host", pluck="proxmox_guest")]}, fields=["name", "guest_name"], limit=3)
	a, b, c = (frappe.get_doc({"doctype": "Monitored Host", "proxmox_guest": g.name, "enabled": 0}).insert(ignore_permissions=True) for g in free)
	frappe.db.set_value("Monitored Host", c.name, "ssh_target", "Keep-Me")
	ga, gb, gc = free

	# the template
	path = os.path.join(tempfile.mkdtemp(), "t.csv")
	said = []
	n = write_template(path, say=said.append)
	text = open(path).read()
	check("the template lists every host, with the guest name first", n == frappe.db.count("Monitored Host") and ga.guest_name in text and text.splitlines()[0].startswith("host,warpgate_target"), text.splitlines()[:2])
	check("it shows the current target of a host that has one", "Keep-Me" in text)

	body = f"host,warpgate_target\n{ga.guest_name},ERP\n{gb.name},Custom-DB\n{gc.guest_name},Other\n{c.name},Keep-Me\nnot-a-host,Zed\n{ga.guest_name},ERP\n"
	to_set, skipped, problems = plan(body)
	check("two empty hosts are matched, one by guest name and one by guest id", sorted(h.host for _, h, _ in to_set) == sorted([a.name, b.name]), [h.host for _, h, _ in to_set])
	check("the targets are kept exactly as written (ERP is not Erp)", {h.host: t for _, h, t in to_set}[a.name] == "ERP")
	check("a host that already has a target is skipped", any("already has the target 'Keep-Me'" in why for _, _, why in skipped), skipped)
	check("an unknown name is a problem", any("no host matches" in why for _, _, why in problems), problems)
	check("a host listed twice is a problem", any("listed twice" in why for _, _, why in problems), problems)

	to_set, _, _ = plan(f"{gc.guest_name},New-Name\n", overwrite=True)
	check("overwrite replaces an existing target when asked", len(to_set) == 1 and to_set[0][2] == "New-Name")
	to_set, skipped, _ = plan(f"{gc.guest_name},Keep-Me\n", overwrite=True)
	check("the same target is not rewritten", to_set == [] and any("already set" in w for _, _, w in skipped))
	check("an empty target is skipped", plan(f"{ga.guest_name},\n")[1][0][2] == "no target given")
	check("a target with a space is rejected", "space" in plan(f"{ga.guest_name},My Server\n")[2][0][2])
	check("a target with a colon is rejected", "colon" in plan(f"{ga.guest_name},a:b\n")[2][0][2])
	check("comments and blank lines are ignored", plan("# c\n\n")[0] == [])
	check("_clean_target trims", _clean_target("  ERP ")[0] == "ERP")

	said.clear()
	r = apply(body, dry_run=True, say=said.append)
	check("a dry run changes nothing", not frappe.db.get_value("Monitored Host", a.name, "ssh_target") and "Nothing was changed" in said[-1] and r["set"] == 0, said[-1])
	check("it lists the hosts that would still be missing a target", any("will still have no target" in m for m in said))
	r = apply(body, dry_run=False, say=lambda m: None)
	check("with confirmation the two hosts are set", frappe.db.get_value("Monitored Host", a.name, "ssh_target") == "ERP" and frappe.db.get_value("Monitored Host", b.name, "ssh_target") == "Custom-DB" and r["set"] == 2)
	check("the host with a target keeps it, and the switches are untouched", frappe.db.get_value("Monitored Host", c.name, "ssh_target") == "Keep-Me" and frappe.db.get_value("Monitored Host", a.name, "enabled") == 0)
	check("a second run changes nothing", apply(body, dry_run=False, say=lambda m: None)["set"] == 0)
finally:
	frappe.db.commit = orig_commit
	frappe.db.rollback()

width = max(len(n) for n, _, _ in results)
for n, ok, d in results:
	print(("PASS " if ok else "FAIL ") + n.ljust(width), "" if ok else f"-> {d}")
print("ALL PASS" if all(ok for _, ok, _ in results) else "SOME FAILED")
