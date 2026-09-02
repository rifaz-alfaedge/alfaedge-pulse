# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class FrappeWorkerHealthLog(Document):
	"""One poll cycle's RQ worker/queue/failed-job snapshot for one bench on
	one Monitored Host — genuine append-only history, inserted fresh every
	push by ingest.py's push_status. See its own field descriptions for how
	the critical-tier streaks are computed without a persistent "current"
	doc to mutate.
	"""
