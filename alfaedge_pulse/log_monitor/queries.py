# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""SQL building blocks shared by the Logs dashboard API and the recurring-
error alert task, so both classify and group rows identically.

Every query reads through ``base_query()``: one inner SELECT that maps the
configured columns (see ``connection.ColumnMap``) onto a fixed set of
aliases — ``lm_ts``, ``lm_site``, ``lm_content``, ``lm_title``,
``lm_type``, ``lm_sig``, ``lm_id`` (prefixed so an alias can never
collide with a real column name, which ClickHouse rejects as a cyclic
alias) — so nothing downstream depends on how the
server script happened to name its columns. The timestamp range and site
filter sit on the inner SELECT (where ClickHouse can use the table's sort
key); derived-column filters (site, type, signature, search) sit outside
it.

- **Site**: the URL column's bare lower-cased host, so http/https/port/
  trailing-slash variants of one site are one site; then any Log Site
  Alias (e.g. ``lulu`` -> ``lulu.alfaedge.org``) is applied on top via
  ``transform()``, so aliases regroup history too, not just new rows.

- **Log type**: "Error" when the content (or the mapped Log Type column,
  if one is configured) contains the configured keyword, case-
  insensitively; "General" otherwise.
- **Title**: the mapped Title column if set; otherwise, for a row holding
  a Python traceback, its last non-empty line (the actual
  ``SomeError: message`` line), prefixed with the first line when that
  first line is a context message rather than "Traceback ..." itself;
  for anything else, its first non-empty line.
- **Signature** (what "the same error" means for recurring-error
  grouping): a hash of the site URL plus the title with every volatile
  token — numbers, long hex runs (hashes, docnames), and quoted values —
  replaced by ``?``, so "Sales Invoice SINV-00123 not found" and
  "... SINV-00456 not found" on the same site count as one error.
- **Resolved errors**: with ``exclude_resolved``, occurrences of a
  signature at or before its Recurring Error's ``resolved_at`` are
  dropped. A resolved error therefore vanishes from recurring-error views
  and alert counts until it happens again.
- **Timestamps** go in as ``String`` params converted with an explicit
  timezone (the site's system timezone), and come back formatted in that
  same timezone, so the dashboard's "naive string = site-local" convention
  holds no matter what timezone the ClickHouse server itself runs in.
"""

from __future__ import annotations

import frappe
from frappe.utils import get_system_timezone

from alfaedge_pulse.log_monitor.connection import ColumnMap, get_column_map, get_settings, get_site_aliases

LOG_TYPES = ("Error", "General")

# Volatile tokens stripped before hashing a signature. Written for a
# ClickHouse string literal: backslashes doubled, single quotes escaped.
_NORMALIZE_RE = r"(?i)[0-9a-f]{8,}|[0-9]+|\'[^\']*\'|\"[^\"]*\""


def _lines(expr: str) -> str:
	return f"arrayFilter(x -> notEmpty(trimBoth(x)), splitByChar('\\n', toString({expr})))"


def title_expr(cols: ColumnMap) -> str:
	if cols.title:
		return f"substring(trimBoth(toString({cols.title})), 1, 300)"
	lines = _lines(cols.content)
	first = f"trimBoth(arrayElement({lines}, 1))"
	last = f"trimBoth(arrayElement({lines}, -1))"
	# A traceback's useful line is its last one (the exception). Frappe's
	# log_error() often prefixes it with a context line ("Error @ fetch_order:")
	# — kept, since it's what tells two PermissionErrors apart.
	has_traceback = f"positionCaseInsensitive(toString({cols.content}), 'Traceback') > 0"
	starts_with_traceback = f"startsWith({first}, 'Traceback')"
	return (
		f"substring(if({has_traceback}, "
		f"if({starts_with_traceback}, {last}, concat(trimRight({first}, ':'), ': ', {last})), "
		f"{first}), 1, 300)"
	)


def site_host_expr(cols: ColumnMap) -> str:
	"""Bare lower-cased host of the site URL column — must match
	``connection.normalize_site``. (ClickHouse's own ``domain()`` returns
	'' for single-label hosts like ``https://lulu``, hence the regex.)"""
	return (
		f"lower(replaceRegexpOne(toString({cols.site}), "
		f"'^(?:[a-zA-Z][a-zA-Z0-9+.-]*://)?([^/:?#]+).*$', '\\\\1'))"
	)


def log_type_expr(cols: ColumnMap) -> str:
	source = cols.log_type or cols.content
	return f"if(positionCaseInsensitive(toString({source}), {{kw:String}}) > 0, 'Error', 'General')"


def base_query(cols: ColumnMap) -> str:
	"""Inner SELECT over [start, end). Needs the params from ``base_params``."""
	title = title_expr(cols)
	log_id = f"toString({cols.id})" if cols.id else f"toString(cityHash64({cols.ts}, {cols.site}, {cols.content}))"
	host = site_host_expr(cols)
	return f"""
		SELECT
			{cols.ts} AS lm_ts,
			transform({host}, {{site_from:Array(String)}}, {{site_to:Array(String)}}, {host}) AS lm_site,
			toString({cols.content}) AS lm_content,
			{title} AS lm_title,
			{log_type_expr(cols)} AS lm_type,
			toString(cityHash64(lm_site, replaceRegexpAll(lm_title, '{_NORMALIZE_RE}', '?'))) AS lm_sig,
			{log_id} AS lm_id
		FROM {cols.table}
		WHERE {cols.ts} >= toDateTime({{start:String}}, {{tz:String}})
			AND {cols.ts} < toDateTime({{end:String}}, {{tz:String}})
	"""


def base_params(start: str, end: str) -> dict:
	settings = get_settings()
	aliases = get_site_aliases()
	return {
		"start": str(start),
		"end": str(end),
		"tz": get_system_timezone(),
		"kw": (settings.error_keyword or "error").strip() or "error",
		"site_from": list(aliases.keys()),
		"site_to": list(aliases.values()),
	}


def ts_out(alias: str = "lm_ts") -> str:
	"""Format a timestamp for the dashboard in the site's timezone."""
	return f"formatDateTime(toTimeZone({alias}, {{tz:String}}), '%Y-%m-%d %H:%i:%S')"


def get_resolutions() -> dict[str, str]:
	"""{signature: resolved_at} for every Recurring Error marked resolved,
	as site-timezone "YYYY-MM-DD HH:MM:SS" strings."""
	return {
		r.signature: r.resolved_at.strftime("%Y-%m-%d %H:%M:%S")
		for r in frappe.get_all(
			"Recurring Error", filters={"resolved_at": ["is", "set"]}, fields=["signature", "resolved_at"]
		)
	}


def build_filtered(
	start: str,
	end: str,
	sites: list[str] | None = None,
	log_types: list[str] | None = None,
	signature: str | None = None,
	search: str | None = None,
	log_id: str | None = None,
	exclude_resolved: bool = False,
	cols: ColumnMap | None = None,
) -> tuple[str, dict]:
	"""``FROM (...) WHERE ...`` fragment + params with all dashboard filters
	applied — callers prepend their own SELECT and append GROUP/ORDER."""
	cols = cols or get_column_map()
	params = base_params(start, end)
	outer = []
	if sites:
		outer.append("lm_site IN {sites:Array(String)}")
		params["sites"] = list(sites)
	types = [t for t in (log_types or []) if t in LOG_TYPES]
	if types and len(types) < len(LOG_TYPES):
		outer.append("lm_type IN {types:Array(String)}")
		params["types"] = types
	if signature:
		outer.append("lm_sig = {signature:String}")
		params["signature"] = str(signature)
	if log_id:
		outer.append("lm_id = {log_id:String}")
		params["log_id"] = str(log_id)
	if exclude_resolved:
		resolutions = get_resolutions()
		if resolutions:
			# Drop every occurrence at or before its signature's resolved_at;
			# signatures never resolved compare against the epoch, i.e. keep all.
			outer.append(
				"lm_ts > parseDateTime64BestEffort(transform(lm_sig, {res_sigs:Array(String)}, "
				"{res_ats:Array(String)}, '1970-01-01 00:00:00'), 3, {tz:String})"
			)
			params["res_sigs"] = list(resolutions.keys())
			params["res_ats"] = list(resolutions.values())
	if search:
		outer.append("(positionCaseInsensitive(lm_content, {q:String}) > 0 OR positionCaseInsensitive(lm_site, {q:String}) > 0)")
		params["q"] = str(search)
	sql = f"FROM ({base_query(cols)})"
	if outer:
		sql += " WHERE " + " AND ".join(outer)
	return sql, params
