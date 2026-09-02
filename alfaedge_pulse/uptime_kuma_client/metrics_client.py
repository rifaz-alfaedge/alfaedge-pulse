# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Reads Uptime Kuma's official, stable Prometheus /metrics endpoint — the
one auth-and-poll path this integration leans on for anything alerting
depends on (see socketio_client.py's module docstring for why the write
path is kept separate from this read path).

Auth is always HTTP Basic Auth at the transport level (confirmed from
Kuma's own ``server/auth.js``): with no API Key configured, it's the
real username/password; once an instance has an API Key, Kuma's
``apiAuthorizer`` ignores the Basic Auth *username* entirely and checks
only the *password* field against the key — so an API Key is sent as
Basic Auth with an empty username, not a Bearer token.
"""

from __future__ import annotations

import re

import requests

from alfaedge_pulse.uptime_kuma_client.base import UptimeKumaAPIError

REQUEST_TIMEOUT = 10

_METRIC_LINE_RE = re.compile(
	r"^(?P<metric>monitor_status|monitor_response_time)\{(?P<labels>[^}]*)\}\s+(?P<value>[-\d.eE+]+)\s*$"
)
_LABEL_RE = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')


def fetch_metrics(base_url: str, username: str, password: str, api_key: str | None = None, verify_ssl: bool = True) -> dict[str, dict]:
	"""GET /metrics and return ``{monitor_name: {"status": float|None, "response_time_ms": float|None}}``."""
	url = f"{base_url.rstrip('/')}/metrics"
	auth = ("", api_key) if api_key else (username, password)
	try:
		response = requests.get(url, auth=auth, timeout=REQUEST_TIMEOUT, verify=bool(verify_ssl))
		response.raise_for_status()
	except requests.RequestException as e:
		raise UptimeKumaAPIError(f"{url} failed: {e}") from e
	return _parse_metrics(response.text)


def _parse_metrics(text: str) -> dict[str, dict]:
	result: dict[str, dict] = {}
	for line in text.splitlines():
		line = line.strip()
		if not line or line.startswith("#"):
			continue
		match = _METRIC_LINE_RE.match(line)
		if not match:
			continue
		labels = {k: v.replace('\\"', '"') for k, v in _LABEL_RE.findall(match.group("labels"))}
		monitor_name = labels.get("monitor_name")
		if not monitor_name:
			continue
		value = float(match.group("value"))
		entry = result.setdefault(monitor_name, {"status": None, "response_time_ms": None})
		if match.group("metric") == "monitor_status":
			entry["status"] = value
		else:
			entry["response_time_ms"] = value
	return result
