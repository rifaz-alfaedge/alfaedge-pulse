# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class FrappeFailedJobLog(Document):
	"""One RQ job currently (or previously) failing on one bench on one
	Monitored Host — upserted by rq_job_id via ingest.py's push_status,
	same pattern as LLM Usage Log's _upsert_log. Purely historical/
	drill-down; carries no alerting logic of its own.
	"""
