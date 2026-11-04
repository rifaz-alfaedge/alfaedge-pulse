"""/home/ubuntu/park_stale_emails.py on synthetic queued emails, all rolled back. Run with:
cd /home/ubuntu/frappe-bench && bench --site alfaedge execute 'exec(open("/home/ubuntu/frappe-bench/apps/alfaedge_pulse/tests/manual/test_park_stale_emails.py").read(), {"__name__": "t"})'
"""
import frappe
from frappe.utils import add_to_date, now_datetime

ns = {"__name__": "lib"}
exec(open("/home/ubuntu/frappe-bench/apps/alfaedge_pulse/scripts/park_stale_emails.py").read(), ns)
park = ns["park_stale_emails"]
results = []


def check(name, ok, detail=""):
	results.append((name, bool(ok), detail))


orig_commit = frappe.db.commit
frappe.db.commit = lambda: None
try:
	def queue(hours_old, status="Not Sent"):
		doc = frappe.get_doc({"doctype": "Email Queue", "sender": "zz@example.com", "message": "x", "status": status, "reference_doctype": "Task", "recipients": [{"recipient": "a@example.com"}]}).insert(ignore_permissions=True)
		frappe.db.set_value("Email Queue", doc.name, "creation", add_to_date(now_datetime(), hours=-hours_old), update_modified=False)
		return doc.name

	real_before = frappe.db.count("Email Queue", {"status": ["in", ["Not Sent", "Partially Sent"]]})
	# park only my rows: compare against the real ones that were already waiting
	old1, old2, partial, recent, sent_old, errored = queue(200), queue(30), queue(100, "Partially Sent"), queue(2), queue(500, "Sent"), queue(300, "Error")
	said = []
	r = park(hours=24, dry_run=True, say=said.append)
	check("a dry run changes nothing", all(frappe.db.get_value("Email Queue", n, "status") == s for n, s in ((old1, "Not Sent"), (old2, "Not Sent"), (partial, "Partially Sent"), (recent, "Not Sent"))) and "Nothing was changed" in said[-1], said[-1])
	check("it reports the old and the recent ones", r["recent"] >= 1 and r["old"] >= 3, r)
	check("it warns that recent ones will be sent", any("WILL be sent" in m for m in said), said)

	said.clear()
	r = park(hours=24, dry_run=False, say=said.append)
	st = lambda n: frappe.db.get_value("Email Queue", n, "status")
	check("old Not Sent and Partially Sent become Error", st(old1) == "Error" and st(old2) == "Error" and st(partial) == "Error", [st(old1), st(old2), st(partial)])
	check("a recent email is left to send", st(recent) == "Not Sent")
	check("Sent and already-Error emails are untouched", st(sent_old) == "Sent" and st(errored) == "Error")
	check("it says why in the error field", "Held back by hand" in (frappe.db.get_value("Email Queue", old1, "error") or ""))
	check("nothing is deleted", all(frappe.db.exists("Email Queue", n) for n in (old1, old2, partial, recent, sent_old, errored)))
	check("a second run finds nothing old", park(hours=24, dry_run=False, say=lambda m: None)["parked"] == 0)
	# keeping a document type
	def typed(hours_old, doctype):
		name = queue(hours_old)
		frappe.db.set_value("Email Queue", name, "reference_doctype", doctype, update_modified=False)
		return name

	leave, task = typed(300, "Staff Leave"), typed(300, "Task")
	said = []
	r = park(hours=24, dry_run=False, say=said.append, keep=["Staff Leave"])
	st = lambda n: frappe.db.get_value("Email Queue", n, "status")
	check("an old email of a kept type is left to send", st(leave) == "Not Sent" and r["kept"] >= 1, (st(leave), r))
	check("an old email of another type is still held back", st(task) == "Error")
	check("the kept ones are listed as going out", any("kept on purpose" in m for m in said), said)
	check("a longer threshold spares more", park(hours=24 * 365, dry_run=True, say=lambda m: None)["old"] == 0)
finally:
	frappe.db.commit = orig_commit
	frappe.db.rollback()

width = max(len(n) for n, _, _ in results)
for n, ok, d in results:
	print(("PASS " if ok else "FAIL ") + n.ljust(width), "" if ok else f"-> {d}")
print("ALL PASS" if all(ok for _, ok, _ in results) else "SOME FAILED")
