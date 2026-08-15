# Changelog

All notable changes to this project are documented here.

## [3.4.0] - 2026-08-15

### Fixed
- **Server status strip's Disk figure** now averages each server's actual
  physical drives (`Proxmox Datastore.usage_percent` — the same rows the
  host cards' disk meters already read from) instead of
  `Proxmox Server.storage_usage`, which is only the root filesystem (`/`)
  and is explicitly excluded from alerting server-side because it doesn't
  reflect this fleet's real two-drive-per-host layout (see `poller.py`'s
  `_apply_host_status`). Shows "—" for a server with no datastore rows
  synced yet, not a misleading 0%.

## [3.3.0] - 2026-08-15

### Added
- **Server status strip** — a thin, always-visible, view-only bar pinned
  above the header (sticky, so it stays on screen across every tab, not
  just Usage Metrics) showing every non-PBS Proxmox server's name plus
  CPU/RAM/Disk percentages, color-coded on the same warning/critical
  thresholds as everywhere else on the dashboard, or an "Offline" badge in
  place of the numbers when a server is unreachable.

## [3.2.0] - 2026-08-14

### Added
- **Dashboard redesign.** The six separate Critical/Warning summary cards
  (Instances, Sites, Hosts/Services) that used to sit above every tab's
  content are gone, replaced by a single bottom-right **Alerts** popup —
  two independently clickable filter chips (⛔ Critical / ⚠️ Warning),
  merged across Proxmox, Uptime, and Host Health, each tagged with its
  source subsystem.
- **Primary navigation now reads as primary** — the Overview/Backups/
  Alerts/... tab bar is visually larger than every panel's own internal
  filter-pill rows (new `Tabs` `size="lg"` variant, scoped to just this bar).
- **Multi-select filters**, backed by a new shared `MultiSelectFilter`
  component:
  - The dashboard header's per-server filter (was a single-select pill row,
    now a header dropdown next to **Critical**, sorted A→Z).
  - AI Usage's Provider/Model/Virtual Key filters (date range stays
    single-select). Backend filtering moved from exact-match to `IN`-clause
    across `llm_usage_monitor/api.py`'s four endpoints.
- **Overview → renamed "Usage Metrics"**, and tab order is now Host
  Health, Uptime, AI Usage, Usage Metrics, Backups, Alerts — Host Health is
  also the new default landing tab.
- **Overview/Usage Metrics card revamp**: meter rings are smaller and only
  render for metrics currently at warning/critical (a fully healthy card
  shows "✓ All metrics normal" instead of every ring all the time); guest
  cards show a Host Health status row when a Monitored Host is linked, and
  Host Health now also drives the card's own critical border/pulse
  animation, not just Proxmox CPU/RAM/disk; a new card-weight Uptime
  summary tile sits above the host/guest grid.
- **Host Health detail dialog**: the bench-health trend chart now also
  plots failed-job count (previously only orphan-worker count, which
  stayed flat at zero for most benches and looked broken); the flat failed-
  jobs list is replaced by the same human-readable root-cause summary
  "Download Log" writes to a file — grouped by exception type + method,
  with occurrence count and the actual error message — inline, with no
  download required (new `host_health.api.get_failed_job_groups`).
- **Dark/light mode toggle** in the header — previously followed the OS
  preference only with no manual override; a click now remembers the
  explicit choice (`localStorage`) and stops following the OS until
  cleared.

## [3.1.0] - 2026-08-14

### Added
- **Root-cause grouping for Failed Job Log.** Raw RQ failures are now
  collapsed into distinct root causes by exception type + enqueued method,
  instead of the operator having to make sense of a flat list. From a
  host's detail dialog on the Host Health tab, **Download Log** produces a
  plain-text log grouping that host's open failures by root cause with the
  full traceback for every occurrence underneath — built for real
  debugging, not just a dashboard count.
  - `Frappe Failed Job Log` gains `job_name`, `exc_type`, and
    `failure_signature` (`sha256(exc_type + job_name)`, indexed), computed
    inline at ingest time, no extra polling. `job_name` is newly captured
    by `pulse_agent` — specifically *not* the RQ job's `func_name`, which
    is the same constant dispatcher string
    (`frappe.utils.background_jobs.execute_job`) for every job Frappe ever
    enqueues; the real per-job identity lives in that dispatcher's own
    `job_name`/`method` kwargs instead (see `pulse_agent`'s v0.1.0
    changelog).
  - New `host_health.api.get_failed_job_log_text` endpoint, generating that
    grouped, downloadable log server-side per host — fetched on demand
    only when "Download Log" is clicked, not part of the dashboard's
    regular poll.
  - Existing "Failed Job Threshold" alerting is unchanged in this release —
    still raw per-bench open-row count, not signature-aware; a candidate for
    a future phase once real grouped data exists to design against.

## [3.0.0] - 2026-08-13

### Added
- **New Host Health module** — real-time monitoring of OS-level services
  (sshd, nginx, mariadb, redis-server) and Frappe/RQ bench internals
  (workers, orphaned workers, failed jobs, queue depth, scheduler
  heartbeat) across every Proxmox guest, fed by a small push agent
  (systemd timer, ~25s interval) rather than SSH-pull polling. A guest
  that stops pushing is itself the down-signal (heartbeat timeout), not a
  failed connection attempt.
  - New doctypes: *Monitored Host*, *Monitored Host Site*, *Expected
    Service*, *Service Status Log*, *Frappe Worker Health Log*, *Frappe
    Failed Job Log*, *Host Monitor Settings*.
  - New API-key-authenticated ingest endpoint
    (`host_health.ingest.push_status`) using Frappe's own built-in
    `api_key`/`api_secret` mechanism — every Monitored Host gets its own
    independent, revocable key pair via a shared, zero-permission agent
    identity.
  - Five new alert types (Service Down, Worker Degraded, Failed Job
    Threshold, Scheduler Stalled, Host Unreachable), routed through the
    exact same alert dispatch/dedup/recovery mechanism (and the same
    approved WhatsApp UTILITY template) as every other alert in this
    app — no parallel alerting system.
  - Per-bench **site inventory** (site name + URL, when explicitly
    configured via `site_config.json`) — inventory only for now, no
    reachability monitoring yet.
  - New "Host Health" dashboard tab, plus a fleet-wide always-visible
    Critical Hosts / Services Down summary.
- **The push agent now lives in its own app, `pulse_agent`**
  (github.com/rifaz-alfaedge/pulse-agent), installed separately on each
  monitored guest — not bundled into this app. The guest fleet runs a mix
  of Frappe/Python versions, and this app's own dependency/version
  constraints (tied to whatever the central Pulse site itself runs)
  aren't something every monitored guest should have to satisfy just to
  report its own health.

## [2.3.0] - 2026-08-12

### Fixed
- **LLM Usage Log's `raw_log` field was the single largest driver of
  database growth** — it stored Bifrost's full raw request/response
  payload for *every* synced request, including successful ones, which
  can run into the hundreds of KB each. It's now only kept for
  error/processing rows, where it's actually useful for debugging;
  everything worth reading off a successful request was already
  flattened into this doctype's other fields. Existing bloat should be
  cleaned up manually per-site (`UPDATE ... SET raw_log = NULL WHERE
  status = 'success'` + `OPTIMIZE TABLE`) since this only stops new
  growth, it doesn't retroactively shrink what's already stored.

### Added
- **Uptime Check Log now has a retention policy** — *Uptime Monitor
  Settings* → **Check Log Retention (days)**, default 90 (matching the
  dashboard's own max history window). Purged daily in batches. Without
  this the table only ever grows, and now that polling can run every few
  seconds instead of once a minute, it would have hit the same kind of
  unbounded growth as the `raw_log` issue above.

## [2.2.0] - 2026-08-11

### Changed
- **Fleet-wide Uptime criticality now requires unanimous agreement, not a
  majority.** A site is flagged Critical only once *every* monitoring
  instance with a verdict agrees it's down — previously at least half
  agreeing was enough. Each instance's own per-check rule (more than half
  of its own last N checks) is unchanged; only the cross-instance
  aggregation changed.

### Added
- **Uptime Kuma polling now runs as its own background loop, not a
  once-a-minute cron tick** — the same pattern already used for the
  Proxmox poller. Poll Interval (seconds) is a genuinely honored
  interval now, down to a few seconds, not capped at Frappe's one-minute
  scheduler floor. The "long" queue worker is now 2 processes instead of
  1, since this and the Proxmox poller are both perpetual background
  loops competing for the same queue — one worker meant one loop (or any
  other "long" job) could stall for minutes waiting its turn.
- **Poll Interval and Heartbeat Interval are now separate settings**
  (`Uptime Monitor Settings`) — Poll Interval is purely how often *we*
  read a result; the new Heartbeat Interval is how often *Kuma itself*
  actually checks (synced onto every Uptime Site and pushed to Kuma).
  Previously one field did both jobs at once.
- **Alert Subscription can now watch Uptime Sites.** A new "Uptime Sites"
  table alongside the existing "Servers / Instances" one lets an engineer
  self-subscribe to specific Uptime Kuma sites, on top of the global
  recipients in Proxmox Monitor Settings. Matched by site — subscribing
  via any one instance's copy of a site watches it everywhere, since
  sites are shared across every connected instance. No new WhatsApp
  template was needed: **Site Down** and its recovery notification
  already send through the same approved UTILITY template every other
  alert type uses.

## [2.1.0] - 2026-08-10

### Added
- **Import Existing Monitors.** A new button on the Uptime tab lists every
  monitor a Kuma instance already has that isn't tracked here yet (e.g.
  one added directly in Kuma before that instance was ever connected) and
  lets you pick which ones to bring in — nothing is created on Kuma's
  side, since they already exist there.
- **Instances are now kept in sync automatically.** Sites are a fleet-wide
  concept, not per-instance: **Add Site** creates a site on every enabled
  instance at once (no more picking one instance), and **Pause / Resume /
  Delete** apply to every instance's copy. A newly connected instance (or
  one added specifically to watch existing sites from a new location)
  automatically catches up to whatever the fleet already has, on the same
  self-throttled schedule as pulling in monitors added directly in Kuma —
  both directions live under **Keep Instances In Sync** in `Uptime
  Monitor Settings`, with no manual step required.
- **Criticality is now a fleet-wide vote.** A site counts as Critical once
  at least half of its monitoring instances independently agree it's
  down (each still judges from its own last-N-checks majority first) —
  not the moment any single location's own network hiccup says so. This
  is the actual point of running more than one Uptime Kuma instance:
  redundancy against one location's own connectivity issues, not just a
  second independent alert source.
- **A persistent Uptime Critical/Down summary now shows on every tab**,
  the same way the Proxmox Critical/Warning summary already did — not
  just the Uptime tab. Entries show just the site name and status; the
  per-instance breakdown stays on the Uptime tab's own site cards.
- **The Uptime site grid shows one card per site, not one per instance.**
  Each card lists every instance's own current status underneath, so a
  site up from one location and down from another is visible at a glance
  in one place instead of two separate, identically-named cards.

### Fixed
- **Adding or editing a site could crash with `AttributeError:
  'UptimeSite' object has no attribute 'validate'`.** A stray call
  assumed every Frappe document has a generic `validate()` method to call
  directly — it doesn't, unless the doctype defines one. Removed; the
  normal insert/save validation (and the existing orphaned-monitor
  cleanup if a local save still fails after Kuma succeeds) already covers
  this correctly.
- **Failed site actions showed a bare `[object Object]` instead of a
  message.** The error thrown by a failed API call isn't a real `Error`
  instance, so `instanceof Error` was always false and the fallback
  stringified the whole object. Now unwraps Frappe's actual error shape
  (including `frappe.throw()`'s user-facing message when present).
- **A successful "Import Existing Monitors" pass closed the dialog before
  its own result summary was ever visible** — both the result and the
  dialog-close state updates landed in the same render, so the summary
  never had a chance to paint. Both this and the equivalent "Add Site"
  outcome now stay open until closed manually.

## [2.0.0] - 2026-08-10

### Added
- **AI Usage dashboard (Bifrost).** A new "AI Usage" tab gives in-depth
  analytics for a self-hosted [Bifrost](https://getbifrost.ai) LLM
  gateway — total cost/tokens/requests, a cost-over-time and
  tokens-over-time chart, provider/model cost breakdowns, and a
  sortable/filterable recent-requests table. A new background job
  (`Bifrost Settings`) pulls request logs from Bifrost's Management API
  on a configurable interval (default 15 minutes, live-editable with no
  restart) into a new `LLM Usage Log` doctype — alfaEdge Pulse keeps its
  own permanent history independent of however long Bifrost itself
  retains logs. The sync is idempotent and self-healing (a request still
  `processing` at sync time is automatically reconciled once it
  completes). Designed to extend to direct-provider (OpenAI/Google/etc.)
  billing API tracking later without a schema rework.
- **Uptime monitoring (Uptime Kuma).** A new "Uptime" tab lets you add,
  pause, resume, and delete monitored sites across any number of
  connected [Uptime Kuma](https://github.com/louislam/uptime-kuma)
  instances (`Uptime Kuma Instance`) directly from the dashboard, see
  live Up/Down status and historical uptime %, and get "Critical Sites"
  vs. "Down (not yet critical)" summary cards at a glance. Status is
  polled every minute from Kuma's official Prometheus `/metrics`
  endpoint and recorded into alfaEdge Pulse's own `Uptime Check Log` —
  Kuma's own alerting stays off entirely. A site is flagged **Critical**
  once more than half of its last N checks (`Uptime Monitor Settings` >
  Alert Window Checks, default 3) report Down, and an alert fires
  through this app's existing Email/WhatsApp mechanism — the same
  "back to normal" recovery notification other alert types already get.
  Adding/editing/deleting a monitor uses Kuma's internal (unofficial)
  Socket.IO API; that risk is deliberately scoped to just that
  admin-initiated convenience feature, not the status polling alerting
  depends on.
- **Charting.** [uPlot](https://github.com/leeoniya/uPlot) (MIT
  licensed, ~21KB gzip) added as the dashboard's first charting
  dependency, used for the new cost/token/uptime trend lines. Simple
  proportional breakdowns (e.g. cost by provider) stay hand-rolled
  CSS/SVG — no library needed for those. Hovering a trend chart shows a
  floating tooltip with the exact date and value(s) under the cursor.
- **AI Usage: breakdown by Virtual Key.** A third breakdown card groups
  cost/tokens/requests by Bifrost Virtual Key — the closest the log data
  gets to "by project/team/consumer" — alongside the existing Provider
  and Model breakdowns.
- **AI Usage: timespan and filter controls.** The fixed 7d/30d/90d range
  tabs are replaced with a date filter offering Today, Yesterday, Last
  7/30/90 Days, or a Custom Range (two date pickers), plus dropdown
  filters for Provider, Model, and Virtual Key — populated from the
  distinct values actually present in your synced data. All apply
  together across the summary tiles, both trend charts, every breakdown
  card, and the recent-requests table.

### Fixed
- **Timestamps could be off by the site's UTC offset.** `timeAgo`/the
  live-freshness check treated every Frappe datetime as UTC, which is
  wrong on any site not configured for the UTC timezone (this
  deployment's is IST, UTC+5:30) — Frappe stores/serializes datetimes
  naively in the site's own system timezone, with no UTC conversion and
  no timezone context available to a standalone SPA like this one to
  convert with. Now parsed as browser-local time instead, correct
  whenever the viewer's timezone matches the site's (the expected case
  for an internal ops dashboard).
- **Bifrost auth model corrected to Basic Auth.** Self-hosted Bifrost
  (non-Enterprise) has no scoped Management API key to generate — that's
  an Enterprise-only feature. `Bifrost Settings` now takes the dashboard
  admin's username/password and authenticates via HTTP Basic Auth,
  matching what a self-hosted instance actually supports.
- **Bifrost sync: concurrency race, timezone bug, and a NOT NULL crash.**
  A duplicate scheduled sync could race the same document into a
  `TimestampMismatchError`; outbound date filters sent to Bifrost were
  naive local time instead of UTC, silently widening the requested
  window; and log rows with empty token/cost sub-objects could crash the
  upsert. All three fixed and verified against a live, high-volume
  Bifrost instance.

## [1.0.6] - 2026-08-09

### Changed
- **Telegram removed from per-user Alert Subscription.** Individual
  subscriptions now offer only Email and WhatsApp — Telegram is a
  fleet-wide-only channel, configured exclusively in Proxmox Monitor
  Settings. Global Telegram alerting is unaffected.

## [1.0.5] - 2026-08-09

### Changed
- **Critical alerts now require confirmation too, not just Warning.**
  Previously, Critical fired on a single at/above-threshold reading while
  Warning required a sustained duration. Both tiers now use the same
  model: a reading must stay at/above its threshold, with no dip below
  it, for **Confirmation Checks** consecutive polls (new setting in
  Proxmox Monitor Settings, default 3) before that alert fires. This
  replaces the old time-based **Warning Duration (minutes)** setting —
  a single transient spike no longer triggers either tier. Applies
  identically to host CPU/RAM, guest CPU/RAM/disk, and datastore usage.

## [1.0.4] - 2026-08-09

### Fixed
- **Alert Subscription Server picker no longer offers PBS servers.** PBS
  hosts don't run VMs/CTs, so they had nothing to subscribe to — the
  dropdown is now filtered to PVE servers only, enforced both client-side
  and server-side.

## [1.0.3] - 2026-08-09

### Added
- **"Back to normal" recovery notifications.** When a Critical Resource
  (CPU/RAM/storage) or Server Offline condition clears, every enabled
  channel now gets a follow-up "resolved" message, in addition to the
  original alert. WhatsApp recovery messages use a separate, dedicated
  UTILITY template (**WhatsApp Recovery Template Name** in Proxmox
  Monitor Settings) so a resolved-condition message doesn't reuse the
  original alert's framing. Backup Failure is intentionally excluded —
  backups often only run every ~24h, so a "back to normal" notification
  would just be a stale, confusing non-sequitur.
- **Alert Subscription module.** Engineers can now self-subscribe to VM/CT
  alerts (*Desk → Alert Subscription*) — one document per user, holding
  their own Email/WhatsApp/Telegram contact info once, plus a table of
  specific VM/CT instances (server + guest) to watch. Subscriptions are
  additive to the existing global recipients in Proxmox Monitor Settings
  — nothing about the existing global alerting changes. Each engineer
  only sees/manages their own subscription document (Managers see all,
  for audit). Subscriptions are VM/CT-only — watching a server itself, or
  a datastore, stays purely the global recipients' job.
- **Subscriptions override the Production-only guest alerting rule.**
  Per-guest resource alerting (Critical Resource / Resource Warning) has
  always been scoped to Production servers, to avoid noise from Dev/
  Staging VMs that are expected to spike routinely. Severity is now
  tracked for *every* guest regardless of role (so Dev/Staging/Backup
  VM/CT cards correctly show real warning/critical icons and appear in
  the summary cards too), but the **global** Settings recipient list is
  still only notified for Production guests — an individual who
  specifically subscribes to a Dev/Staging instance gets notified about
  it regardless of role. Server-level, datastore-level, Backup Failure,
  and Server Offline alerting are unaffected (always global, as before).
- **"Send Test Alert" button on Alert Subscription** — lets a subscriber
  verify their own channel/contact setup without needing Manager access.
- **Dashboard: VM/CT cards lead with the active sort metric.** Sorting
  the Virtual Machines & Containers grid by CPU/RAM/Storage now also
  reorders each card's own meter rings so the sorted-by metric is always
  the first (leftmost) ring — RAM first when sorting by RAM, Disk first
  when sorting by Storage, and so on.
- **Dashboard: warning/critical meter icons.** A CPU/RAM/Storage ring at
  or above its warning/critical threshold now shows a warning triangle
  or critical octagon icon in place of the percentage, with a pulsing
  glow on critical readings to draw the eye.
- **Dashboard: two persistent summary cards** — "Critical Instances" and
  "Warning Instances" — shown under the tab bar on every tab, listing
  matching servers/guests by name with their CPU/RAM/Storage values.
- **Dashboard: VM/CT sort control** — sort the Virtual Machines &
  Containers grid by CPU, RAM, or Storage, ascending or descending
  (default: CPU descending).

### Changed
- VM/CT lazy-load page size reduced from 24 to 12 per load.

## [1.0.2] - 2026-08-08

### Changed
- WhatsApp alerts now send through a Meta-approved **UTILITY** category
  template instead of free text. Meta flags free-text business-initiated
  messages as non-compliant "marketing" traffic; templated sends are the
  compliant path for proactive infra alerts.
- The template name is configured via a new **WhatsApp Template Name**
  field on **Proxmox Monitor Settings** — never hardcoded. Accepts either
  the template's exact `WhatsApp Templates` document name or its plain
  Template Name.
- If no template is configured, WhatsApp alerts are treated as not fully
  configured and skipped (same behavior as when recipients are empty) —
  there is no free-text fallback.

### Added
- **Send Test Alert** button on the Proxmox Monitor Settings form — fires
  a real test message through every currently enabled and fully
  configured channel (Email/Telegram/WhatsApp) on demand, so setup can be
  verified without waiting for a real Warning/Critical condition.

## [1.0.1] - 2026-08-08

### Fixed
- WhatsApp alerts were silently reporting success without ever sending.
  The `WhatsApp Message` document created by this app was missing
  `type: "Outgoing"` and `content_type: "text"`, which `frappe_whatsapp`
  requires to actually place the API call — the insert succeeded either
  way, so failures (and the fact nothing sent) were invisible until now.

## [1.0.0] - 2026-08-06

### Added
- Initial public release. Real-time Proxmox VE/PBS fleet dashboard:
  auto-discovery of servers, VMs/CTs, storage, and backups; resource
  usage history; Warning/Critical threshold alerting over Email,
  Telegram, and WhatsApp; role-based access via `Proxmox Monitor Manager`
  and `Proxmox Monitor Viewer`.
