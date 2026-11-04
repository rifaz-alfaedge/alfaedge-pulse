"""Hold back old queued email before the scheduler is resumed.

Frappe's email flush sends everything still marked 'Not Sent' or 'Partially
Sent' once the scheduler runs. After a long pause that would deliver messages
that are days or weeks old. This marks the OLD ones 'Error' (the same way the
copy of production was muted) so they are never sent; nothing is deleted, and
recent emails are left alone.

By default it only LISTS. It changes things only when PULSE_PARK_CONFIRM=yes.
PULSE_PARK_HOURS sets what counts as old (default 24 hours).

    cd /home/ubuntu/frappe-bench
    bench --site alfaedge execute 'exec(open("/home/ubuntu/park_stale_emails.py").read(), {"__name__": "t"})'
    PULSE_PARK_CONFIRM=yes bench --site alfaedge execute 'exec(open("/home/ubuntu/park_stale_emails.py").read(), {"__name__": "t"})'
"""
import collections
import os

import frappe
from frappe.utils import add_to_date, now_datetime

WAITING = ("Not Sent", "Partially Sent")
NOTE = "Held back by hand before the scheduler was resumed, because it was queued long before."


def park_stale_emails(hours=24, dry_run=True, say=print) -> dict:
	cutoff = add_to_date(now_datetime(), hours=-hours)
	waiting = frappe.get_all("Email Queue", filters={"status": ["in", WAITING]}, fields=["name", "creation", "reference_doctype", "sender"], order_by="creation asc", limit_page_length=0)
	old = [e for e in waiting if e.creation < cutoff]
	recent = [e for e in waiting if e.creation >= cutoff]
	say(f"Waiting to be sent: {len(waiting)} | older than {hours} h: {len(old)} | newer: {len(recent)}")
	if old:
		by_what = collections.Counter((e.reference_doctype or "(none)", e.sender or "(no sender)") for e in old)
		for (what, sender), n in by_what.most_common(6):
			say(f"  {n:>4} old  about {what}  from {sender}")
		say(f"  oldest {old[0].creation} | newest of the old ones {old[-1].creation}")
	if recent:
		say(f"These {len(recent)} recent ones WILL be sent when the scheduler runs (newest {recent[-1].creation}).")
	if dry_run or not old:
		say("Nothing was changed." if dry_run else "Nothing old to hold back.")
		return {"old": len(old), "recent": len(recent), "parked": 0}
	names = [e.name for e in old]
	for i in range(0, len(names), 500):
		frappe.db.sql("update `tabEmail Queue` set status = 'Error', error = %(note)s where name in %(names)s", {"note": NOTE, "names": names[i : i + 500]})
	frappe.db.commit()
	say(f"Marked {len(names)} old email(s) as Error. They will not be sent. Nothing was deleted.")
	return {"old": len(old), "recent": len(recent), "parked": len(names)}


if __name__ == "t":  # `bench execute` runs this file with that name; tests import it under another
	park_stale_emails(hours=float(os.environ.get("PULSE_PARK_HOURS", "24")), dry_run=os.environ.get("PULSE_PARK_CONFIRM") != "yes")
