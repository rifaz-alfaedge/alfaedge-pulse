# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Background polling of every enabled Uptime Kuma Instance's /metrics
endpoint, and the cross-instance-consensus alerting rule built on top of it.

Design notes:

- **The bounded-loop/heartbeat pattern from ``poller.py``, not a plain
  cron tick** — see that module's docstring for the full reasoning.
  ``poll_all_instances`` (one full cycle) is unchanged; ``run_uptime_poll_loop``
  just calls it every ``poll_interval_seconds`` from inside a background
  job that loops internally, giving genuine sub-minute polling using only
  the RQ worker Frappe already runs — no new infrastructure. Bounded to
  ``MAX_LOOP_SECONDS`` per invocation (not a true infinite loop) so a
  graceful `bench restart` never has to wait it out; a watchdog
  (``ensure_uptime_poller_running``, wired to the 1-minute cron and
  ``after_migrate``) restarts it once its heartbeat goes stale — both
  after a crash and after each bounded invocation's normal exit. Below
  the old 1-minute-cron design, ``poll_interval_seconds`` was mostly a
  no-op (the cron tick and configured interval always coincided at the
  cron floor); now it's read fresh every cycle and genuinely drives how
  often polling happens, all the way down to single-digit seconds.
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
- **Criticality is a two-tier check, not a single number.** Each instance
  first decides its own verdict from its own last N checks (more than
  half down — unchanged, per-instance rule). A site then counts as
  fleet-wide Critical only once *every* instance that has a verdict
  agrees it's down — deliberately unanimous, not a majority: this is the
  whole reason to run multiple instances, so one location's own network
  hiccup can't declare a site down on its own — every vantage point has
  to agree first. See ``_reconcile_site_criticality``. Every ``Uptime
  Site`` sibling for a given site_name is kept in sync to the same
  is_critical value — it's a
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

import signal
import time

import frappe
from frappe.utils import add_to_date, cint, get_datetime, now_datetime
from frappe.utils.password import get_decrypted_password

from proxmox_monitor.tasks.poller import _handle_transition
from proxmox_monitor.uptime_kuma_client.base import UptimeKumaAPIError
from proxmox_monitor.uptime_kuma_client.metrics_client import fetch_metrics
from proxmox_monitor.uptime_monitor.api import auto_import_new_monitors, sync_missing_sites_to_instance

DEFAULT_POLL_INTERVAL_SECONDS = 60
DEFAULT_ALERT_WINDOW_CHECKS = 3
DEFAULT_AUTO_IMPORT_INTERVAL_MINUTES = 15
DEFAULT_CHECK_LOG_RETENTION_DAYS = 90

# Deleted in slices rather than one statement — this table gets a row per
# site per instance per poll cycle, so at a busy interval a full purge can
# be millions of rows; one giant DELETE would hold a long-running
# transaction/lock over the same table _poll_instance is trying to insert
# into concurrently. 5,000 was picked to keep each slice's lock brief
# without needing thousands of round trips either.
_PURGE_BATCH_SIZE = 5000

# Confirmed against Uptime Kuma's own source (server/prometheus.js):
# "Monitor Status (1 = UP, 0 = DOWN, 2 = PENDING, 3 = MAINTENANCE)".
STATUS_UP = 1
STATUS_DOWN = 0
_STATUS_LABEL = {STATUS_UP: "Up", STATUS_DOWN: "Down", 2: "Pending", 3: "Maintenance"}

UPTIME_POLLER_JOB_ID = "proxmox_monitor_uptime_poller"
UPTIME_HEARTBEAT_CACHE_KEY = "proxmox_monitor:uptime_poller_heartbeat"

# See poller.py's MAX_LOOP_SECONDS for the full reasoning — same bound,
# same "long" queue, comfortably inside its stopwaitsecs.
MAX_LOOP_SECONDS = 300


# ---------------------------------------------------------------------------
# Loop lifecycle — mirrors poller.py's ensure_poller_running/run_poll_loop
# ---------------------------------------------------------------------------


def ensure_uptime_poller_running() -> None:
	"""Watchdog: (re)start the uptime poll loop if its heartbeat has gone stale.

	Wired to the same 1-minute cron as the Proxmox watchdog, and to
	``after_migrate`` so the loop comes up automatically after installing
	or updating the app. Also what picks the loop back up after each
	bounded ``run_uptime_poll_loop`` invocation ends normally.
	"""
	if frappe.cache().get_value(UPTIME_HEARTBEAT_CACHE_KEY):
		return  # loop is alive and ticking; nothing to do

	frappe.enqueue(
		"proxmox_monitor.tasks.uptime_kuma_poller.run_uptime_poll_loop",
		queue="long",
		# A little headroom over MAX_LOOP_SECONDS for the final cycle to
		# finish; RQ force-kills the job if it ever runs past this, as a
		# hard backstop behind the loop's own bound.
		timeout=MAX_LOOP_SECONDS + 120,
		job_id=UPTIME_POLLER_JOB_ID,
		# job_id alone does not dedupe in Frappe — deduplicate=True is a
		# separate, required opt-in. Without it, a job with this ID
		# already queued/running wouldn't actually block a second enqueue.
		deduplicate=True,
	)


def run_uptime_poll_loop() -> None:
	"""One bounded run of the uptime poll loop (up to MAX_LOOP_SECONDS), then returns normally.

	Not meant to be called directly outside of ``ensure_uptime_poller_running``,
	which is also what re-launches this once it returns.
	"""
	stop_requested = False

	def _request_stop(signum, frame):
		nonlocal stop_requested
		stop_requested = True

	# Belt-and-braces: if a SIGTERM *does* get forwarded into this job
	# (behaviour varies by RQ/worker configuration), exit within the
	# current sleep tick rather than waiting out the full bound below.
	previous_handler = signal.signal(signal.SIGTERM, _request_stop)
	start_time = time.monotonic()
	try:
		while not stop_requested and (time.monotonic() - start_time) < MAX_LOOP_SECONDS:
			interval = cint(frappe.db.get_single_value("Uptime Monitor Settings", "poll_interval_seconds"))
			interval = max(interval, 1) if interval else DEFAULT_POLL_INTERVAL_SECONDS

			# Heartbeat TTL is a few cycles wide so a single slow cycle (e.g.
			# one unreachable instance taking the full request timeout)
			# doesn't make the watchdog think the loop has died and spawn a
			# duplicate.
			frappe.cache().set_value(UPTIME_HEARTBEAT_CACHE_KEY, "1", expires_in_sec=max(interval * 4, 60))

			try:
				poll_all_instances()
			except Exception:
				# A failure here means something broke outside the
				# per-instance try/except in poll_all_instances (e.g.
				# reading Settings itself) — log it but keep the loop
				# alive rather than let one bad cycle end the whole poller.
				frappe.log_error(title="Uptime Monitor: poll cycle failed", message=frappe.get_traceback())

			# Sleep in 1s increments (rather than one time.sleep(interval)
			# call) so a SIGTERM, if forwarded into this job, lands within
			# ~1s instead of waiting out the full poll interval.
			for _ in range(interval):
				if stop_requested:
					break
				time.sleep(1)
	finally:
		signal.signal(signal.SIGTERM, previous_handler)


def poll_all_instances() -> None:
	"""One full poll cycle, called every ``poll_interval_seconds`` by
	``run_uptime_poll_loop``. Never lets one instance's failure block
	another's (mirrors ``poller.py``'s per-server isolation in
	``sync_all_servers``). Criticality is reconciled fleet-wide once at
	the end, after every instance's own poll — it needs this cycle's
	checks from all of them already recorded before it can count votes."""
	settings = frappe.get_cached_doc("Uptime Monitor Settings")
	interval_seconds = cint(settings.poll_interval_seconds) or DEFAULT_POLL_INTERVAL_SECONDS
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
			_poll_instance(instance.name, interval_seconds)
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


def _poll_instance(instance_name: str, interval_seconds: int) -> None:
	doc = frappe.get_doc("Uptime Kuma Instance", instance_name)
	if doc.last_synced:
		elapsed_seconds = (now_datetime() - get_datetime(doc.last_synced)).total_seconds()
		if elapsed_seconds < interval_seconds:
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
	"""Cross-instance consensus: a site counts as fleet-wide Critical only
	once *every* instance that has an opinion on it (see
	``_instance_verdict``) reports it Down — not the moment any single
	location's own check flips, which could just be that location's own
	network hiccup (the whole reason to run multiple instances at all). A
	paused site's instance doesn't get a vote either (its checks have
	gone stale, not down). Runs once per poll cycle, after every enabled
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
		is_critical = total_votes > 0 and down_votes == total_votes

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
			f"{site_name} is down (flagged Down by all of its monitoring instances).",
			recovery_message=f"{site_name} is back up.",
		)


def purge_old_check_logs() -> None:
	"""Deletes Uptime Check Log rows older than Check Log Retention (days).

	Wired to Frappe's daily scheduler event (see hooks.py) — this table
	only ever grows (one row per site per instance per poll cycle, and
	polling can now run every few seconds — see this module's docstring),
	so without a cap it becomes the next version of the LLM Usage Log
	bloat this app has already hit once. Alerting never looks past the
	last few checks (Alert Window), so nothing functional depends on
	history sticking around past what the dashboard's own history view
	ever offers (7/30/90 days — see frontend/src/components/UptimePanel.tsx).
	Deleted in batches — see _PURGE_BATCH_SIZE."""
	retention_days = cint(
		frappe.db.get_single_value("Uptime Monitor Settings", "check_log_retention_days")
	) or DEFAULT_CHECK_LOG_RETENTION_DAYS
	cutoff = add_to_date(now_datetime(), days=-retention_days)

	total_deleted = 0
	while True:
		names = frappe.get_all(
			"Uptime Check Log", filters={"checked_at": ["<", cutoff]}, limit_page_length=_PURGE_BATCH_SIZE, pluck="name"
		)
		if not names:
			break
		frappe.db.delete("Uptime Check Log", {"name": ["in", names]})
		frappe.db.commit()
		total_deleted += len(names)
		if len(names) < _PURGE_BATCH_SIZE:
			break

	if total_deleted:
		frappe.logger().info(f"Uptime Monitor: purged {total_deleted} check log row(s) older than {retention_days} days")
