# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Whitelisted read endpoints for the OpenRouter dashboard tab.

Same shape and rules as ``llm_usage_monitor.api`` (Bifrost's): parameterized raw
SQL for the aggregates, and every function gates on the same three roles because
raw SQL bypasses Frappe's own permission checks. Nothing here reads or writes
Bifrost's tables, and nothing returns the management key.

Daily usage is keyed by OpenRouter's **UTC** day, so ``start_date`` / ``end_date``
are treated as plain dates (the time part the frontend sends is ignored) and are
compared with the stored UTC dates. Balance snapshots are site-timezone (IST)
datetimes, like every other Pulse timestamp.
"""

from __future__ import annotations

import frappe
from frappe.utils import add_to_date, flt, get_datetime, getdate, nowdate

ALLOWED_ROLES = ("System Manager", "Proxmox Monitor Manager", "Proxmox Monitor Viewer")
DIMENSIONS = {"model": "model", "provider": "provider_name"}
#: Over this many days the balance series is averaged per hour instead of per snapshot.
BALANCE_RAW_DAYS = 2
DEFAULT_DAYS = 30


def _check_permission() -> None:
	frappe.only_for(ALLOWED_ROLES)


def _range(start_date: str | None, end_date: str | None, days: int = DEFAULT_DAYS):
	end = getdate(end_date) if end_date else getdate(nowdate())
	start = getdate(start_date) if start_date else add_to_date(end, days=-days)
	return getdate(start), getdate(end)


def _parse_list(value) -> list[str] | None:
	"""Filters arrive as a JSON array in the query string (the frontend's
	MultiSelectFilter sends ``JSON.stringify``); a real list works too."""
	if not value:
		return None
	if isinstance(value, str):
		try:
			value = frappe.parse_json(value)
		except Exception:
			value = [value]
	return [str(v) for v in value] if value else None


def _where(start, end, provider, model) -> tuple[str, dict]:
	where, values = ["`date` between %(start)s and %(end)s"], {"start": start, "end": end}
	providers, models = _parse_list(provider), _parse_list(model)
	if providers:
		where.append("provider_name in %(providers)s")
		values["providers"] = providers
	if models:
		where.append("model in %(models)s")
		values["models"] = models
	return " and ".join(where), values


def _latest_credits() -> dict | None:
	row = frappe.db.sql(
		"select taken_at, total_purchased, total_used, remaining from `tabOpenRouter Balance Snapshot` order by taken_at desc limit 1",
		as_dict=True,
	)
	if not row:
		return None
	r = row[0]
	return {
		"taken_at": r.taken_at,
		"total_purchased": flt(r.total_purchased, 6),
		"total_used": flt(r.total_used, 6),
		"remaining": flt(r.remaining, 6),
	}


@frappe.whitelist()
def get_summary(start_date=None, end_date=None, provider=None, model=None) -> dict:
	"""Totals for the range, plus the latest credit balance."""
	_check_permission()
	start, end = _range(start_date, end_date)
	where, values = _where(start, end, provider, model)
	row = frappe.db.sql(
		f"""select coalesce(sum(usage_usd), 0) as spend, coalesce(sum(byok_usage_usd), 0) as byok,
			coalesce(sum(requests), 0) as requests, coalesce(sum(prompt_tokens), 0) as prompt_tokens,
			coalesce(sum(completion_tokens), 0) as completion_tokens, coalesce(sum(reasoning_tokens), 0) as reasoning_tokens
		from `tabOpenRouter Daily Usage` where {where}""",
		values,
		as_dict=True,
	)[0]
	latest_day = frappe.db.sql("select max(`date`) from `tabOpenRouter Daily Usage`")[0][0]
	return {
		"total_cost": flt(row.spend, 6),
		"byok_cost": flt(row.byok, 6),
		"total_requests": int(row.requests),
		"prompt_tokens": int(row.prompt_tokens),
		"completion_tokens": int(row.completion_tokens),
		"reasoning_tokens": int(row.reasoning_tokens),
		# prompt + completion; reasoning is shown separately so it is not counted twice
		"total_tokens": int(row.prompt_tokens) + int(row.completion_tokens),
		"latest_day": str(latest_day) if latest_day else None,
		"credits": _latest_credits(),
	}


@frappe.whitelist()
def get_trend(start_date=None, end_date=None, provider=None, model=None) -> list[dict]:
	"""One point per UTC day: spend, requests and tokens."""
	_check_permission()
	start, end = _range(start_date, end_date)
	where, values = _where(start, end, provider, model)
	rows = frappe.db.sql(
		f"""select `date` as bucket, sum(usage_usd) as total_cost, sum(requests) as total_requests,
			sum(prompt_tokens) as prompt_tokens, sum(completion_tokens) as completion_tokens, sum(reasoning_tokens) as reasoning_tokens
		from `tabOpenRouter Daily Usage` where {where} group by `date` order by `date` asc""",
		values,
		as_dict=True,
	)
	return [
		{
			"bucket": str(r.bucket),
			"total_cost": flt(r.total_cost, 6),
			"total_requests": int(r.total_requests or 0),
			"prompt_tokens": int(r.prompt_tokens or 0),
			"completion_tokens": int(r.completion_tokens or 0),
			"reasoning_tokens": int(r.reasoning_tokens or 0),
			"total_tokens": int(r.prompt_tokens or 0) + int(r.completion_tokens or 0),
		}
		for r in rows
	]


@frappe.whitelist()
def get_breakdown(dimension="model", start_date=None, end_date=None, limit=15, provider=None, model=None) -> list[dict]:
	"""Spend, requests and tokens grouped by model or provider, biggest spend first."""
	_check_permission()
	if dimension not in DIMENSIONS:
		frappe.throw(f"Unknown dimension '{dimension}'")
	column = DIMENSIONS[dimension]
	start, end = _range(start_date, end_date)
	where, values = _where(start, end, provider, model)
	values["limit"] = max(1, min(int(limit or 15), 100))
	rows = frappe.db.sql(
		f"""select coalesce(nullif({column}, ''), '(unknown)') as label, sum(usage_usd) as total_cost,
			sum(requests) as total_requests, sum(prompt_tokens + completion_tokens) as total_tokens
		from `tabOpenRouter Daily Usage` where {where} group by label order by total_cost desc, total_requests desc limit %(limit)s""",
		values,
		as_dict=True,
	)
	return [
		{"label": r.label, "total_cost": flt(r.total_cost, 6), "total_requests": int(r.total_requests or 0), "total_tokens": int(r.total_tokens or 0)}
		for r in rows
	]


@frappe.whitelist()
def get_model_table(start_date=None, end_date=None, provider=None, model=None) -> list[dict]:
	"""Every model in the range with its totals (the per-model table)."""
	_check_permission()
	start, end = _range(start_date, end_date)
	where, values = _where(start, end, provider, model)
	rows = frappe.db.sql(
		f"""select coalesce(nullif(model, ''), '(unknown)') as model, group_concat(distinct nullif(provider_name, '') separator ', ') as providers,
			sum(usage_usd) as total_cost, sum(requests) as total_requests, sum(prompt_tokens) as prompt_tokens,
			sum(completion_tokens) as completion_tokens, sum(reasoning_tokens) as reasoning_tokens
		from `tabOpenRouter Daily Usage` where {where} group by 1 order by total_cost desc, total_requests desc limit 500""",
		values,
		as_dict=True,
	)
	return [
		{
			"model": r.model,
			"providers": r.providers or "",
			"total_cost": flt(r.total_cost, 6),
			"total_requests": int(r.total_requests or 0),
			"prompt_tokens": int(r.prompt_tokens or 0),
			"completion_tokens": int(r.completion_tokens or 0),
			"reasoning_tokens": int(r.reasoning_tokens or 0),
		}
		for r in rows
	]


@frappe.whitelist()
def get_balance_history(start_date=None, end_date=None) -> list[dict]:
	"""Remaining credit over time: every snapshot for a short range, an hourly
	average for a long one (30-minute snapshots over 30 days is 1,440 points)."""
	_check_permission()
	start, end = _range(start_date, end_date)
	first, last = get_datetime(f"{start} 00:00:00"), get_datetime(f"{end} 23:59:59")
	values = {"first": first, "last": last}
	if (end - start).days > BALANCE_RAW_DAYS:
		rows = frappe.db.sql(
			"""select date_format(taken_at, '%%Y-%%m-%%d %%H:00:00') as taken_at, round(avg(remaining), 6) as remaining,
				round(avg(total_purchased), 6) as total_purchased, round(avg(total_used), 6) as total_used
			from `tabOpenRouter Balance Snapshot` where taken_at between %(first)s and %(last)s group by 1 order by 1 asc""",
			values,
			as_dict=True,
		)
	else:
		rows = frappe.db.sql(
			"""select taken_at, remaining, total_purchased, total_used from `tabOpenRouter Balance Snapshot`
			where taken_at between %(first)s and %(last)s order by taken_at asc""",
			values,
			as_dict=True,
		)
	return [
		{"taken_at": str(r.taken_at), "remaining": flt(r.remaining, 6), "total_purchased": flt(r.total_purchased, 6), "total_used": flt(r.total_used, 6)}
		for r in rows
	]


@frappe.whitelist()
def get_filter_options() -> dict:
	_check_permission()
	providers = frappe.db.sql("select distinct provider_name from `tabOpenRouter Daily Usage` where provider_name is not null and provider_name != '' order by 1", pluck=True)
	models = frappe.db.sql("select distinct model from `tabOpenRouter Daily Usage` where model is not null and model != '' order by 1", pluck=True)
	return {"providers": providers, "models": models}
