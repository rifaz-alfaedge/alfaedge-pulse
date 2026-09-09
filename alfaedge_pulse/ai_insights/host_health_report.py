# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Weekly AI-generated report over Host Health: this bench's own worker/
job/service health and disk-capacity forecasts from the last 7 days.
Completely independent of proxmox_report.py — see that module's docstring
for why the user wants these kept separate rather than blended.
"""

from __future__ import annotations

import json

import frappe

from alfaedge_pulse.ai_insights.orchestration import generate_weekly_report as _generate_weekly_report
from alfaedge_pulse.ai_insights.orchestration import run_report
from alfaedge_pulse.host_health.api import _parse_exc_message


def _aggregate_worker_health(cutoff) -> list[dict]:
	"""Per (monitored_host, bench_name) trend over the window — same
	group-by shape as the dashboard's own latestPerBench, but averaged
	over 7 days rather than "latest row" since a weekly report cares
	about the pattern, not a single snapshot."""
	return frappe.db.sql(
		"""
		select monitored_host, bench_name,
			round(avg(orphan_worker_count), 1) as avg_orphans, max(orphan_worker_count) as max_orphans,
			round(avg(failed_job_count), 1) as avg_failed_jobs, max(failed_job_count) as max_failed_jobs,
			round(avg(long_running_job_count), 1) as avg_long_running
		from `tabFrappe Worker Health Log`
		where timestamp >= %(cutoff)s
		group by monitored_host, bench_name
		""",
		{"cutoff": cutoff},
		as_dict=True,
	)


def _aggregate_failed_jobs(cutoff) -> list[dict]:
	"""Fleet-wide root-cause grouping over the window — same
	failure_signature grouping as host_health/api.py's
	_grouped_failed_jobs, but spanning every host at once (a weekly
	report wants "what broke across the fleet," not one host at a time)
	and scoped to this window rather than "still open right now"."""
	rows = frappe.get_all(
		"Frappe Failed Job Log",
		filters={"last_seen": [">=", cutoff]},
		fields=["monitored_host", "bench_name", "job_name", "exc_type", "exc_info", "failure_signature", "resolved"],
		order_by="last_seen desc",
		ignore_permissions=True,
	)
	groups: dict[str, list[dict]] = {}
	for row in rows:
		groups.setdefault(row["failure_signature"], []).append(row)
	return [
		{
			"exc_type": occurrences[0]["exc_type"] or "Unknown error",
			"job_name": occurrences[0]["job_name"] or "unknown method",
			"message": _parse_exc_message(occurrences[0]["exc_info"]),
			"hosts": sorted({o["monitored_host"] for o in occurrences}),
			"occurrence_count": len(occurrences),
			"still_open": any(not o["resolved"] for o in occurrences),
		}
		for occurrences in sorted(groups.values(), key=len, reverse=True)
	]


def _aggregate_service_status() -> list[dict]:
	"""Current-state snapshot, not a 7-day aggregate — a service's
	down_streak already carries its own trend, and there's no value in
	averaging a categorical current_state over time."""
	return frappe.get_all(
		"Service Status Log",
		filters={"down_streak": [">", 0]},
		fields=["monitored_host", "service_name", "current_state", "down_streak"],
		ignore_permissions=True,
	)


def build_host_health_weekly_data(cutoff) -> dict:
	return {
		"worker_health_7d": _aggregate_worker_health(cutoff),
		"failed_jobs_7d": _aggregate_failed_jobs(cutoff),
		"services_currently_flapping": _aggregate_service_status(),
	}


def _build_prompt(data: dict, period_start, period_end) -> str:
	return f"""You are a Frappe/Python debugging assistant. Below is 7 days of aggregated Host \
Health monitoring data ({period_start} to {period_end}) for a Frappe bench's own servers — \
background worker health, recurring failed jobs (with exception type and message), and \
flapping services. Write a concise, plain-English report a busy administrator can act on \
directly. Use exactly these three Markdown sections, in this order:

## Executive Summary
2-4 sentences on overall bench stability this week — is it quiet, or is something recurring.

## Recurring Errors & Recommended Fixes
For each recurring failed-job error in the data, explain what is failing (job, exception type \
and message), how often and on which hosts, and give a specific, actionable recommended fix — \
a code/config change, a missing null check, a timeout to raise, a retry to add, an upstream \
dependency to check, etc. Do the same for any flapping services, with a likely cause and fix \
if the data suggests one. If nothing recurred, say so plainly.

## Action Items
A short, prioritized checklist — the concrete next steps, most important first.

Do NOT suggest CPU/RAM/disk resizing, scaling, or any capacity/resource-usage changes — that \
is already covered by a separate Proxmox Fleet report. Stay focused on Frappe-level errors and \
their fixes. Be specific and quantitative where the data supports it. Do not pad with generic \
advice not grounded in the data below.

DATA:
{json.dumps(data, indent=2, default=str)}
"""


def generate_weekly_report() -> None:
	"""Cron entrypoint (see hooks.py). Thin wrapper supplying this report's
	specific data/prompt builders to the shared orchestration in
	ai_insights.orchestration — see that module for the settings gate,
	error handling, and delivery logic common to both weekly reports."""
	_generate_weekly_report(
		report_type="Host Health",
		enabled_field="host_health_report_enabled",
		build_data=build_host_health_weekly_data,
		build_prompt=_build_prompt,
	)


_MANUAL_RUN_JOB_ID = "alfaedge_pulse_host_health_weekly_report_manual"


def _run_now_job() -> None:
	"""Background-job target enqueued by run_now() below — see
	proxmox_report._run_now_job's docstring."""
	run_report("Host Health", build_host_health_weekly_data, _build_prompt)


@frappe.whitelist()
def run_now() -> dict:
	"""Whitelisted handler for AI Insights Settings' "Generate Host Health
	Report Now" button — see proxmox_report.run_now's docstring for why
	this enqueues rather than running inline, and why it bypasses the
	enabled/host_health_report_enabled toggles."""
	frappe.only_for(("System Manager", "Proxmox Monitor Manager"))
	frappe.enqueue(
		"alfaedge_pulse.ai_insights.host_health_report._run_now_job",
		queue="long",
		timeout=300,
		job_id=_MANUAL_RUN_JOB_ID,
		deduplicate=True,
	)
	return {
		"ok": True,
		"message": frappe._("Host Health report generation started in the background — check the Weekly AI Report list in a moment."),
	}
