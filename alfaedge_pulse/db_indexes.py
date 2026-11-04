# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Database indexes Pulse's queries rely on, checked on every migrate.

Doctype JSON cannot declare a composite index, so these used to be created by
one-off patches. That does not work on a site where the app is installed fresh:
Frappe's ``install-app`` records every patch in ``patches.txt`` as already run
without running it, so such a site never got any of them. On production that
left the history tables without indexes, and a routine "latest row for this
host" lookup scanned 1.6 million rows (5 seconds) on every poll.

``ensure_indexes`` is idempotent and cheap when everything exists (one SHOW INDEX
per table). It runs from ``after_install`` and ``after_migrate``, and never
raises: a failed index is logged and the migrate goes on.
"""

from __future__ import annotations

import frappe

#: (doctype, columns, what needs it)
INDEXES: list[tuple[str, list[str], str]] = [
	# "Latest row for this host and bench", on every poll and in the overview.
	("Frappe Worker Health Log", ["monitored_host", "bench_name", "timestamp"], "streaks and latest worker health per host"),
	("Frappe Worker Health Log", ["timestamp"], "daily retention purge"),
	("Frappe Failed Job Log", ["failure_signature"], "grouping failed jobs"),
	("Frappe Failed Job Log", ["monitored_host", "resolved", "last_seen"], "open failed jobs per host"),
	("Frappe Failed Job Log", ["resolved", "resolved_at"], "daily retention purge"),
	("Resource Metric Log", ["monitored_host", "collected_at"], "latest load/swap per host and trends"),
	("Resource Metric Log", ["collected_at"], "daily retention purge"),
	("Resource Disk Sample", ["resource_mount", "collected_at"], "disk usage history and fill forecast"),
	("Resource Disk Sample", ["collected_at"], "daily retention purge"),
	("Memory Metric Log", ["monitored_host", "collected_at"], "latest sample and trends"),
	("Memory Metric Log", ["collected_at"], "daily retention purge"),
	("Disk IO Sample", ["monitored_host", "device", "collected_at"], "latest sample per disk and trends"),
	("Disk IO Sample", ["collected_at"], "daily retention purge"),
	("MariaDB Metric Log", ["monitored_host", "collected_at"], "latest sample and trends"),
	("MariaDB Metric Log", ["collected_at"], "daily retention purge"),
	("MariaDB Config Snapshot", ["monitored_host", "collected_at"], "latest settings and sizes"),
	("MariaDB Config Snapshot", ["collected_at"], "daily retention purge"),
	("Uptime Check Log", ["site", "checked_at"], "uptime history per site"),
	("LLM Usage Log", ["source", "request_timestamp"], "AI Usage filters"),
	("LLM Usage Log", ["provider", "request_timestamp"], "AI Usage filters"),
	("LLM Usage Log", ["model", "request_timestamp"], "AI Usage filters"),
	("LLM Usage Log", ["status", "request_timestamp"], "AI Usage filters"),
]


def _existing_keys(doctype: str) -> list[list[str]]:
	"""Column lists of every index on the doctype's table, in index order."""
	keys: dict[str, list[tuple[int, str]]] = {}
	for row in frappe.db.sql(f"SHOW INDEX FROM `tab{doctype}`", as_dict=True):
		keys.setdefault(row["Key_name"], []).append((row["Seq_in_index"], row["Column_name"]))
	return [[col for _, col in sorted(cols)] for cols in keys.values()]


def has_index(existing: list[list[str]], columns: list[str]) -> bool:
	"""True if some index starts with these columns: a wider index serves the
	same lookups, so it is not duplicated."""
	return any(key[: len(columns)] == columns for key in existing)


def ensure_indexes(say=None) -> list[str]:
	"""Create whichever of ``INDEXES`` is missing. Returns what was created."""
	say = say or (lambda message: None)
	created: list[str] = []
	for doctype, columns, reason in INDEXES:
		try:
			if not frappe.db.table_exists(doctype, cached=False):
				continue
			if has_index(_existing_keys(doctype), columns):
				continue
			say(f"adding index {doctype} ({', '.join(columns)}) for {reason} ...")
			frappe.db.add_index(doctype, columns)
			created.append(f"{doctype} ({', '.join(columns)})")
		except Exception:
			frappe.log_error(title=f"Pulse: could not add index {doctype} {columns}", message=frappe.get_traceback())
			say(f"could not add index {doctype} ({', '.join(columns)}); see the Error Log")
	return created


if __name__ == "t":  # `bench execute` runs a copy of this file with that name
	done = ensure_indexes(say=print)
	frappe.db.commit()
	print(f"Done. Added {len(done)} index(es)." if done else "Every index was already there.")
