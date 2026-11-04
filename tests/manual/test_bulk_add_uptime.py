"""scripts/bulk_add_uptime_sites.py: parsing, planning, pacing, retry, and an
end-to-end add through the real add_site with Kuma replaced. All rolled back;
Kuma is never contacted. Run with:
cd /home/ubuntu/frappe-bench && bench --site alfaedge execute 'exec(open("/home/ubuntu/frappe-bench/apps/alfaedge_pulse/tests/manual/test_bulk_add_uptime.py").read(), {"__name__": "t"})'
"""
import frappe

ns = {"__name__": "lib"}
exec(open("/home/ubuntu/frappe-bench/apps/alfaedge_pulse/scripts/bulk_add_uptime_sites.py").read(), ns)
parse_line, parse_file, plan, bulk_add = ns["parse_line"], ns["parse_file"], ns["plan"], ns["bulk_add"]

results = []


def check(name, ok, detail=""):
	results.append((name, bool(ok), (list(detail) if isinstance(detail, list) else detail)))


def one(*fields):
	return parse_line(list(fields))


# ---- single lines ----
row, why = one("https://shop.example.com/path")
check("a lone URL is a web check named after its host", row and row["monitor_type"] == "HTTP(s)" and row["site_name"] == "shop.example.com" and row["url"] == "https://shop.example.com/path", (row, why))
row, _ = one("Example Shop", "https://shop.example.com")
check("name and URL", row["site_name"] == "Example Shop" and row["check_interval_seconds"] == 60)  # no fleet value passed: the fallback
row, _ = one("API", "https://api.example.com/health", "", "30")
check("an interval column", row["check_interval_seconds"] == 30)
row, _ = one("Database", "db.example.com:3306")
check("host:port is a TCP check", row["monitor_type"] == "TCP" and row["hostname"] == "db.example.com" and row["port"] == 3306 and row["url"] is None, row)
row, _ = one("Gateway", "203.0.113.7", "ping")
check("an IP with type ping", row["monitor_type"] == "Ping" and row["hostname"] == "203.0.113.7", row)
row, _ = one("Gateway", "gw.example.com")
check("a bare host is a ping check", row["monitor_type"] == "Ping")
row, _ = one("Site", "shop.example.com", "http")
check("a web check without a scheme gets https://", row["url"] == "https://shop.example.com", row)
row, _ = one("Site", "shop.example.com:8443", "tcp", "")
check("type column wins over the guess", row["monitor_type"] == "TCP" and row["port"] == 8443)
row, _ = one("Mixed", "HTTPS://Shop.Example.com")
check("scheme is case-insensitive", row and row["monitor_type"] == "HTTP(s)", row)

for label, fields, fragment in (
	("a bad web address", ["x", "https://bad host/"], "valid web address"),
	("port out of range", ["x", "db.example.com:99999"], "host:port"),
	("a non-numeric port", ["x", "db.example.com:abc"], "host:port"),
	("an impossible IP", ["x", "999.1.1.1", "ping"], "valid host"),
	("an interval of zero", ["x", "https://a.example.com", "", "0"], "interval"),
	("an interval beyond a day", ["x", "https://a.example.com", "", "86401"], "interval"),
	("an interval that is not a number", ["x", "https://a.example.com", "", "fast"], "interval"),
	("an unknown type", ["x", "https://a.example.com", "dns"], "unknown type"),
	("a name with no target", ["Name only", ""], "no target"),
):
	row, why = parse_line(fields)
	check(f"rejects {label}", row is None and fragment in (why or ""), why)

row, _ = one("Fast", "https://a.example.com", "", "15")
check("a 15 second interval is accepted", row and row["check_interval_seconds"] == 15, row)
row, _ = parse_line(["Fleet", "https://a.example.com"], default_interval=15)
check("a line with no interval takes the default it is given", row["check_interval_seconds"] == 15, row)
row, _ = parse_line(["Own", "https://a.example.com", "", "30"], default_interval=15)
check("an interval on the line still wins", row["check_interval_seconds"] == 30, row)

# the default comes from Uptime Monitor Settings
orig_get_single = frappe.db.get_single_value
frappe.db.get_single_value = lambda dt, field, *a, **k: 15 if (dt, field) == ("Uptime Monitor Settings", "heartbeat_interval_seconds") else orig_get_single(dt, field, *a, **k)
try:
	check("fleet_interval reads the setting", ns["fleet_interval"]() == 15)
	add15, _, _ = plan("A,https://a.example.com\nB,https://b.example.com,,45\n", existing=[])
	check("plan gives lines without an interval the fleet value", [r["check_interval_seconds"] for _, r in add15] == [15, 45], [r["check_interval_seconds"] for _, r in add15])
	frappe.db.get_single_value = lambda dt, field, *a, **k: None
	addf, _, _ = plan("A,https://a.example.com\n", existing=[])
	check("with no setting the fallback is 60", addf[0][1]["check_interval_seconds"] == 60)
finally:
	frappe.db.get_single_value = orig_get_single

# ---- whole files ----
text = '﻿name,target,type,interval\n# a comment\n\n"Shop, Main",https://shop.example.com\nDB,db.example.com:3306\nBad,https://bad host\n'.lstrip("﻿")
parsed = parse_file(text)
check("header, comment and blank lines are ignored", [n for n, _, _ in parsed] == [4, 5, 6], [n for n, _, _ in parsed])
check("a quoted name may contain a comma", parsed[0][1]["site_name"] == "Shop, Main", parsed[0][1])
check("a bad line is reported with its line number", parsed[2][1] is None and parsed[2][0] == 6)

add, skipped, rejected = plan("A,https://a.example.com\na,https://again.example.com\nB,https://b.example.com\nC,https://c.example.com\nbad,https://bad host\n", existing=["b"])
check("existing names (any case) are skipped", [s[1] for s in skipped if s[2] == "already exists"] == ["B"], skipped)
check("a name repeated in the file is skipped", [s[1] for s in skipped if "repeated" in s[2]] == ["a"], skipped)
check("only new, valid lines are added", [r["site_name"] for _, r in add] == ["A", "C"] and len(rejected) == 1, (add, rejected))

# ---- bulk_add behaviour, with the Kuma step replaced ----
orig_get_all = frappe.get_all
saved = {"instances": ["Kuma 1", "Kuma 2"], "sites": []}


def fake_get_all(doctype, *a, **k):
	if doctype == "Uptime Kuma Instance":
		return list(saved["instances"])
	if doctype == "Uptime Site":
		return list(saved["sites"])
	return orig_get_all(doctype, *a, **k)


frappe.get_all = fake_get_all
try:
	file = "A,https://a.example.com\nB,https://b.example.com\nC,db.example.com:3306\n"
	calls, pauses, said = [], [], []

	def ok_add(row):
		calls.append(row["site_name"])
		return {"created": ["x"], "errors": []}

	bulk_add(file, dry_run=True, say=said.append, pause=pauses.append, add=ok_add)
	check("a dry run adds nothing and waits for nothing", calls == [] and pauses == [] and "Nothing was added" in said[-1], said[-1])
	check("the preview lists the lines to add", sum("  ADD " in m for m in said) == 3, said[:6])

	said.clear()
	r = bulk_add(file, dry_run=False, say=said.append, pause=pauses.append, add=ok_add)
	check("a real run adds each line in order", calls == ["A", "B", "C"] and r["added"] == ["A", "B", "C"], calls)
	check("it paces itself: 3.2 s per instance per site", pauses == [6.4, 6.4, 6.4], pauses)

	calls.clear(); pauses.clear(); said.clear()
	attempts = {"B": 0}

	def flaky(row):
		if row["site_name"] == "B":
			attempts["B"] += 1
			if attempts["B"] == 1:
				raise Exception("Too frequently, try again later.")
		if row["site_name"] == "C":
			raise Exception("Failed to add this site to any instance: Kuma 1: boom")
		calls.append(row["site_name"])
		return {"created": ["x"], "errors": ["Kuma 2: partial problem"] if row["site_name"] == "A" else []}

	r = bulk_add(file, dry_run=False, say=said.append, pause=pauses.append, add=flaky)
	check("a rate-limit message waits 65 s and retries once", attempts["B"] == 2 and 65 in pauses and "B" in r["added"], (attempts, pauses))
	check("one failing line does not stop the rest", [f[0] for f in r["failed"]] == ["C"] and r["added"] == ["A", "B"], r)
	check("a partly failed add is shown on its line", any("partial problem" in m for m in said), said)
	check("it tells you how to retry", any("same file again" in m for m in said), said[-1])

	said.clear(); calls.clear()
	saved["instances"] = []
	bulk_add(file, dry_run=False, say=said.append, pause=pauses.append, add=ok_add)
	check("with no enabled instance nothing is added", calls == [] and any("No enabled Uptime Kuma instance" in m for m in said), said)
	saved["instances"] = ["Kuma 1"]

	said.clear()
	big = "\n".join(f"s{i},https://s{i}.example.com" for i in range(501))
	r = bulk_add(big, dry_run=False, say=said.append, pause=pauses.append, add=ok_add)
	check("more than 500 lines is refused", r["added"] == [] and any("Too many rows" in m for m in said), said[-1])
finally:
	frappe.get_all = orig_get_all

# ---- end to end through the real add_site, Kuma replaced ----
import alfaedge_pulse.uptime_monitor.api as uptime_api

orig = (uptime_api._create_site_on_instance, frappe.db.commit)
counter = {"n": 5000}


def local_only(instance, site_name, monitor_type, url, hostname, port, interval, is_active):
	counter["n"] += 1
	return frappe.get_doc({"doctype": "Uptime Site", "instance": instance, "site_name": site_name, "monitor_type": monitor_type, "url": url, "hostname": hostname,
	                       "port": port, "check_interval_seconds": interval, "is_active": is_active, "kuma_monitor_id": counter["n"]}).insert(ignore_permissions=True)


uptime_api._create_site_on_instance = local_only
frappe.db.commit = lambda: None
try:
	inst = frappe.get_doc({"doctype": "Uptime Kuma Instance", "instance_name": "ZZ-bulk-test", "base_url": "https://127.0.0.1:9", "username": "u", "password": "p", "enabled": 1}).insert(ignore_permissions=True).name
	only_mine = lambda doctype, *a, **k: [inst] if doctype == "Uptime Kuma Instance" and k.get("filters") == {"enabled": 1} else orig_get_all(doctype, *a, **k)
	frappe.get_all = only_mine
	try:
		bulk_add("ZZ Shop,https://zz-shop.example.com,,45\nZZ DB,zz-db.example.com:3306\nZZ Gate,203.0.113.9,ping\n", dry_run=False, say=lambda m: None, pause=lambda s: None)
	finally:
		frappe.get_all = orig_get_all
	made = {s.site_name: s for s in frappe.get_all("Uptime Site", filters={"instance": inst}, fields=["site_name", "monitor_type", "url", "hostname", "port", "check_interval_seconds", "is_active"])}
	check("three real Uptime Site records were created", set(made) == {"ZZ Shop", "ZZ DB", "ZZ Gate"}, list(made))
	check("web check fields", made["ZZ Shop"].monitor_type == "HTTP(s)" and made["ZZ Shop"].url == "https://zz-shop.example.com" and made["ZZ Shop"].check_interval_seconds == 45, made["ZZ Shop"])
	check("TCP check fields", made["ZZ DB"].monitor_type == "TCP" and made["ZZ DB"].hostname == "zz-db.example.com" and made["ZZ DB"].port == 3306, made["ZZ DB"])
	check("ping check fields, active, the fleet interval by default", made["ZZ Gate"].monitor_type == "Ping" and made["ZZ Gate"].is_active == 1 and made["ZZ Gate"].check_interval_seconds == (int(frappe.db.get_single_value("Uptime Monitor Settings", "heartbeat_interval_seconds") or 0) or 60), made["ZZ Gate"])
	again, skipped, _ = plan("ZZ Shop,https://zz-shop.example.com\n", existing=orig_get_all("Uptime Site", pluck="site_name"))
	check("running the same file again skips what exists", again == [] and skipped and skipped[0][2] == "already exists", skipped)
finally:
	uptime_api._create_site_on_instance, frappe.db.commit = orig
	frappe.get_all = orig_get_all
	frappe.db.rollback()

width = max(len(n) for n, _, _ in results)
for n, ok, d in results:
	print(("PASS " if ok else "FAIL ") + n.ljust(width), "" if ok else f"-> {d}")
print("ALL PASS" if all(ok for _, ok, _ in results) else "SOME FAILED")
