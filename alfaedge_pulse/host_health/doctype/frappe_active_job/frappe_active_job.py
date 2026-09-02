# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class FrappeActiveJob(Document):
	"""One RQ job currently executing on one bench on one Monitored Host —
	upserted by rq_job_id via ingest.py's push_status, same pattern as
	Frappe Failed Job Log. Unlike that doctype, this is a live snapshot,
	not a history log: a row is deleted outright (not tombstoned) the
	moment its rq_job_id no longer appears in a push, since "a job that
	used to be running" isn't useful drill-down material the way a past
	failure is. Carries no alerting logic of its own — Frappe Worker
	Health Log's long_running_job_count/long_running_job_critical_streak
	is what actually drives the Long Running Job alert.
	"""
