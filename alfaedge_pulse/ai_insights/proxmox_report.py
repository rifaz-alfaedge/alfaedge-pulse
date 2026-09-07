# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Weekly AI-generated report over the Proxmox fleet: host/guest resource
history, alerts, and backups from the last 7 days — right-sizing
suggestions, recurring issues, action items. Completely independent of
host_health_report.py (the user explicitly wants these analyzed and
reported separately, not blended), even though both share the same
Weekly AI Report doctype shape and delivery helpers.

Generated once PER Proxmox Server role (Production, Development, Staging,
...), not once for the whole fleet — a fleet with both production and
development servers blended into one report buries production findings
under development noise, and the two audiences usually care about
different things. A server with no role set falls into the "Unassigned"
bucket rather than being silently dropped from every report.

Aggregates into compact JSON before ever touching the LLM — sending 7
days of raw Proxmox Host/Guest Metric Log rows (tens of thousands of them
across a real fleet) would be needlessly expensive and mostly noise for
what's fundamentally a "what's the trend, what's out of line" question.
"""

from __future__ import annotations

import json
from functools import partial

import frappe

from alfaedge_pulse.ai_insights.orchestration import is_report_enabled, run_report

# Sustained-usage thresholds for flagging right-sizing candidates in the
# aggregated data handed to the LLM — deliberately generous (avg, not a
# single spike) so a guest that's merely had one busy afternoon isn't
# flagged as a resize candidate; these are about a 7-day *pattern*.
_LOW_USAGE_AVG_PERCENT = 15
_HIGH_USAGE_AVG_PERCENT = 80

_UNASSIGNED_ROLE = "Unassigned"


def _servers_by_role() -> dict[str, list[str]]:
	"""Every enabled Proxmox Server's name, grouped by role. A server
	with no role set falls into "Unassigned" rather than being silently
	excluded from every report. Only roles that actually have a server
	appear as a key, so the caller naturally skips generating an empty
	report for a role nobody has hardware in."""
	servers = frappe.get_all("Proxmox Server", filters={"enabled": 1}, fields=["name", "role"])
	grouped: dict[str, list[str]] = {}
	for s in servers:
		grouped.setdefault(s.role or _UNASSIGNED_ROLE, []).append(s.name)
	return grouped


def _aggregate_host_metrics(cutoff, server_names: list[str]) -> dict:
	if not server_names:
		return {}
	rows = frappe.db.sql(
		"""
		select server,
			round(avg(cpu_usage), 1) as avg_cpu, round(max(cpu_usage), 1) as max_cpu,
			round(avg(memory_usage), 1) as avg_mem, round(max(memory_usage), 1) as max_mem,
			round(avg(swap_usage), 1) as avg_swap, round(max(swap_usage), 1) as max_swap,
			round(avg(storage_usage), 1) as avg_disk, round(max(storage_usage), 1) as max_disk
		from `tabProxmox Host Metric Log`
		where collected_at >= %(cutoff)s and server in %(server_names)s
		group by server
		""",
		{"cutoff": cutoff, "server_names": server_names},
		as_dict=True,
	)
	return {r["server"]: r for r in rows}


def _aggregate_guest_metrics(cutoff, server_names: list[str]) -> dict:
	if not server_names:
		return {}
	rows = frappe.db.sql(
		"""
		select guest,
			round(avg(cpu_usage), 1) as avg_cpu, round(max(cpu_usage), 1) as max_cpu,
			round(avg(memory_usage), 1) as avg_mem, round(max(memory_usage), 1) as max_mem,
			round(avg(disk_usage), 1) as avg_disk, round(max(disk_usage), 1) as max_disk
		from `tabProxmox Guest Metric Log`
		where collected_at >= %(cutoff)s and server in %(server_names)s
		group by guest
		""",
		{"cutoff": cutoff, "server_names": server_names},
		as_dict=True,
	)
	return {r["guest"]: r for r in rows}


def _aggregate_alerts(cutoff, server_names: list[str], guest_names: list[str]) -> list[dict]:
	# Proxmox Alert Log has no `server` column — it's a Dynamic Link
	# (reference_doctype/reference_name), pointing at either a Proxmox
	# Server (host-level alerts) or a Proxmox Guest (guest-level alerts).
	# Scoping to a role means matching either kind of reference name.
	reference_names = server_names + guest_names
	if not reference_names:
		return []
	return frappe.db.sql(
		"""
		select alert_type, count(*) as occurrences,
			sum(case when resolved = 0 then 1 else 0 end) as still_open
		from `tabProxmox Alert Log`
		where sent_at >= %(cutoff)s and reference_name in %(reference_names)s
		group by alert_type
		order by occurrences desc
		""",
		{"cutoff": cutoff, "reference_names": reference_names},
		as_dict=True,
	)


def _aggregate_backups(cutoff, server_names: list[str]) -> list[dict]:
	if not server_names:
		return []
	return frappe.db.sql(
		"""
		select server, status, count(*) as occurrences
		from `tabProxmox Backup Log`
		where backup_time >= %(cutoff)s and server in %(server_names)s
		group by server, status
		order by server, status
		""",
		{"cutoff": cutoff, "server_names": server_names},
		as_dict=True,
	)


def build_proxmox_weekly_data(cutoff, server_names: list[str]) -> dict:
	"""Compact, LLM-ready snapshot of the last 7 days across one role's
	worth of the Proxmox fleet (see _servers_by_role) — one dict per
	host/guest with actual usage (from Resource History) alongside
	allocated capacity (already captured by the live poller, no new API
	calls), plus counts scoped to just these servers' alerts/backups."""
	host_metrics = _aggregate_host_metrics(cutoff, server_names)
	guest_metrics = _aggregate_guest_metrics(cutoff, server_names)

	servers = frappe.get_all(
		"Proxmox Server",
		filters={"name": ["in", server_names]},
		fields=["name", "server_name", "role", "server_type", "cores", "memory_total", "storage_total"],
	)
	hosts = []
	for s in servers:
		metrics = host_metrics.get(s.name, {})
		hosts.append(
			{
				"name": s.server_name,
				"role": s.role,
				"type": s.server_type,
				"allocated": {"cpu_cores": s.cores, "memory_gb": s.memory_total, "root_disk_gb": s.storage_total},
				"usage_7d": metrics or "no data yet",
			}
		)

	guests = frappe.get_all(
		"Proxmox Guest",
		filters={"server": ["in", server_names]},
		fields=["name", "guest_name", "server", "guest_type", "status", "cpus", "memory_total", "disk_total"],
	)
	guest_list = []
	for g in guests:
		metrics = guest_metrics.get(g.name, {})
		flag = None
		if metrics:
			if metrics.get("avg_mem") is not None and metrics["avg_mem"] < _LOW_USAGE_AVG_PERCENT:
				flag = "candidate for downsizing (low sustained memory usage)"
			elif metrics.get("avg_mem") is not None and metrics["avg_mem"] > _HIGH_USAGE_AVG_PERCENT:
				flag = "candidate for upsizing (high sustained memory usage)"
		guest_list.append(
			{
				"name": g.guest_name,
				"server": g.server,
				"type": g.guest_type,
				"status": g.status,
				"allocated": {"vcpus": g.cpus, "memory_gb": g.memory_total, "disk_gb": g.disk_total},
				"usage_7d": metrics or "no data yet",
				"flag": flag,
			}
		)

	return {
		"hosts": hosts,
		"guests": guest_list,
		"alerts_7d": _aggregate_alerts(cutoff, server_names, [g["name"] for g in guests]),
		"backups_7d": _aggregate_backups(cutoff, server_names),
	}


def _build_prompt(data: dict, period_start, period_end, role: str) -> str:
	return f"""You are a Proxmox infrastructure analyst. Below is 7 days of aggregated \
monitoring data ({period_start} to {period_end}) for the **{role}** segment of a Proxmox VE \
fleet only — host and guest resource usage (with allocated capacity alongside actual usage), \
alert counts, and backup outcomes for {role} servers specifically. Other environments (if any) \
are analyzed and reported separately, so do not speculate about servers outside this data. \
Write a concise, plain-English report a busy system administrator can act on directly. Use \
exactly these four Markdown sections, in this order:

## Executive Summary
2-4 sentences on overall {role} fleet health this week.

## Right-Sizing Suggestions
For any host/guest whose allocated capacity looks mismatched with actual usage (over- or \
under-provisioned), name it specifically and say what to change and why. If nothing stands \
out, say so plainly — don't invent suggestions.

## Recurring Issues
Alert types or backup failures that happened more than once, grouped sensibly, with likely \
root causes if the data suggests one (e.g. a resource spike correlating with a scheduled \
job).

## Action Items
A short, prioritized checklist — the concrete next steps, most important first.

Be specific and quantitative where the data supports it (percentages, counts). Do not pad \
with generic advice not grounded in the data below.

DATA:
{json.dumps(data, indent=2, default=str)}
"""


def generate_weekly_report() -> None:
	"""Cron entrypoint (see hooks.py) — one Weekly AI Report per Proxmox
	Server role that currently has at least one enabled server (see
	_servers_by_role), so Production/Development/Staging/... stay
	separate rather than blended into one report."""
	if not is_report_enabled("proxmox_report_enabled"):
		return
	for role, server_names in _servers_by_role().items():
		run_report(
			"Proxmox Fleet",
			partial(build_proxmox_weekly_data, server_names=server_names),
			partial(_build_prompt, role=role),
			role=role,
		)


_MANUAL_RUN_JOB_ID = "alfaedge_pulse_proxmox_weekly_report_manual"


def _run_now_job() -> None:
	"""Background-job target enqueued by run_now() below — same
	generation body as the scheduled cron path (one report per role),
	just without the enabled/proxmox_report_enabled gate (a manual click
	is itself the deliberate trigger)."""
	for role, server_names in _servers_by_role().items():
		run_report(
			"Proxmox Fleet",
			partial(build_proxmox_weekly_data, server_names=server_names),
			partial(_build_prompt, role=role),
			role=role,
		)


@frappe.whitelist()
def run_now() -> dict:
	"""Whitelisted handler for AI Insights Settings' "Generate Proxmox
	Fleet Report Now" button. Enqueues the work rather than running it
	inline — an LLM call plus aggregation can take anywhere from several
	seconds to well over gunicorn's 120s worker timeout (see
	llm_usage_monitor.bifrost_settings.sync_now's identical reasoning for
	its own "Sync Now" button), which would otherwise kill the request
	mid-flight and leave the browser with a dropped connection instead of
	a clean result — more so now that one click can generate several
	reports (one per role) in sequence.
	"""
	frappe.only_for(("System Manager", "Proxmox Monitor Manager"))
	frappe.enqueue(
		"alfaedge_pulse.ai_insights.proxmox_report._run_now_job",
		queue="long",
		timeout=600,
		job_id=_MANUAL_RUN_JOB_ID,
		deduplicate=True,
	)
	return {
		"ok": True,
		"message": frappe._("Proxmox Fleet report generation started in the background (one report per server role) — check the Weekly AI Report list in a moment."),
	}
