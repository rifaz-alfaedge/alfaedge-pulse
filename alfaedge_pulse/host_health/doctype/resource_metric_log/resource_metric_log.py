# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from __future__ import annotations

from frappe.model.document import Document


class ResourceMetricLog(Document):
	"""One load-average/swap reading for one Monitored Host — genuine
	append-only history, inserted by ingest.py's push_resource_metrics,
	same shape as Frappe Worker Health Log. Streak fields are computed at
	ingest time from the prior row (see _upsert_resource_metrics), not
	mutated on a persistent doc, since there's nothing persistent to mutate
	on an append-only table.
	"""
