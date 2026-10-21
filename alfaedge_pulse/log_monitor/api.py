# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Whitelisted endpoints for the dashboard's Logs tab.

Logs are queried live from ClickHouse on every call (see ``queries.py``) —
nothing is copied into MariaDB except ``Recurring Error`` alert state.
These endpoints don't go through Frappe's doctype permissions at all, so
each one gates explicitly on the same roles as the rest of the dashboard.

Every endpoint converts ClickHouse/config failures into a plain
``frappe.throw`` with the underlying message, so the dashboard shows
"ClickHouse unreachable: ..." rather than a generic 500.
"""

from __future__ import annotations

from contextlib import contextmanager

import frappe
from frappe.utils import add_to_date, cint, get_datetime, now_datetime

from alfaedge_pulse.clickhouse_client.base import ClickHouseAPIError
from alfaedge_pulse.log_monitor.connection import (
	SECRET_FIELDS,
	SETTINGS_DOCTYPE,
	LogMonitorNotConfigured,
	get_client,
	get_column_map,
	get_secret,
	get_settings,
	get_site_aliases,
	normalize_site,
)
from alfaedge_pulse.log_monitor.queries import base_params, build_filtered, site_host_expr, ts_out

VIEW_ROLES = ("System Manager", "Proxmox Monitor Manager", "Proxmox Monitor Viewer")
#: Connection settings and site aliases are managed by this role alone —
#: every other dashboard role can only view and filter the logs.
MANAGER_ROLES = ("Proxmox Monitor Manager",)
MAX_PAGE_LENGTH = 200
PREVIEW_CHARS = 400
#: ~this many points per trend chart, whatever the selected range.
TARGET_BUCKETS = 120
_NICE_BUCKETS = (60, 300, 900, 1800, 3600, 3 * 3600, 6 * 3600, 12 * 3600, 86400, 7 * 86400)

#: Non-secret settings fields the dashboard dialog can read and write.
EDITABLE_FIELDS = (
	"enabled", "protocol", "host", "port", "url_path", "verify_ssl", "query_timeout_seconds",
	"api_key_header", "api_key_prefix", "database_name", "table_name",
	"ts_column", "site_column", "content_column", "title_column", "id_column", "log_type_column",
	"error_keyword", "enable_recurring_alerts", "occurrence_threshold", "window_minutes",
	"check_interval_minutes", "notify_global_recipients",
)


@contextmanager
def _clickhouse_errors():
	try:
		yield
	except LogMonitorNotConfigured as e:
		frappe.throw(str(e), title=frappe._("Log Monitor not configured"))
	except ClickHouseAPIError as e:
		frappe.throw(str(e), title=frappe._("ClickHouse query failed"))


def _parse_list(value) -> list[str] | None:
	if not value:
		return None
	if isinstance(value, (list, tuple)):
		return list(value) or None
	parsed = frappe.parse_json(value)
	if isinstance(parsed, str):
		return [parsed]
	return parsed or None


def _range(start_date: str | None, end_date: str | None, default_days: int = 1) -> tuple[str, str]:
	end = get_datetime(end_date) if end_date else now_datetime()
	start = get_datetime(start_date) if start_date else add_to_date(end, days=-default_days)
	# Exclusive upper bound — the frontend sends "... 23:59:59" for end-of-day.
	end = add_to_date(end, seconds=1)
	return start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")


def _filters(start_date, end_date, site_url, log_type, signature=None, search=None):
	start, end = _range(start_date, end_date)
	return build_filtered(
		start, end,
		sites=_parse_list(site_url),
		log_types=_parse_list(log_type),
		signature=signature or None,
		search=(search or "").strip() or None,
	)


def _bucket_seconds(start: str, end: str) -> int:
	span = (get_datetime(end) - get_datetime(start)).total_seconds()
	target = span / TARGET_BUCKETS
	return next((b for b in _NICE_BUCKETS if b >= target), _NICE_BUCKETS[-1])


@frappe.whitelist()
def get_log_filter_options(days: int = 30) -> dict:
	"""Site URLs seen in the last ``days`` days, busiest first."""
	frappe.only_for(VIEW_ROLES)
	start, end = _range(None, None, default_days=cint(days) or 30)
	from_sql, params = build_filtered(start, end)
	with _clickhouse_errors():
		rows = get_client().query(
			f"SELECT lm_site AS site_url, count() AS count {from_sql} GROUP BY lm_site ORDER BY count DESC LIMIT 1000",
			params,
		)
	return {"sites": [{"site_url": r["site_url"], "count": cint(r["count"])} for r in rows if r["site_url"]]}


@frappe.whitelist()
def get_log_summary(start_date=None, end_date=None, site_url=None, log_type=None, search=None) -> dict:
	"""Totals for the summary tiles plus a bucketed Error/General trend."""
	frappe.only_for(VIEW_ROLES)
	start, end = _range(start_date, end_date)
	from_sql, params = _filters(start_date, end_date, site_url, log_type, search=search)
	bucket = _bucket_seconds(start, end)
	params["bucket"] = bucket
	with _clickhouse_errors():
		client = get_client()
		totals = client.query(
			f"""SELECT count() AS total, countIf(lm_type = 'Error') AS errors,
				countIf(lm_type = 'General') AS general, uniqExact(lm_site) AS sites,
				uniqExactIf(lm_sig, lm_type = 'Error') AS distinct_errors {from_sql}""",
			params,
		)[0]
		trend = client.query(
			f"""SELECT {ts_out('b')} AS bucket, countIf(lm_type = 'Error') AS errors,
				countIf(lm_type = 'General') AS general
				FROM (SELECT toStartOfInterval(lm_ts, toIntervalSecond({{bucket:UInt32}}), {{tz:String}}) AS b, lm_type {from_sql})
				GROUP BY b ORDER BY b""",
			params,
		)
	return {
		"total": cint(totals["total"]),
		"errors": cint(totals["errors"]),
		"general": cint(totals["general"]),
		"sites": cint(totals["sites"]),
		"distinct_errors": cint(totals["distinct_errors"]),
		"bucket_seconds": bucket,
		"trend": [{"bucket": r["bucket"], "errors": cint(r["errors"]), "general": cint(r["general"])} for r in trend],
	}


@frappe.whitelist()
def get_logs(
	start_date=None, end_date=None, site_url=None, log_type=None, signature=None, search=None,
	page: int = 1, page_length: int = 50, sort_order: str = "desc",
) -> dict:
	"""One page of log rows (content truncated — see ``get_log_detail``)."""
	frappe.only_for(VIEW_ROLES)
	page = max(cint(page), 1)
	page_length = min(max(cint(page_length), 1), MAX_PAGE_LENGTH)
	direction = "ASC" if str(sort_order).lower() == "asc" else "DESC"
	from_sql, params = _filters(start_date, end_date, site_url, log_type, signature, search)
	params.update({"limit": page_length, "offset": (page - 1) * page_length, "preview": PREVIEW_CHARS})
	with _clickhouse_errors():
		client = get_client()
		total = cint(client.query(f"SELECT count() AS c {from_sql}", params)[0]["c"])
		rows = client.query(
			f"""SELECT lm_id AS log_id, {ts_out()} AS ts, lm_site AS site_url, lm_type AS log_type,
				lm_title AS title, lm_sig AS signature, substring(lm_content, 1, {{preview:UInt32}}) AS preview,
				length(lm_content) > {{preview:UInt32}} AS truncated
				{from_sql} ORDER BY lm_ts {direction} LIMIT {{limit:UInt32}} OFFSET {{offset:UInt32}}""",
			params,
		)
	for r in rows:
		r["truncated"] = bool(cint(r["truncated"]))
	return {"total": total, "page": page, "page_length": page_length, "rows": rows}


@frappe.whitelist()
def get_log_detail(log_id: str, ts: str) -> dict:
	"""Full content of one row. ``ts`` narrows the scan to that second so
	the lookup stays cheap even when the id is a computed hash."""
	frappe.only_for(VIEW_ROLES)
	moment = get_datetime(ts)
	from_sql, params = build_filtered(
		add_to_date(moment, seconds=-1).strftime("%Y-%m-%d %H:%M:%S"),
		add_to_date(moment, seconds=2).strftime("%Y-%m-%d %H:%M:%S"),
		log_id=str(log_id),
	)
	with _clickhouse_errors():
		rows = get_client().query(
			f"""SELECT lm_id AS log_id, {ts_out()} AS ts, lm_site AS site_url, lm_type AS log_type,
				lm_title AS title, lm_sig AS signature, lm_content AS content
				{from_sql} LIMIT 1""",
			params,
		)
	if not rows:
		frappe.throw(frappe._("Log entry not found"), frappe.DoesNotExistError)
	return rows[0]


@frappe.whitelist()
def get_recurring_errors(
	start_date=None, end_date=None, site_url=None, search=None, min_count: int = 2, limit: int = 200,
	hide_resolved: int = 1,
) -> list[dict]:
	"""Error rows grouped by signature, most frequent first, annotated with
	this app's own alert state for that signature.

	With ``hide_resolved`` (the default), occurrences at or before an
	error's resolution don't count — a resolved error is absent until it
	happens again, and then comes back flagged ``reopened`` with only the
	new occurrences counted."""
	frappe.only_for(VIEW_ROLES)
	start, end = _range(start_date, end_date)
	from_sql, params = build_filtered(
		start, end,
		sites=_parse_list(site_url),
		log_types=["Error"],
		search=(search or "").strip() or None,
		exclude_resolved=bool(cint(hide_resolved)),
	)
	params.update({"min_count": max(cint(min_count), 1), "limit": min(max(cint(limit), 1), 1000)})
	with _clickhouse_errors():
		rows = get_client().query(
			f"""SELECT lm_sig AS signature, any(lm_site) AS site_url, any(lm_title) AS title,
				count() AS occurrences, {ts_out('min(lm_ts)')} AS first_seen, {ts_out('max(lm_ts)')} AS last_seen,
				argMax(lm_id, lm_ts) AS latest_log_id, {ts_out('max(lm_ts)')} AS latest_ts
				{from_sql} GROUP BY lm_sig HAVING occurrences >= {{min_count:UInt32}}
				ORDER BY occurrences DESC, max(lm_ts) DESC LIMIT {{limit:UInt32}}""",
			params,
		)
	state = {}
	if rows:
		state = {
			r.signature: r
			for r in frappe.get_all(
				"Recurring Error",
				filters={"signature": ["in", [r["signature"] for r in rows]]},
				fields=["name", "signature", "is_critical", "alert_count", "monitored_host", "resolved_at", "resolved_by"],
			)
		}
	for r in rows:
		r["occurrences"] = cint(r["occurrences"])
		local = state.get(r["signature"])
		r["is_critical"] = bool(local and local.is_critical)
		r["recurring_error"] = local.name if local else None
		r["alert_count"] = cint(local.alert_count) if local else 0
		r["monitored_host"] = local.monitored_host if local else None
		r["resolved_at"] = str(local.resolved_at) if local and local.resolved_at else None
		r["resolved_by"] = local.resolved_by if local else None
		# Shown despite a resolution → it has happened again since.
		r["reopened"] = bool(r["resolved_at"]) and bool(cint(hide_resolved))
	return rows


@frappe.whitelist(methods=["POST"])
def resolve_error(signature: str) -> dict:
	"""Mark one recurring error (one signature = one error on one site) as
	resolved as of now. Its existing occurrences stop counting everywhere
	recurring errors are shown or alerted on; it reappears on its own if it
	happens again. Also closes any open Recurring Error alert for it, with a
	"back to normal" to its subscribers."""
	frappe.only_for(VIEW_ROLES)
	from alfaedge_pulse.alerts.dispatch import dispatch_recovery, has_open_alert, resolve_alert

	signature = (signature or "").strip()
	if not signature.isdigit():
		frappe.throw(frappe._("Invalid error signature"))

	# Look the error up in ClickHouse rather than trusting display fields
	# from the client — the signature must be a real one.
	start, end = _range(None, None, default_days=30)
	from_sql, params = build_filtered(start, end, log_types=["Error"], signature=signature)
	with _clickhouse_errors():
		found = get_client().query(
			f"""SELECT any(lm_site) AS site_url, argMax(lm_title, lm_ts) AS title,
				{ts_out('min(lm_ts)')} AS first_seen, {ts_out('max(lm_ts)')} AS last_seen, count() AS c
				{from_sql}""",
			params,
		)
	if not found or not cint(found[0]["c"]):
		frappe.throw(frappe._("No error with this signature in the last 30 days"), frappe.DoesNotExistError)
	info = found[0]

	now = now_datetime()
	name = frappe.db.get_value("Recurring Error", {"signature": signature})
	doc = frappe.get_doc("Recurring Error", name) if name else frappe.new_doc("Recurring Error")
	doc.update({
		"signature": signature,
		"title": doc.title or info["title"] or "(no message)",
		"site_url": doc.site_url or info["site_url"],
		"first_seen": doc.first_seen or info["first_seen"],
		"last_seen": info["last_seen"],
		"is_critical": 0,
		"window_count": 0,
		"resolved_at": now,
		"resolved_by": frappe.session.user,
	})
	doc.save(ignore_permissions=True)

	if has_open_alert("Recurring Error", doc.name, "Recurring Error"):
		resolve_alert("Recurring Error", doc.name, "Recurring Error")
		who = frappe.utils.get_fullname(frappe.session.user)
		dispatch_recovery(
			"Recurring Error", "Recurring Error", doc.name,
			f"Marked resolved by {who}: {doc.title} on {doc.site_url}",
		)
	return {"signature": signature, "resolved_at": str(now), "resolved_by": frappe.session.user}


# ---------------------------------------------------------------------------
# Site aliases (Log Site Alias) — merging hosts that are really one site
# ---------------------------------------------------------------------------


@frappe.whitelist()
def get_site_overview(days: int = 30) -> dict:
	"""Every host seen in the last ``days`` days (before aliasing), with the
	raw URLs it appeared under, its log count, and what it's shown as —
	plus suggestions for alias targets (other hosts, Monitored Host sites,
	existing alias names)."""
	frappe.only_for(MANAGER_ROLES)
	cols = get_column_map()
	start, end = _range(None, None, default_days=cint(days) or 30)
	params = base_params(start, end)
	host = site_host_expr(cols)
	with _clickhouse_errors():
		rows = get_client().query(
			f"""SELECT {host} AS host, groupUniqArray(10)(toString({cols.site})) AS urls, count() AS logs,
				{ts_out(f"max({cols.ts})")} AS last_seen
				FROM {cols.table}
				WHERE {cols.ts} >= toDateTime({{start:String}}, {{tz:String}}) AND {cols.ts} < toDateTime({{end:String}}, {{tz:String}})
				GROUP BY host ORDER BY logs DESC LIMIT 2000""",
			params,
		)
	aliases = get_site_aliases()
	hosts = []
	for r in rows:
		if not r["host"]:
			continue
		hosts.append({
			"host": r["host"],
			"urls": sorted(r["urls"]),
			"logs": cint(r["logs"]),
			"last_seen": r["last_seen"],
			"alias": aliases.get(r["host"]),
		})
	monitored = {
		normalize_site(v)
		for row in frappe.get_all("Monitored Host Site", fields=["site_url", "site_name"])
		for v in (row.site_url, row.site_name)
		if v
	}
	suggestions = sorted({h["host"] for h in hosts} | set(aliases.values()) | monitored)
	return {"hosts": hosts, "suggestions": [s for s in suggestions if s]}


@frappe.whitelist(methods=["POST"])
def save_site_alias(source_host: str, site: str | None = None) -> dict:
	"""Show ``source_host`` as ``site``. A blank ``site`` (or one equal to
	the host) removes the alias."""
	frappe.only_for(MANAGER_ROLES)
	source_host = normalize_site(source_host)
	if not source_host:
		frappe.throw(frappe._("Host is required"))
	existing = frappe.db.get_value("Log Site Alias", {"source_host": source_host})
	site = (site or "").strip()
	if not site or normalize_site(site) == source_host:
		if existing:
			frappe.delete_doc("Log Site Alias", existing, ignore_permissions=True)
		return {"source_host": source_host, "site": None}
	doc = frappe.get_doc("Log Site Alias", existing) if existing else frappe.new_doc("Log Site Alias")
	doc.update({"source_host": source_host, "site": site})
	doc.save(ignore_permissions=True)
	return {"source_host": doc.source_host, "site": doc.site}


# ---------------------------------------------------------------------------
# Settings, editable from the dashboard (secrets are write-only)
# ---------------------------------------------------------------------------


@frappe.whitelist()
def get_log_settings() -> dict:
	"""What the Logs tab needs to know about the connection.

	Managers get every non-secret setting plus has_<secret> flags (secrets
	are never sent to anyone). Everyone else only learns whether Log
	Monitor is set up — not where ClickHouse lives or how it's configured."""
	frappe.only_for(VIEW_ROLES)
	settings = get_settings()
	can_edit = _is_manager()
	data = {
		"enabled": settings.enabled,
		"configured": bool(settings.enabled and (settings.host or "").strip()),
		"last_checked": settings.last_checked,
		"can_edit": can_edit,
	}
	if can_edit:
		data.update({field: settings.get(field) for field in EDITABLE_FIELDS})
		data.update({f"has_{field}": bool(get_secret(field)) for field in SECRET_FIELDS})
		data.update({"effective_url": settings.effective_url, "last_error": settings.last_error})
	return data


def _is_manager() -> bool:
	return bool(set(frappe.get_roles()) & set(MANAGER_ROLES))


def _clean_values(values) -> dict:
	values = frappe.parse_json(values) if isinstance(values, str) else (values or {})
	return {k: v for k, v in values.items() if k in EDITABLE_FIELDS or k in SECRET_FIELDS or k.startswith("clear_")}


@frappe.whitelist(methods=["POST"])
def save_log_settings(values) -> dict:
	"""Update settings. A blank secret keeps the stored value; pass
	``clear_<secret>: true`` to remove one."""
	frappe.only_for(MANAGER_ROLES)
	values = _clean_values(values)
	doc = frappe.get_doc(SETTINGS_DOCTYPE)
	for field in EDITABLE_FIELDS:
		if field in values:
			doc.set(field, values[field])
	for field in SECRET_FIELDS:
		if cint(values.get(f"clear_{field}")):
			doc.set(field, "")
		elif values.get(field):
			doc.set(field, values[field])
	doc.save()
	return get_log_settings()


@frappe.whitelist(methods=["POST"])
def test_log_connection(values=None) -> dict:
	"""Test saved settings, overlaid with any unsaved ``values``."""
	frappe.only_for(MANAGER_ROLES)
	from alfaedge_pulse.log_monitor.doctype.clickhouse_log_settings.clickhouse_log_settings import (
		run_connection_test,
	)

	return run_connection_test(_clean_values(values) if values else None)
