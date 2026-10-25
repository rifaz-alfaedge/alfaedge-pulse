# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Scheduled downtime windows for Uptime sites (``Uptime Maintenance Window``).

A window is a planned shutdown, so checks during it must not count as an
outage. They are still recorded, flagged ``in_maintenance``, but excluded
from uptime %, response time and Site Down alerts — see
``tasks.uptime_kuma_poller``.

Windows are keyed by ``site_name``, like every other site action (see
``uptime_monitor.api``'s module docstring), so one window covers the site
on every Kuma instance. They never touch Kuma itself.

Three kinds, in the site's system timezone (what ``now_datetime()`` uses):

- **Daily**: ``start_time``–``end_time`` every day. An end at or before the
  start runs past midnight (21:00–07:00).
- **Weekly**: the ticked weekdays, either ``all_day`` or a time range; a
  range that runs past midnight belongs to the day it starts.
- **One-time**: ``start_datetime``–``end_datetime``.

A site with both a nightly and a Sunday shutdown has two windows.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import frappe
from frappe.utils import cint, get_datetime, to_timedelta

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
WINDOW_FIELDS = [
	"name", "site_name", "enabled", "window_type", "all_day", "start_time", "end_time",
	"start_datetime", "end_datetime", "note", *WEEKDAYS,
]

#: How far back/ahead repeating windows are expanded when looking for the
#: last or next one — a weekly window always has an occurrence within this.
_SEARCH_DAYS = 8


def windows_by_site(site_names: list[str] | None = None) -> dict[str, list]:
	"""Enabled windows grouped by site_name — one query per poll cycle."""
	filters = {"enabled": 1}
	if site_names is not None:
		filters["site_name"] = ["in", site_names or [""]]
	grouped: dict[str, list] = {}
	for w in frappe.get_all("Uptime Maintenance Window", filters=filters, fields=WINDOW_FIELDS):
		grouped.setdefault(w.site_name, []).append(w)
	return grouped


def occurrences(window, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
	"""Every (from, to) occurrence of ``window`` overlapping [start, end)."""
	if window.window_type == "One-time":
		if not (window.start_datetime and window.end_datetime):
			return []
		s, e = get_datetime(window.start_datetime), get_datetime(window.end_datetime)
		return [(s, e)] if s < end and e > start else []

	days = {i for i, d in enumerate(WEEKDAYS) if cint(window.get(d))}
	weekly = window.window_type == "Weekly"
	if weekly and not days:
		return []
	all_day = weekly and cint(window.all_day)
	if not all_day and (window.start_time is None or window.end_time is None):
		return []
	t_start = timedelta(0) if all_day else to_timedelta(window.start_time)
	t_end = timedelta(0) if all_day else to_timedelta(window.end_time)

	out = []
	# Start a day early: last night's window can still be running today.
	day: date = start.date() - timedelta(days=1)
	while day <= end.date():
		if not weekly or day.weekday() in days:
			midnight = datetime.combine(day, datetime.min.time())
			s = midnight + t_start
			e = midnight + t_end if t_end > t_start else midnight + timedelta(days=1) + t_end
			if s < end and e > start:
				out.append((s, e))
		day += timedelta(days=1)
	return out


def active_window(windows: list, at: datetime | None = None):
	"""The window covering ``at`` (default now), or None."""
	at = at or frappe.utils.now_datetime()
	for w in windows:
		for s, e in occurrences(w, at, at + timedelta(seconds=1)):
			if s <= at < e:
				return w
	return None


def last_window_end(windows: list, at: datetime | None = None) -> datetime | None:
	"""The latest end of a window before ``at``. Checks taken before it are
	from before (or during) the shutdown, so they say nothing about whether
	the site came back."""
	at = at or frappe.utils.now_datetime()
	ends = [
		e for w in windows for _, e in occurrences(w, at - timedelta(days=_SEARCH_DAYS), at) if e <= at
	]
	return max(ends) if ends else None


def next_window(windows: list, at: datetime | None = None) -> tuple[datetime, datetime] | None:
	"""The next occurrence starting after ``at``, as (from, to)."""
	at = at or frappe.utils.now_datetime()
	upcoming = []
	for w in windows:
		horizon = at + timedelta(days=_SEARCH_DAYS)
		if w.window_type == "One-time" and w.start_datetime:
			horizon = max(horizon, get_datetime(w.start_datetime) + timedelta(seconds=1))
		upcoming += [(s, e) for s, e in occurrences(w, at, horizon) if s > at]
	return min(upcoming) if upcoming else None


def intervals(windows: list, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
	"""All occurrences of these windows overlapping [start, end), for
	marking maintenance on an availability strip."""
	return sorted(occ for w in windows for occ in occurrences(w, start, end))


def describe(window) -> str:
	"""Short human text: "Daily 21:00–07:00", "Weekly · Sun · all day"."""
	def hm(value) -> str:
		total = int(to_timedelta(value).total_seconds()) // 60
		return f"{total // 60:02d}:{total % 60:02d}"

	if window.window_type == "One-time":
		s, e = get_datetime(window.start_datetime), get_datetime(window.end_datetime)
		return f"One-time {s:%d %b %H:%M} → {e:%d %b %H:%M}"
	times = "all day" if cint(window.all_day) and window.window_type == "Weekly" else f"{hm(window.start_time)}–{hm(window.end_time)}"
	if window.window_type == "Daily":
		return f"Daily {times}"
	days = ", ".join(d[:3].capitalize() for d in WEEKDAYS if cint(window.get(d)))
	return f"Weekly · {days} · {times}"
