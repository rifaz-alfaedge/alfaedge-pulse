"""db_indexes.ensure_indexes: logic, idempotence, re-creation and the speed-up. Run with:
cd /home/ubuntu/frappe-bench && bench --site alfaedge execute 'exec(open("/home/ubuntu/frappe-bench/apps/alfaedge_pulse/tests/manual/test_db_indexes.py").read(), {"__name__": "t"})'
(Index changes are DDL, which cannot be rolled back; the test only drops and re-adds one small index.)
"""
import time

import frappe

from alfaedge_pulse import db_indexes

results = []


def check(name, ok, detail=""):
	results.append((name, bool(ok), detail))


# ---- the matching rule ----
keys = [["monitored_host", "bench_name", "timestamp"], ["timestamp"]]
check("an identical index counts", db_indexes.has_index(keys, ["timestamp"]))
check("a wider index serves a shorter lookup", db_indexes.has_index(keys, ["monitored_host"]) and db_indexes.has_index(keys, ["monitored_host", "bench_name"]))
check("columns in another order do not count", not db_indexes.has_index(keys, ["bench_name", "monitored_host"]))
check("a later column alone does not count", not db_indexes.has_index(keys, ["bench_name"]))
check("a longer index than exists is missing", not db_indexes.has_index([["monitored_host"]], ["monitored_host", "timestamp"]))
check("no indexes at all", not db_indexes.has_index([], ["timestamp"]))

# ---- everything is in place, and running again changes nothing ----
db_indexes.ensure_indexes()
missing = []
for doctype, columns, _why in db_indexes.INDEXES:
	if frappe.db.table_exists(doctype, cached=False) and not db_indexes.has_index(db_indexes._existing_keys(doctype), columns):
		missing.append((doctype, columns))
check("every index in the list exists", not missing, missing)
check("a second run creates nothing", db_indexes.ensure_indexes() == [])
check("the list covers the tables the retention purge reads", {"Frappe Worker Health Log", "Resource Metric Log", "Resource Disk Sample", "Memory Metric Log", "Disk IO Sample", "MariaDB Metric Log", "MariaDB Config Snapshot"} <= {d for d, _, _ in db_indexes.INDEXES})

# ---- a missing index is put back ----
doctype, cols = "MariaDB Config Snapshot", ["collected_at"]
by_key = {}
for r in frappe.db.sql("show index from `tabMariaDB Config Snapshot`", as_dict=True):
	by_key.setdefault(r["Key_name"], []).append(r["Column_name"])
name = next(k for k, v in by_key.items() if v == ["collected_at"])  # the single-column one, not the composite
frappe.db.sql_ddl(f"alter table `tabMariaDB Config Snapshot` drop index `{name}`")
remaining = [c for c in by_key.values() if c != ["collected_at"]]
check("the index really was removed for the test", not db_indexes.has_index(remaining, cols))
made = db_indexes.ensure_indexes(say=lambda m: None)
check("a dropped index is created again", made == ["MariaDB Config Snapshot (collected_at)"], made)

# ---- a failing index never fails the migrate ----
orig = frappe.db.add_index


def boom(*a, **k):
	raise RuntimeError("disk full")


frappe.db.sql_ddl("alter table `tabMariaDB Config Snapshot` drop index `collected_at_index`") if db_indexes.has_index(db_indexes._existing_keys(doctype), cols) else None
frappe.db.add_index = boom
try:
	out = db_indexes.ensure_indexes(say=lambda m: None)
	check("a failure is swallowed and nothing is reported as created", out == [], out)
finally:
	frappe.db.add_index = orig
	frappe.db.rollback()
db_indexes.ensure_indexes()
check("and the next run puts it back", db_indexes.has_index(db_indexes._existing_keys(doctype), cols))

# ---- it is wired in ----
hooks = open(frappe.get_app_path("alfaedge_pulse", "hooks.py")).read()
install = open(frappe.get_app_path("alfaedge_pulse", "install.py")).read()
check("migrate runs it", "alfaedge_pulse.db_indexes.ensure_indexes" in hooks)
check("a fresh install runs it", "ensure_indexes()" in install)

# ---- the lookup that took 5 s is now an index lookup ----
host = frappe.db.get_value("Frappe Worker Health Log", {}, "monitored_host")
if host:
	sql = "SELECT orphan_critical_streak FROM `tabFrappe Worker Health Log` WHERE monitored_host=%s AND bench_name=%s ORDER BY timestamp DESC LIMIT 1"
	bench = frappe.db.get_value("Frappe Worker Health Log", {"monitored_host": host}, "bench_name")
	plan = frappe.db.sql("explain " + sql, (host, bench), as_dict=True)[0]
	t = time.time()
	frappe.db.sql(sql, (host, bench))
	took = time.time() - t
	check("the latest-worker-health lookup uses an index", plan["key"] and plan["type"] == "ref", plan)
	check("and takes well under a second", took < 0.5, round(took, 3))

width = max(len(n) for n, _, _ in results)
for n, ok, d in results:
	print(("PASS " if ok else "FAIL ") + n.ljust(width), "" if ok else f"-> {d}")
print("ALL PASS" if all(ok for _, ok, _ in results) else "SOME FAILED")
