# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Shared error type for both Uptime Kuma client modules (Socket.IO and
Prometheus /metrics) — see socketio_client.py and metrics_client.py."""

from __future__ import annotations


class UptimeKumaAPIError(Exception):
	"""Raised for any failure talking to an Uptime Kuma instance, via
	either its Socket.IO API or its /metrics endpoint.

	Callers (the poller, the dashboard API) catch this per-instance so one
	unreachable Kuma instance never blocks the others.
	"""
