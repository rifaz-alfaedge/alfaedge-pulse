# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Whitelisted read endpoints for the AI Usage dashboard tab.

Aggregates run as raw parameterized SQL rather than ``frappe.qb`` — this
app already uses ``frappe.db.sql`` for its other cross-row aggregates (see
``patches/backfill_guest_last_backup.py``, ``tasks/bifrost_sync.py``'s
processing-reconciliation query), so this stays consistent rather than
introducing a second query-building style for the first time here.

Raw SQL bypasses Frappe's automatic doctype permission checks (unlike
``frappe.get_list``), so every function here explicitly gates on the same
three roles ``LLM Usage Log`` itself grants read access to.
"""

from __future__ import annotations

import frappe
from frappe.utils import add_to_date, cint, flt, get_datetime, nowdate

ALLOWED_ROLES = ("System Manager", "Proxmox Monitor Manager", "Proxmox Monitor Viewer")
ALLOWED_DIMENSIONS = {
	"provider": "provider",
	"model": "model",
	# Bifrost's Virtual Keys are how projects/teams/consumers are scoped in
	# practice — the /api/logs row only carries the key's name (not any
	# team/customer it belongs to), so this is as close to "by project" as
	# the log data itself gets.
	"virtual_key_name": "virtual_key_name",
}
DIMENSION_FALLBACK_LABEL = {
	"virtual_key_name": "(No Virtual Key)",
}
ALLOWED_SORT_FIELDS = {"request_timestamp", "latency_ms", "total_tokens", "total_cost"}
RECENT_REQUEST_FIELDS = [
	"name", "source", "provider", "model", "status", "request_timestamp",
	"latency_ms", "total_tokens", "total_cost", "virtual_key_name",
]


def _check_permission() -> None:
	frappe.only_for(ALLOWED_ROLES)


def _default_range(start_date: str | None, end_date: str | None, days: int = 30) -> tuple[str, str]:
	end = get_datetime(end_date) if end_date else get_datetime(nowdate() + " 23:59:59")
	start = get_datetime(start_date) if start_date else add_to_date(end, days=-days)
	return start, end


def _apply_filters(
	conditions: str,
	values: dict,
	source: str | None,
	provider: str | None,
	model: str | None,
	virtual_key_name: str | None,
) -> str:
	"""Shared exact-match filter clause for the raw-SQL aggregates below —
	kept as a single function so the AI Usage tab's filter bar (source,
	provider, model, virtual key) behaves identically across every chart."""
	if source:
		conditions += " and source = %(source)s"
		values["source"] = source
	if provider:
		conditions += " and provider = %(provider)s"
		values["provider"] = provider
	if model:
		conditions += " and model = %(model)s"
		values["model"] = model
	if virtual_key_name:
		conditions += " and virtual_key_name = %(virtual_key_name)s"
		values["virtual_key_name"] = virtual_key_name
	return conditions


@frappe.whitelist()
def get_usage_summary(
	start_date: str | None = None,
	end_date: str | None = None,
	source: str | None = None,
	provider: str | None = None,
	model: str | None = None,
	virtual_key_name: str | None = None,
) -> dict:
	"""Total requests/tokens/cost, average latency, and success rate for a
	date range — the AI Usage tab's summary tiles."""
	_check_permission()
	start, end = _default_range(start_date, end_date)

	conditions = "request_timestamp between %(start)s and %(end)s"
	values = {"start": start, "end": end}
	conditions = _apply_filters(conditions, values, source, provider, model, virtual_key_name)

	row = frappe.db.sql(
		f"""
		select
			count(*) as total_requests,
			coalesce(sum(total_tokens), 0) as total_tokens,
			coalesce(sum(total_cost), 0) as total_cost,
			coalesce(avg(latency_ms), 0) as average_latency,
			coalesce(sum(case when status = 'success' then 1 else 0 end) / nullif(count(*), 0), 0) as success_rate
		from `tabLLM Usage Log`
		where {conditions}
		""",
		values,
		as_dict=True,
	)[0]
	return {
		"total_requests": cint(row.total_requests),
		"total_tokens": cint(row.total_tokens),
		"total_cost": flt(row.total_cost, 6),
		"average_latency": flt(row.average_latency, 1),
		"success_rate": flt(row.success_rate, 4),
	}


@frappe.whitelist()
def get_usage_trend(
	start_date: str | None = None,
	end_date: str | None = None,
	group_by: str = "day",
	source: str | None = None,
	provider: str | None = None,
	model: str | None = None,
	virtual_key_name: str | None = None,
) -> list[dict]:
	"""Daily (or hourly) cost/token/request series for the trend chart,
	computed from our own table — not Bifrost's histogram endpoints —
	since only our DB can combine multiple sources in one series once a
	second source exists."""
	_check_permission()
	start, end = _default_range(start_date, end_date)
	bucket_expr = "date_format(request_timestamp, '%%Y-%%m-%%d %%H:00:00')" if group_by == "hour" else "date(request_timestamp)"

	conditions = "request_timestamp between %(start)s and %(end)s"
	values = {"start": start, "end": end}
	conditions = _apply_filters(conditions, values, source, provider, model, virtual_key_name)

	rows = frappe.db.sql(
		f"""
		select
			{bucket_expr} as bucket,
			count(*) as total_requests,
			coalesce(sum(total_tokens), 0) as total_tokens,
			coalesce(sum(total_cost), 0) as total_cost
		from `tabLLM Usage Log`
		where {conditions}
		group by bucket
		order by bucket asc
		""",
		values,
		as_dict=True,
	)
	return [
		{
			"bucket": str(r.bucket),
			"total_requests": cint(r.total_requests),
			"total_tokens": cint(r.total_tokens),
			"total_cost": flt(r.total_cost, 6),
		}
		for r in rows
	]


@frappe.whitelist()
def get_breakdown(
	dimension: str = "provider",
	start_date: str | None = None,
	end_date: str | None = None,
	source: str | None = None,
	provider: str | None = None,
	model: str | None = None,
	virtual_key_name: str | None = None,
	limit: int = 15,
) -> list[dict]:
	"""Cost/token/request share by provider, model, or virtual key, highest cost first."""
	_check_permission()
	column = ALLOWED_DIMENSIONS.get(dimension)
	if not column:
		frappe.throw(frappe._("Invalid dimension: {0}").format(dimension))
	start, end = _default_range(start_date, end_date)

	conditions = "request_timestamp between %(start)s and %(end)s"
	values = {"start": start, "end": end, "limit": cint(limit) or 15}
	conditions = _apply_filters(conditions, values, source, provider, model, virtual_key_name)

	rows = frappe.db.sql(
		f"""
		select
			{column} as label,
			count(*) as total_requests,
			coalesce(sum(total_tokens), 0) as total_tokens,
			coalesce(sum(total_cost), 0) as total_cost
		from `tabLLM Usage Log`
		where {conditions}
		group by {column}
		order by total_cost desc
		limit %(limit)s
		""",
		values,
		as_dict=True,
	)
	fallback_label = DIMENSION_FALLBACK_LABEL.get(dimension, frappe._("Unknown"))
	return [
		{
			"label": r.label or fallback_label,
			"total_requests": cint(r.total_requests),
			"total_tokens": cint(r.total_tokens),
			"total_cost": flt(r.total_cost, 6),
		}
		for r in rows
	]


@frappe.whitelist()
def get_recent_requests(
	start_date: str | None = None,
	end_date: str | None = None,
	source: str | None = None,
	provider: str | None = None,
	model: str | None = None,
	virtual_key_name: str | None = None,
	limit: int = 50,
	offset: int = 0,
	sort_by: str = "request_timestamp",
	order: str = "desc",
) -> dict:
	"""Paginated, filterable request table — uses ``frappe.get_list`` (not
	raw SQL) so Frappe's own permission layer applies automatically."""
	sort_by = sort_by if sort_by in ALLOWED_SORT_FIELDS else "request_timestamp"
	order = "asc" if str(order).lower() == "asc" else "desc"
	start, end = _default_range(start_date, end_date)

	filters: dict = {"request_timestamp": ["between", [start, end]]}
	if source:
		filters["source"] = source
	if provider:
		filters["provider"] = provider
	if model:
		filters["model"] = model
	if virtual_key_name:
		filters["virtual_key_name"] = virtual_key_name

	rows = frappe.get_list(
		"LLM Usage Log",
		filters=filters,
		fields=RECENT_REQUEST_FIELDS,
		order_by=f"{sort_by} {order}",
		limit_page_length=cint(limit) or 50,
		limit_start=cint(offset),
	)
	total_count = frappe.db.count("LLM Usage Log", filters=filters)
	return {"rows": rows, "total_count": total_count}


@frappe.whitelist()
def get_filter_options() -> dict:
	"""Distinct provider/model/virtual-key values across all synced data,
	for the AI Usage tab's filter dropdowns."""
	_check_permission()

	def distinct(column: str) -> list[str]:
		rows = frappe.db.sql(
			f"""
			select distinct {column} from `tabLLM Usage Log`
			where {column} is not null and {column} != ''
			order by {column}
			""",
		)
		return [r[0] for r in rows]

	return {
		"providers": distinct("provider"),
		"models": distinct("model"),
		"virtual_keys": distinct("virtual_key_name"),
	}
