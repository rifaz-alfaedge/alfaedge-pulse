# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from __future__ import annotations

from frappe.model.document import Document


class UptimeKumaInstance(Document):
	"""One monitored Uptime Kuma deployment (of potentially several, at
	different locations). Credentials here are used two ways — see
	alfaedge_pulse.uptime_kuma_client:

	- Socket.IO login (username/password) for admin-initiated monitor
	  management (add/edit/delete/pause/resume) — an unofficial but
	  currently-working Kuma API.
	- HTTP Basic Auth (username/password) or an API Key, for the official,
	  stable Prometheus /metrics endpoint polled every minute for status.
	"""
