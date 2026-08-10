# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Background polling of every enabled Uptime Kuma Instance's /metrics
endpoint, and the cross-instance-consensus alerting rule built on top of it.

Design notes:

- **A plain self-throttled function on the existing every-minute cron**,
  same pattern as ``tasks.bifrost_sync`` — see that module's docstring for
  why this isn't the bounded-loop/heartbeat pattern ``poller.py`` uses.
  At the default 1-minute interval this throttle is a no-op (the cron
  tick and the configured interval coincide), but the field stays
  Desk-editable without a code/hooks.py change if ever slowed down.
- **We build our own heartbeat history** (``Uptime Check Log``) from
  Kuma's official, stable ``/metrics`` endpoint rather than relying on
  Kuma's own retention or its unofficial Socket.IO heartbeat API — see
  ``uptime_kuma_client/socketio_client.py``'s module docstring for the
  full reasoning (that client is reserved for admin-initiated monitor
  management only).
- **Pending/Maintenance readings are excluded from the down-ratio
  calculation entirely** — no ``Uptime Check Log`` row is even written
  for them, so "more than half of the last N checks" only ever considers
  genuine Up/Down results, never "was paused/in a maintenance window."
- **Criticality is a two-tier vote, not a single number.** Each instance
  first decides its own verdict from its own last N checks (more than
  half down — unchanged, per-instance rule). A site then counts as
  fleet-wide Critical once *at least half* of the instances that have a
  verdict agree it's down — deliberately a looser ">=" than the strict
  ">" used for the per-instance vote, matching what was asked for. This
  is the whole reason to run multiple instances: one location's own
  network hiccup outvoting itself shouldn't declare a site down; a real
  majority of vantage points agreeing should. See
  ``_reconcile_site_criticality``. Every ``Uptime Site`` sibling for a
  given site_name is kept in sync to the same is_critical value — it's a
  per-site fact, not a per-instance one, once instances are meant to
  mirror each other (see ``uptime_monitor.api``'s module docstring).
- **The alert edge-trigger reuses ``poller.py``'s ``_handle_transition``
  directly** rather than re-implementing the same was_critical/is_critical
  dispatch-once-per-incident logic a second time — it's already
  generic (doctype/name/alert_type as plain arguments), so this is a
  straightforward cross-module reuse, not a duplication. It now fires
  once per site (fleet-wide), not once per instance.
- **Auto-import piggybacks on this same cron tick, self-throttled
  independently** (its own ``last_auto_import``/``auto_import_interval_minutes``,
  separate from polling's) since it needs a Socket.IO login rather than a
  cheap HTTP GET — running it every poll cycle would be needless overhead
  at the default 1-minute poll interval. It's gated first, per instance,
  before that instance's ``/metrics`` poll runs, so a newly-imported site
  gets its first check in the very same cycle rather than waiting a full
  poll interval.
- **"Auto-import" covers both directions of keeping instances in sync**:
  pulling monitors Kuma already has that we don't (``auto_import_new_monitors``
  — e.g. a monitor added directly in Kuma), and pushing site definitions
  we already have on *other* instances onto one that's missing them
  (``sync_missing_sites_to_instance`` — e.g. a newly connected instance,
  or one added specifically to watch existing sites from a new location).
  Both run under the one setting/interval since they're the same
  "reconcile this instance against the fleet" operation from opposite
  directions — see ``uptime_monitor.api``'s module docstring for the fuller
  reasoning on why site_name is the sync key.
"""

from __future__ import annotations

import frappe
from frappe.utils import cint, get_datetime, now_datetime
from frappe.utils.password import get_decrypted_password

from proxmox_monitor.tasks.poller import _handle_transition
from proxmox_monitor.uptime_kuma_client.base import UptimeKumaAPIError
from proxmox_monitor.uptime_kuma_client.metrics_client import fetch_metrics
from proxmox_monitor.uptime_monitor.api import auto_import_new_monitors, sync_missing_sites_to_instance

DEFAULT_POLL_INTERVAL_MINUTES = 1
DEFAULT_ALERT_WINDOW_CHECKS = 3
DEFAULT_AUTO_IMPORT_INTERVAL_MINUTES = 15

# Confirmed against Uptime Kuma's own source (server/prometheus.js):
# "Monitor Status (1 = UP, 0 = DOWN, 2 = PENDING, 3 = MAINTENANCE)".
STATUS_UP = 1
STATUS_DOWN = 0
_STATUS_LABEL = {STATUS_UP: "Up", STATUS_DOWN: "Down", 2: "Pending", 3: "Maintenance"}


def poll_all_instances() -> None:
	"""Cron entrypoint. Never lets one instance's failure block another's
	(mirrors ``poller.py``'s per-server isolation in ``sync_all_servers``).
	Criticality is reconciled fleet-wide once at the end, after every
	instance's own poll — it needs this cycle's checks from all of them
	already recorded before it can count votes."""
	settings = frappe.get_cached_doc("Uptime Monitor Settings")
	interval_minutes = cint(settings.poll_interval_minutes) or DEFAULT_POLL_INTERVAL_MINUTES
	window = cint(settings.alert_window_checks) or DEFAULT_ALERT_WINDOW_CHECKS
	auto_import_enabled = bool(cint(settings.auto_import_enabled))
	auto_import_interval = cint(settings.auto_import_interval_minutes) or DEFAULT_AUTO_IMPORT_INTERVAL_MINUTES

	for instance in frappe.get_all("Uptime Kuma Instance", filters={"enabled": 1}, fields=["name"]):
		if auto_import_enabled:
			try:
				_maybe_auto_import(instance.name, auto_import_interval)
			except Exception:
				frappe.db.rollback()
				frappe.log_error(title=f"Uptime Monitor: auto-import failed for {instance.name}", message=frappe.get_traceback())
		try:
			_poll_instance(instance.name, interval_minutes)
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"Uptime Monitor: poll failed for {instance.name}", message=frappe.get_traceback())

	try:
		_reconcile_site_criticality(window)
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title="Uptime Monitor: criticality reconciliation failed", message=frappe.get_traceback())


def _maybe_auto_import(instance_name: str, interval_minutes: int) -> None:
	doc = frappe.get_doc("Uptime Kuma Instance", instance_name)
	if doc.last_auto_import:
		elapsed_minutes = (now_datetime() - get_datetime(doc.last_auto_import)).total_seconds() / 60
		if elapsed_minutes < interval_minutes:
			return

	try:
		auto_import_new_monitors(instance_name)
		sync_missing_sites_to_instance(instance_name)
		frappe.db.set_value(
			"Uptime Kuma Instance", instance_name, {"last_auto_import": now_datetime(), "last_auto_import_error": ""}
		)
	except UptimeKumaAPIError as e:
		frappe.db.set_value(
			"Uptime Kuma Instance", instance_name, {"last_auto_import": now_datetime(), "last_auto_import_error": str(e)}
		)
	frappe.db.commit()


def _poll_instance(instance_name: str, interval_minutes: int) -> None:
	doc = frappe.get_doc("Uptime Kuma Instance", instance_name)
	if doc.last_synced:
		elapsed_minutes = (now_datetime() - get_datetime(doc.last_synced)).total_seconds() / 60
		if elapsed_minutes < interval_minutes:
			return

	password = get_decrypted_password("Uptime Kuma Instance", instance_name, "password", raise_exception=False)
	api_key = get_decrypted_password("Uptime Kuma Instance", instance_name, "api_key", raise_exception=False)

	try:
		metrics = fetch_metrics(doc.base_url, doc.username, password, api_key=api_key or None, verify_ssl=bool(doc.verify_ssl))
	except UptimeKumaAPIError as e:
		frappe.db.set_value("Uptime Kuma Instance", instance_name, {"last_synced": now_datetime(), "last_error": str(e)})
		frappe.db.commit()
		return

	frappe.db.set_value("Uptime Kuma Instance", instance_name, {"last_synced": now_datetime(), "last_error": ""})

	sites = frappe.get_all("Uptime Site", filters={"instance": instance_name}, fields=["name", "site_name"])
	for site in sites:
		try:
			_record_site_check(site, metrics)
		except Exception:
			frappe.log_error(title=f"Uptime Monitor: failed to process site {site.site_name}", message=frappe.get_traceback())
	frappe.db.commit()


def _record_site_check(site, metrics: dict) -> None:
	"""Records this instance's own reading only — no criticality/alerting
	decision here anymore, since that's now a fleet-wide vote taken once
	every instance has reported in (see ``_reconcile_site_criticality``)."""
	reading = metrics.get(site.site_name)
	if reading is None or reading.get("status") is None:
		# No current /metrics reading for this site name on this instance —
		# e.g. deleted directly in Kuma out of band, or a brand-new monitor
		# that hasn't completed its first check yet. Nothing to record.
		return

	status = int(reading["status"])
	now = now_datetime()
	current_status = _STATUS_LABEL.get(status, "Pending")

	if status in (STATUS_UP, STATUS_DOWN):
		frappe.get_doc(
			{
				"doctype": "Uptime Check Log",
				"site": site.name,
				"checked_at": now,
				"is_up": 1 if status == STATUS_UP else 0,
				"raw_status": str(status),
				"response_time_ms": reading.get("response_time_ms"),
			}
		).insert(ignore_permissions=True)
	# Pending/Maintenance: no log row — same "doesn't count" treatment it's
	# always had, now also meaning it won't cast a vote in the fleet-wide
	# reconciliation below (see _instance_verdict).

	frappe.db.set_value("Uptime Site", site.name, {"current_status": current_status, "last_checked": now})


def _instance_verdict(site_doc_name: str, window: int) -> bool | None:
	"""One instance's own opinion on one site: more than half of its last
	``window`` counted (Up/Down only) checks report Down. ``None`` — not
	"up" — when there isn't a full window of samples yet, so a brand-new
	instance/site pairing doesn't cast a premature vote either way in the
	fleet-wide count below."""
	rows = frappe.get_all(
		"Uptime Check Log",
		filters={"site": site_doc_name},
		fields=["is_up"],
		order_by="checked_at desc",
		limit_page_length=window,
	)
	if len(rows) < window:
		return None
	down_count = sum(1 for r in rows if not r.is_up)
	return (down_count / len(rows)) > 0.5


def _reconcile_site_criticality(window: int) -> None:
	"""Cross-instance consensus: a site counts as fleet-wide Critical once
	*at least half* of the instances that have an opinion on it (see
	``_instance_verdict``) report it Down — not the moment any single
	location's own check flips, which could just be that location's own
	network hiccup (the whole reason to run multiple instances at all). A
	paused site's instance doesn't get a vote either (its checks have
	gone stale, not down). Runs once per cron tick, after every enabled
	instance's own poll above, since it needs this cycle's checks from all
	of them already recorded. Every sibling for a site_name is kept in
	sync to the same is_critical value, and the alert fires once per site
	— not once per instance — via the usual was/is edge-trigger."""
	all_sites = frappe.get_all(
		"Uptime Site", fields=["name", "site_name", "is_critical", "is_active"], order_by="name asc"
	)
	groups: dict[str, list] = {}
	for site in all_sites:
		groups.setdefault(site.site_name, []).append(site)

	for site_name, siblings in groups.items():
		down_votes = 0
		total_votes = 0
		for sibling in siblings:
			if not cint(sibling.is_active):
				continue
			verdict = _instance_verdict(sibling.name, window)
			if verdict is None:
				continue
			total_votes += 1
			if verdict:
				down_votes += 1

		was_critical = bool(cint(siblings[0].is_critical))
		is_critical = total_votes > 0 and (down_votes / total_votes) >= 0.5

		if is_critical != was_critical:
			for sibling in siblings:
				frappe.db.set_value("Uptime Site", sibling.name, "is_critical", 1 if is_critical else 0)
			frappe.db.commit()

		_handle_transition(
			was_critical,
			is_critical,
			"Uptime Site",
			siblings[0].name,
			"Site Down",
			f"{site_name} is down (flagged Down by at least half of its monitoring instances).",
			recovery_message=f"{site_name} is back up.",
		)
