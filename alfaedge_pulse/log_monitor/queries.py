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
key); derived-column filters (type, signature, search) sit outside it.

- **Log type**: "Error" when the content (or the mapped Log Type column,
  if one is configured) contains the configured keyword, case-
  insensitively; "General" otherwise.
- **Title**: the mapped Title column if set; otherwise the last non-empty
  line of a Python traceback (the actual ``SomeError: message`` line —
  the first line is always just "Traceback (most recent call last):"), or
  the first non-empty line of anything else.
- **Signature** (what "the same error" means for recurring-error
  grouping): a hash of the site URL plus the title with every volatile
  token — numbers, long hex runs (hashes, docnames), and quoted values —
  replaced by ``?``, so "Sales Invoice SINV-00123 not found" and
  "... SINV-00456 not found" on the same site count as one error.
- **Timestamps** go in as ``String`` params converted with an explicit
  timezone (the site's system timezone), and come back formatted in that
  same timezone, so the dashboard's "naive string = site-local" convention
  holds no matter what timezone the ClickHouse server itself runs in.
"""

from __future__ import annotations

from frappe.utils import get_system_timezone

from alfaedge_pulse.log_monitor.connection import ColumnMap, get_column_map, get_settings

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
	return (
		f"substring(trimBoth(if(startsWith(trimLeft(toString({cols.content})), 'Traceback'), "
		f"arrayElement({lines}, -1), arrayElement({lines}, 1))), 1, 300)"
	)


def log_type_expr(cols: ColumnMap) -> str:
	source = cols.log_type or cols.content
	return f"if(positionCaseInsensitive(toString({source}), {{kw:String}}) > 0, 'Error', 'General')"


def base_query(cols: ColumnMap, site_filter: bool = False) -> str:
	"""Inner SELECT over [start, end). Needs params ``start``, ``end``,
	``tz``, ``kw`` (see ``base_params``), plus ``sites`` if site_filter."""
	title = title_expr(cols)
	log_id = f"toString({cols.id})" if cols.id else f"toString(cityHash64({cols.ts}, {cols.site}, {cols.content}))"
	where = [
		f"{cols.ts} >= toDateTime({{start:String}}, {{tz:String}})",
		f"{cols.ts} < toDateTime({{end:String}}, {{tz:String}})",
	]
	if site_filter:
		where.append(f"toString({cols.site}) IN {{sites:Array(String)}}")
	return f"""
		SELECT
			{cols.ts} AS lm_ts,
			toString({cols.site}) AS lm_site,
			toString({cols.content}) AS lm_content,
			{title} AS lm_title,
			{log_type_expr(cols)} AS lm_type,
			toString(cityHash64(lm_site, replaceRegexpAll(lm_title, '{_NORMALIZE_RE}', '?'))) AS lm_sig,
			{log_id} AS lm_id
		FROM {cols.table}
		WHERE {" AND ".join(where)}
	"""


def base_params(start: str, end: str) -> dict:
	settings = get_settings()
	return {
		"start": str(start),
		"end": str(end),
		"tz": get_system_timezone(),
		"kw": (settings.error_keyword or "error").strip() or "error",
	}


def ts_out(alias: str = "lm_ts") -> str:
	"""Format a timestamp for the dashboard in the site's timezone."""
	return f"formatDateTime(toTimeZone({alias}, {{tz:String}}), '%Y-%m-%d %H:%i:%S')"


def build_filtered(
	start: str,
	end: str,
	sites: list[str] | None = None,
	log_types: list[str] | None = None,
	signature: str | None = None,
	search: str | None = None,
	log_id: str | None = None,
	cols: ColumnMap | None = None,
) -> tuple[str, dict]:
	"""``FROM (...) WHERE ...`` fragment + params with all dashboard filters
	applied — callers prepend their own SELECT and append GROUP/ORDER."""
	cols = cols or get_column_map()
	params = base_params(start, end)
	if sites:
		params["sites"] = list(sites)
	outer = []
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
	if search:
		outer.append("(positionCaseInsensitive(lm_content, {q:String}) > 0 OR positionCaseInsensitive(lm_site, {q:String}) > 0)")
		params["q"] = str(search)
	sql = f"FROM ({base_query(cols, site_filter=bool(sites))})"
	if outer:
		sql += " WHERE " + " AND ".join(outer)
	return sql, params
