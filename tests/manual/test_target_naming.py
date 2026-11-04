"""Warpgate target naming (custom name wins, else the hostname) and the role allow-list.
cd /home/ubuntu/frappe-bench && bench --site alfaedge execute 'exec(open("/home/ubuntu/frappe-bench/apps/alfaedge_pulse/tests/manual/test_target_naming.py").read(), {"__name__": "t"})'

Settings are replaced in memory only (nothing is written to SSH Settings or its cache), the
connection function is replaced by one that fails the test if it is ever called, and the
synthetic hosts are rolled back. Warpgate is never contacted.
"""
import frappe

from alfaedge_pulse.host_health import ssh_client
from alfaedge_pulse.host_health.doctype.ssh_settings import ssh_settings as ss

results = []


def check(name, ok, detail=""):
	results.append((name, bool(ok), detail))


contacted = []
orig_collect, orig_get_cached = ssh_client.collect, frappe.get_cached_doc
orig_roles = frappe.conf.get("pulse_ssh_roles")
ssh_client.collect = lambda *a, **k: contacted.append(a) or (_ for _ in ()).throw(AssertionError("Warpgate must not be contacted by a test"))


def with_settings(**override):
	real = orig_get_cached("SSH Settings")
	fake = frappe._dict(real.as_dict())
	fake.update({"connection_mode": "Warpgate", "server_host": "bastion.example", "warpgate_user": "pulse-monitor", "server_port": 2222})
	fake.update(override)

	def patched(doctype, *a, **k):
		return fake if doctype == "SSH Settings" else orig_get_cached(doctype, *a, **k)

	frappe.get_cached_doc = patched


def reason(host):
	try:
		ssh_client.resolve_target(host)
		return None
	except ssh_client.SSHPollError as exc:
		return str(exc)


def free_guest(role):
	taken = frappe.get_all("Monitored Host", pluck="proxmox_guest")
	for server in frappe.get_all("Proxmox Server", filters={"role": role, "server_type": "PVE"}, pluck="name"):
		rows = frappe.get_all("Proxmox Guest", filters={"server": server, "name": ["not in", taken or [""]], "guest_name": ["is", "set"]}, fields=["name", "guest_name"], limit=1)
		if rows:
			return rows[0]
	return None


try:
	g_stg, g_prod = free_guest("Staging"), free_guest("Production")
	check("the test has a Staging and a Production guest to use", g_stg and g_prod, (g_stg, g_prod))
	mk = lambda g, **kw: frappe.get_doc({"doctype": "Monitored Host", "proxmox_guest": g.name, "enabled": 0, **kw}).insert(ignore_permissions=True)
	plain, custom = mk(g_stg), mk(free_guest("Staging") or g_stg, ssh_target="ERP") if free_guest("Staging") else None
	frappe.conf["pulse_ssh_roles"] = None

	# ---- the naming convention ----
	with_settings(target_naming="Hostname")
	t = ssh_client.resolve_target(plain.name)
	check("Hostname convention: the target is the host's hostname", t.username == f"pulse-monitor:{g_stg.guest_name}", t.username)
	check("the connection goes to the bastion, not to the host", t.hostname == "bastion.example" and t.port == 2222)
	if custom:
		check("a custom target typed on the host wins", ssh_client.resolve_target(custom.name).username == "pulse-monitor:ERP")
	with_settings(target_naming=None)
	check("an unset convention behaves as Hostname", ssh_client.resolve_target(plain.name).username.endswith(g_stg.guest_name))
	with_settings(target_naming="Custom per host only")
	r = reason(plain.name)
	check("Custom-only: a host without its own target is refused, in plain words", r and "No Warpgate target" in r, r)
	if custom:
		check("Custom-only: a host with its own target works", ssh_client.resolve_target(custom.name).username == "pulse-monitor:ERP")
	with_settings(target_naming="Hostname")
	frappe.db.set_value("Monitored Host", plain.name, "hostname", "")
	check("Hostname convention with no hostname is refused", "No Warpgate target" in (reason(plain.name) or ""), reason(plain.name))
	frappe.db.set_value("Monitored Host", plain.name, "hostname", "my host")
	check("a hostname with a space cannot be a target", "cannot be a Warpgate target" in (reason(plain.name) or ""), reason(plain.name))
	frappe.db.set_value("Monitored Host", plain.name, "hostname", "a:b")
	check("a hostname with a colon cannot be a target", "cannot be a Warpgate target" in (reason(plain.name) or ""))
	frappe.db.set_value("Monitored Host", plain.name, "hostname", g_stg.guest_name)

	with_settings(connection_mode="Direct", default_user="pulse")
	d = ssh_client.resolve_target(plain.name)
	check("Direct mode does not use the naming convention", d.pin_scope == "host" and d.username == "pulse", (d.hostname, d.username))
	with_settings(target_naming="Hostname")

	# ---- the role allow-list ----
	frappe.conf["pulse_ssh_roles"] = ["Staging", "Development"]
	check("an allowed role is resolved", reason(plain.name) is None, reason(plain.name))
	prod = mk(g_prod)
	r = reason(prod.name)
	check("a Production host is refused on a server limited to Staging and Development", r and "only polls hosts of role Staging, Development" in r and "Production" in r, r)
	frappe.conf["pulse_ssh_roles"] = "Staging, Development"
	check("the list may also be written as text", "only polls hosts of role" in (reason(prod.name) or "") and reason(plain.name) is None)
	frappe.conf["pulse_ssh_roles"] = ["Production"]
	check("a production server may be limited to Production", reason(prod.name) is None and "only polls" in (reason(plain.name) or ""))
	frappe.conf["pulse_ssh_roles"] = None
	check("with no list any role is allowed", reason(prod.name) is None and reason(plain.name) is None)
	frappe.conf["pulse_ssh_roles"] = []
	check("an empty list means no limit", reason(prod.name) is None)

	# ---- Test Connection and the poller never reach the network for a refused host ----
	frappe.conf["pulse_ssh_roles"] = ["Staging"]
	frappe.set_user("Administrator")
	out = ss.test_connection(prod.name)
	check("Test Connection on a refused host answers and does not connect", out["ok"] is False and out["kind"] == "config" and "only polls" in out["message"] and contacted == [], (out, contacted))
finally:
	ssh_client.collect, frappe.get_cached_doc = orig_collect, orig_get_cached
	frappe.conf["pulse_ssh_roles"] = orig_roles
	frappe.db.rollback()

check("nothing in this test contacted Warpgate", contacted == [], contacted)
width = max(len(n) for n, _, _ in results)
for n, ok, d in results:
	print(("PASS " if ok else "FAIL ") + n.ljust(width), "" if ok else f"-> {d}")
print("ALL PASS" if all(ok for _, ok, _ in results) else "SOME FAILED")
