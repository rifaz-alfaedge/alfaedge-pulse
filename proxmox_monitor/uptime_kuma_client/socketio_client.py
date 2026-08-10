# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Purpose-built client for Uptime Kuma's internal Socket.IO API — used
*only* for admin-initiated monitor management (add/edit/delete/pause/
resume) from this app's dashboard.

This is explicitly Kuma's *unofficial* API — its own docs call it
"primarily designed for the application's own use... not officially
supported for third-party integrations... may break without notice." We
accept that risk deliberately, and only for this one low-frequency,
admin-initiated feature: the actual status polling that alerting depends
on instead uses Kuma's official, stable Prometheus /metrics endpoint (see
metrics_client.py). If a future Kuma upgrade breaks the event
names/payload shape below, only "manage sites from this dashboard" needs
a fix — monitoring/alerting keeps working regardless.

Event names, auth flow, and the ``add``/``editMonitor`` payload shape
below were confirmed directly against Uptime Kuma's live `master` source
(``server/server.js``, ``server/model/monitor.js``,
``src/pages/EditMonitor.vue``) at the time this was written — not the
long-unmaintained community ``uptime-kuma-api`` PyPI wrapper (last
released 2023). If monitor creation starts failing after a Kuma upgrade,
re-check those files for what changed.
"""

from __future__ import annotations

import socketio
from frappe.utils import cint

from proxmox_monitor.uptime_kuma_client.base import UptimeKumaAPIError

#: This app's Monitor Type options -> Kuma's own `type` slug (confirmed
#: against src/pages/EditMonitor.vue's <option value="..."> list).
KUMA_MONITOR_TYPE = {"HTTP(s)": "http", "TCP": "port", "Ping": "ping"}

CONNECT_TIMEOUT = 15
CALL_TIMEOUT = 15


class UptimeKumaClient:
	"""One short-lived, authenticated Socket.IO session against a single
	Uptime Kuma instance. Meant to be used for a single call or a small
	batch and then disconnected — not held open, since this app only ever
	needs it for admin-initiated, infrequent actions."""

	def __init__(self, base_url: str, username: str, password: str, verify_ssl: bool = True):
		self._sio = socketio.Client(ssl_verify=bool(verify_ssl))
		try:
			self._sio.connect(base_url, transports=["websocket"], wait_timeout=CONNECT_TIMEOUT)
			result = self._sio.call("login", {"username": username, "password": password}, timeout=CALL_TIMEOUT)
			if not result or not result.get("ok"):
				raise UptimeKumaAPIError((result or {}).get("msg") or "Login failed")
		except UptimeKumaAPIError:
			self._safe_disconnect()
			raise
		except socketio.exceptions.SocketIOError as e:
			self._safe_disconnect()
			raise UptimeKumaAPIError(f"Could not connect to {base_url}: {e}") from e

	def _safe_disconnect(self) -> None:
		try:
			self._sio.disconnect()
		except Exception:
			pass

	def _call(self, event: str, *args):
		try:
			return self._sio.call(event, *args, timeout=CALL_TIMEOUT)
		except socketio.exceptions.SocketIOError as e:
			raise UptimeKumaAPIError(f"{event} failed: {e}") from e

	def add_monitor(self, site) -> int:
		"""Create a new monitor from an ``Uptime Site`` doc (unsaved or
		saved — only its own fields are read). Returns Kuma's monitor ID."""
		result = self._call("add", _build_monitor_payload(site))
		if not result or not result.get("ok"):
			raise UptimeKumaAPIError((result or {}).get("msg") or "Failed to create monitor")
		return result["monitorID"]

	def edit_monitor(self, monitor_id: int, site) -> None:
		payload = _build_monitor_payload(site)
		payload["id"] = monitor_id
		result = self._call("editMonitor", payload)
		if not result or not result.get("ok"):
			raise UptimeKumaAPIError((result or {}).get("msg") or "Failed to update monitor")

	def delete_monitor(self, monitor_id: int) -> None:
		result = self._call("deleteMonitor", monitor_id)
		if not result or not result.get("ok"):
			raise UptimeKumaAPIError((result or {}).get("msg") or "Failed to delete monitor")

	def pause_monitor(self, monitor_id: int) -> None:
		result = self._call("pauseMonitor", monitor_id)
		if not result or not result.get("ok"):
			raise UptimeKumaAPIError((result or {}).get("msg") or "Failed to pause monitor")

	def resume_monitor(self, monitor_id: int) -> None:
		result = self._call("resumeMonitor", monitor_id)
		if not result or not result.get("ok"):
			raise UptimeKumaAPIError((result or {}).get("msg") or "Failed to resume monitor")

	def disconnect(self) -> None:
		self._safe_disconnect()

	def __enter__(self):
		return self

	def __exit__(self, *exc):
		self.disconnect()


def _build_monitor_payload(site) -> dict:
	"""Map an Uptime Site doc to Kuma's add/editMonitor payload shape.

	A handful of fields below (accepted_statuscodes, conditions,
	kafkaProducerBrokers, kafkaProducerSaslOptions, rabbitmqNodes) are
	irrelevant to the HTTP(s)/TCP/Ping types this app creates, but Kuma's
	own ``add``/``editMonitor`` handlers unconditionally JSON.stringify
	them — omitting them entirely risks sending ``undefined`` where valid
	JSON is expected, so they're always included with a harmless default.
	"""
	interval = cint(site.check_interval_seconds) or 60
	payload = {
		"name": site.site_name,
		"type": KUMA_MONITOR_TYPE[site.monitor_type],
		"interval": interval,
		"retryInterval": interval,
		"resendInterval": 0,
		"maxretries": 0,
		"active": bool(site.is_active),
		"upsideDown": False,
		"notificationIDList": {},
		"accepted_statuscodes": ["200-299"],
		"conditions": [],
		"kafkaProducerBrokers": [],
		"kafkaProducerSaslOptions": {},
		"rabbitmqNodes": [],
	}
	if site.monitor_type == "HTTP(s)":
		payload.update({"url": site.url, "method": "GET", "maxredirects": 10, "ignoreTls": False})
	elif site.monitor_type == "TCP":
		payload.update({"hostname": site.hostname, "port": cint(site.port)})
	elif site.monitor_type == "Ping":
		payload.update({"hostname": site.hostname, "packetSize": 56})
	return payload
