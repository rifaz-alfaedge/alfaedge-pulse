# Changelog

All notable changes to this project are documented here.

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
