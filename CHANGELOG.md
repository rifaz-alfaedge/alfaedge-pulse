# Changelog

All notable changes to this project are documented here.

## [5.0.0] - 2026-10-24

### Removed
Unused features are gone. None of them had any data on production.
- **Alert Subscription**, with its Instances, Uptime Sites, Monitored
  Hosts and Log Sites tables and the subscription test button. Alerts,
  recovery messages, the Host Health digests and the 2-minute batch now go
  only to the global Email/WhatsApp recipients in Proxmox Monitor
  Settings. Non-Production alerts are still logged and shown on the
  dashboard, but nobody is sent them.
- **Telegram alerts** (Enable Telegram Alerts, Telegram Chat ID).
- **Embedded console:** the console relay, the Proxmox Console Session
  Log, the Console Username/Password fields on Proxmox Server, and the
  noVNC/xterm and `websockets` dependencies.
- **Client** doctype, and the Client field on Proxmox Server.
- **Unused VM/CT fields:** Network Mode, Public IP, Access URL and
  Assigned Engineer on Proxmox Guest. They're also gone from the VM/CT
  popup, the IP column and search.

A patch drops the removed tables and columns, the stored console
passwords and the Telegram settings.

### Added
- **Scheduled downtime for Uptime sites.** Give a site any mix of
  **daily**, **weekly** (chosen days, all day or a time range) and
  **one-time** windows, e.g. LuLu FinServ off nightly 21:00–07:00 and all
  of Sunday. Inside a window, checks are kept but don't count towards
  uptime, response time or alerts, the site shows **Maintenance**, and no
  Site Down alert is sent. When it ends, only fresh checks count, so a
  site still down alerts after the usual alert window. Managed from the
  site's side panel; stored in the new **Uptime Maintenance Window**
  doctype. Kuma is never touched.

### Changed
- **Host Health tab redesigned** in the same layout as Logs:
  - filters (role, status, Proxmox server, search) and six stat tiles;
  - a fleet load and new-failed-jobs chart beside a ranked **Needs
    attention** list;
  - Hosts, Services, Failed jobs and Disks & forecast views, each a
    sortable table;
  - a side panel for the selected host, with the existing full details
    (bench history, active jobs, failed-job root causes, log download)
    one click away.

  It's served by one new `get_host_overview` call instead of seven list
  queries.
- **Uptime tab redesigned** the same way:
  - filters (range, status, type, instance, search) and six stat tiles;
  - one table row per site, with each instance's status, uptime %,
    average response and an availability strip;
  - Incidents and Instances views;
  - a right-hand column with the selected site, **Needs attention** (down,
    down on one instance only, stale or failing instances, duplicate
    monitors, low uptime) and each Uptime Kuma instance's health.

  Sites can now be edited from the dashboard. Uptime is served by one new
  `get_site_overview` call.
- **Recurring-error alerts:** *Also Notify Global Recipients* in ClickHouse
  Log Settings is now **Notify Global Recipients**. It's the only way
  recurring errors are sent. When it's off (the default, and production's
  current setting), they're recorded and shown in the Logs tab only.

### Fixed
- **A disabled Uptime Kuma instance is no longer contacted.** Edit, Pause,
  Resume and Delete used to call every instance, even disabled ones.
  Now, on a disabled instance they change only Pulse's own Uptime Site,
  and the card says so. The check-interval sync skips disabled instances
  too. Their sites catch up when the instance is re-enabled. Disabled
  instances are marked on each site card.
- **Deleting an Uptime site works.** It used to fail on any site that had
  check history, and it did so only after the monitor had already been
  deleted on Kuma, leaving Pulse showing a site that no longer existed.
  Now the site's check history is deleted with it, its alert history is
  kept, and Kuma is called last. If Kuma fails, nothing is deleted in
  Pulse.
- **No false "Cannot read properties of undefined" error on the Uptime
  tab.** Pause, Resume and Delete worked but then showed this error and
  didn't refresh the card, and the Add Site and Import dialogs couldn't
  show their results. They now read the response correctly.

## [4.8.0] - 2026-10-23

### Added
- **Delete a disabled Proxmox Server, with an archive.** This is
  available from a **Delete Server…** button on the Proxmox Server form
  in Desk, or a managers-only **Disabled servers** panel at the bottom of
  Usage Metrics. It needs Proxmox Monitor Manager, works only on disabled
  servers, and you confirm by typing the server's name.
  - **Runs in the background.** A server can drag along tens of thousands
    of backup, alert and metric-history rows.
  - **Removes everything linked:**
    - the server's VMs/CTs and datastores;
    - backup and console-session logs;
    - discovered sites and its NPM;
    - every alert on any of these;
    - Host Health records for its VMs/CTs, with their services, jobs,
      mounts and history (their agent keys stop working);
    - the matching rows in Alert Subscriptions (the subscriptions
      themselves stay);
    - stored secrets.

    Recurring Errors are kept, just unlinked.
  - **Archived first:** a new **Proxmox Server Archive** record holds who,
    when, per-type counts, and a gzipped JSON with full copies of every
    deleted record. Metric history is kept as counts and date ranges only,
    and secrets are never included.
  - **Safe on failure:** if anything fails, the whole deletion is rolled
    back and the archive is marked Failed with the error.

## [4.7.2] - 2026-10-23

### Changed
- **Disabled Proxmox Servers are hidden from the dashboard.** A server
  disabled in Desk no longer appears in Usage Metrics, the status strip,
  the Critical/Offline counts, the Backups tab or the alerts popup, and
  neither do its VMs/CTs. Previously a disabled PBS kept showing as
  Offline.
- **Open alerts for a disabled server are closed automatically.** Its
  VMs/CTs and datastores are covered too. A disabled server is never
  polled again, so its last alert (e.g. "Server Offline") would otherwise
  stay open forever and keep counting under Open Alerts.

## [4.7.1] - 2026-10-23

### Fixed
- **Usage Metrics sorting used percentages, not actual usage.** A
  43%-full 8 GB disk (3.6 GB used) sorted above a 20%-full 100 GB disk
  (20 GB used). CPU, RAM, disk and swap now sort by the actual amount
  used (cores and GB); a host's Disks column sorts by total GB used
  across its drives; load sorts by the 1-minute load average. The
  warning/critical colours still follow the percentage thresholds.

## [4.7.0] - 2026-10-23

### Added
- **Site discovery: which site URLs live on which VM/CT.** A new
  `Site Discovery` module runs once a day (after Cloudflare Settings'
  sync hour) and on demand ("Sync now"). It merges three sources into
  one `Instance Site` row per domain:
  - **Nginx Proxy Manager:** each host's NPM proxy hosts. A domain maps
    to a VM/CT through its forward IP (preferring guests on the NPM's own
    host), then by name.
  - **Direct DNS:** Cloudflare A/AAAA records pointing straight at a
    guest's own IP.
  - **Pulse agent:** sites reported by the Host Health agent. These are
    merged into existing rows as "also seen in".

  A domain in more than one NPM is assigned to the NPM whose public IP
  its Cloudflare record points to (CNAME chains and wildcards are
  followed). Problems are flagged rather than guessed: **Ambiguous**,
  **DNS Mismatch**, **Not in Cloudflare** or **Unmatched**.

  Outages never wipe data. Sites owned by an NPM that failed to sync keep
  their previous owner, and while Cloudflare is down nothing is deleted
  or overwritten.
- **NPM Instance and Cloudflare Settings:** the NPM admin email/password
  and Cloudflare API token are encrypted Password fields. An NPM with no
  URL of its own defaults to `http://<Proxmox host>:81`.
  - Managed from Usage Metrics → **Site sources** (Proxmox Monitor
    Manager only): add, edit, delete and test NPMs, test Cloudflare,
    Sync now, and a "Needs attention" list.
  - Also managed from Desk. Secrets are write-only and never sent to the
    browser.

### Changed
- **Usage Metrics:**
  - The Uptime column is removed from both tables.
  - The VM/CT table has a **Sites** count column, and its search also
    matches site URLs.
- **Detail popup:** shows only what the table doesn't: datacenter, root
  filesystem, backup status and retention for hosts; network mode, access
  URL, engineer and last sync for guests. It also lists the instance's
  **Sites** (or, for a host, the sites served through its NPM), each with
  its source, NPM target, Cloudflare target and any problem.
- The stale-job guard from 4.6.0 is now a shared helper
  (`tasks/jobs.py`), used by both the poller and the site sync.

## [4.6.0] - 2026-10-22

### Fixed
- **The Proxmox poller could stop permanently after a worker restart.**
  When a worker was killed mid-run (for example by
  `supervisorctl restart` during a deploy), RQ kept the poll job marked
  "started". Frappe's `deduplicate=True` then refused every re-enqueue
  from the 1-minute watchdog, so polling never resumed. Dashboard data,
  Server Offline, Critical Resource and backup checks all froze with
  nothing raised; this happened in production from 16 Oct.
  `ensure_poller_running` now deletes a queued/started poll job older
  than the bounded run time (`MAX_LOOP_SECONDS` + 5 min), logs it as
  "cleared stale poller job", and restarts the loop.

### Added
- **Poller Stalled alert.** The 1-minute watchdog alerts globally when no
  enabled Proxmox Server has synced for 5 minutes (or 5 poll intervals),
  and sends a recovery once syncing resumes.
- **Per-guest Backup Overdue alert.** Previously only the host as a whole
  was checked, so one guest backing up kept every guest green. Now each
  running VM/CT with no successful backup within **Backup Overdue After
  (hours)** (new Proxmox Monitor Settings field, default 26; 0 turns it
  off) raises Backup Overdue, with a recovery once it backs up again.
  - Exempt: stopped guests, guests tagged `no-backup` in Proxmox, and
    guests created within the window.
  - Batched through the 2-minute alert batch, so a host-wide failure
    arrives as one message.
  - The global recipients only get Production hosts' guests;
    per-instance Alert Subscriptions get theirs regardless.
  - The Usage Metrics "Last backup" colour uses the same window.
- **Backup Failure recovery message** when a host's backups succeed
  again (previously the alert was only closed silently).

### Changed
- vzdump task window raised from 50 to 200 per cycle, so frequent
  per-guest jobs don't push results out between cycles. Tasks already
  recorded are still skipped by UPID.
- A PBS snapshot is linked to a guest only when its VMID is unique across
  the fleet; otherwise it's recorded unlinked rather than guessed.

## [4.5.0] - 2026-10-22

### Changed
- **Usage Metrics is now two plain tables instead of cards.** The meter
  rings, icons, card grid and Uptime summary tile are gone.
  - **Hosts and Backup Server:** status, CPU (used/total cores), RAM,
    swap, each drive (used/total), load average (1·5·15), uptime and
    last sync.
  - **VMs and Containers:** host, status, CPU (used/allocated vCPU),
    RAM and disk (used/allocated), load average, IP, uptime, last
    backup and Host Health status.
  - Values past the warning/critical thresholds are coloured, and a
    critical or warning row gets a coloured left edge.
  - **Sorting:** every column sorts in either direction, with missing
    values kept last, and the choice is remembered per browser.
  - **Filtering:** each table has its own filters. Hosts filter by
    role, status and severity. Guests filter by search (name, VMID, IP,
    tag), host, status, VM/CT, Host Health and severity. A
    "Showing X of Y" count and Clear filters appear while filtering.
  - The header's Servers filter and the status strip's Critical/Offline
    pills feed the same filters.
  - Clicking a row still opens the detail dialog.
- **Console links removed** from the VM/CT detail dialog and the Host
  Health detail dialog, ahead of a different console approach.
  `lib/consoleUrl.ts` was deleted; the dormant `console_relay/` backend
  is untouched.

### Added
- **Host load average:** `Proxmox Server.load_avg_1min/5min/15min`,
  read from the node status the poller already fetches, so there are no
  new API calls.
- **`host_health.api.get_guest_load_averages`:** the latest
  resource-agent load average per guest. Only guests running the Pulse
  resource agent report load; Proxmox doesn't expose guest load. A
  reading older than 15 minutes shows greyed, with its age.

## [4.4.0] - 2026-10-21

### Added
- **Resolve recurring errors.** Each error on Logs → Recurring Errors
  (and on any page using the `resolve_error` API) has a **Resolve**
  button. Resolving stamps the error's `Recurring Error` record with
  `resolved_at` / `resolved_by`, and from then on its earlier occurrences
  no longer count:
  - The error disappears from the recurring-errors list (and from
    per-site totals built on it).
  - It reappears by itself if it happens again, flagged **Reopened**
    and counting only the occurrences since the resolution.
  - The recurring-error alert check ignores the resolved occurrences too.
  - Any open alert for the error is closed, with a "Marked resolved by
    …" message to its subscribers.

  Proxmox Monitor Manager and Proxmox Monitor Viewer can resolve.
  `get_recurring_errors` hides resolved occurrences by default; pass
  `hide_resolved=0` to see everything. All Logs is unaffected.

## [4.3.1] - 2026-10-16

### Fixed
- **Log Monitor connection failed when a credential was pasted with
  stray whitespace.** A username copied with a leading tab
  (`'\tpulse_reader'`) made every request fail with "Invalid leading
  whitespace ... in header value", since credentials are sent as HTTP
  headers. Host, path, username, password and API key are now trimmed on
  save and again when building each request, so values that are already
  saved work too.

## [4.3.0] - 2026-10-16

### Added
- **Log Monitor (ClickHouse).** A new **Logs** tab for the Frappe Error
  Logs that every production site's server script ships into ClickHouse.
  The logs are queried live over ClickHouse's HTTP interface; none are
  copied into MariaDB.
  - Filter by **log type**, **site URL**, **date range**, and free-text
    search, with summary tiles and an Errors-vs-General trend chart. A row
    is **Error** when its content contains the configured keyword (default
    `error`, case-insensitive), and **General** otherwise. The log table
    is paginated, and clicking a row shows its full traceback with a copy
    button.
  - **Recurring Errors** groups the same error on the same site, with
    occurrence counts and first/last seen times. Volatile tokens (numbers,
    hashes, docnames, quoted values) are normalized first, so
    "SINV-00123 not found" and "SINV-00456 not found" count as one error.
    Clicking a group drills down to its individual occurrences.
  - **Recurring-error alerts.** When the same error hits a site at least
    *N* times within *M* minutes (default 5 in 60), a `Recurring Error`
    alert goes out through the existing Email/WhatsApp channels, batched
    with the 2-minute resource-alert batch. A recovery message follows once
    the error drops back under the threshold. Each recurring error is
    linked to the Monitored Host that reports its site, so an Alert
    Subscription on that host also receives the host's site errors. Local
    alert state lives in the new `Recurring Error` doctype.
  - **Recurring-error alerts go to engineers individually by default.**
    Alert Subscription has a new **Recurring Errors (Logs)** section: tick
    **All Sites**, or list specific sites, picked from the sites seen on
    the Logs tab. Each engineer only receives errors from their own sites.
    The global recipients in Proxmox Monitor Settings get recurring errors
    only when **Also Notify Global Recipients** is ticked in ClickHouse Log
    Settings (off by default).
  - **Sites are identified by host.** The same host under `http://` and
    `https://` (or with a different port or trailing slash) counts as one
    site, e.g. `anson.alfaedge.org`. A site logged under a bare name such
    as `https://lulu` can be merged into its real site, or given any
    display name, from the Logs → **Sites** dialog or the new **Log Site
    Alias** doctype. Aliases are applied inside every ClickHouse query, so
    they also regroup logs recorded before the alias existed.
  - **`ClickHouse Log Settings`** can be edited both in Desk and in the
    dashboard's Logs → Settings dialog. Every setting is configurable:
    protocol, host, port, URL path, timeout, database, table, the column
    mapping, the error keyword, and the alert thresholds. The username,
    password, and optional reverse-proxy API key are encrypted Password
    fields. They are never sent back to the browser, and the dialog treats
    them as write-only.
  - **Access.** Only **Proxmox Monitor Manager** can open Settings or
    Sites, test the connection, or change aliases, in the dashboard and in
    Desk. Every other dashboard role (including System Manager without
    that role) can view and filter the logs and recurring errors, and
    isn't shown where ClickHouse is or how it's configured.
  - Security: every filter value is passed as a ClickHouse query parameter
    rather than interpolated into the SQL. Identifiers are validated, and
    requests run with `readonly=1`. A ClickHouse user whose profile is
    already `readonly=1` rejects per-request settings, so the client
    detects that and retries without them.

### Fixed
- An Alert Subscription whose email or WhatsApp number was also on the
  global recipient list never received batched alerts that the global
  list itself wasn't sent, such as load/swap alerts on non-Production
  hosts. It now receives them.

### Changed
- The 2-minute batched alert email is now titled "Alert Summary" rather
  than "Resource Alert Summary", since it also carries Recurring Error
  alerts.

## [4.1.0] - 2026-09-05

### Changed
- **Backup Failure alerts now judged over a rolling 24-hour window,
  not per task.** With backup jobs running every 30-60 minutes, flagging
  critical on any single failed vzdump task (one guest locked, one
  transient error) fired constantly even though the fleet was still
  being backed up fine overall. `_handle_backup_task_result` now checks
  whether *any* task has succeeded anywhere on the host within the last
  24 hours; only a genuine day-long gap with zero successes marks the
  server critical and sends one alert, which clears the moment a new
  success is recorded. Every task's own outcome is still recorded in
  full on the Backups tab regardless — this only changes what triggers
  an alert. Added a search index on `Proxmox Backup Log`'s `server`,
  `status`, and `backup_time` fields, since this check now runs on every
  poll cycle for every server.

## [4.0.0] - 2026-09-02

### Changed
- **App renamed `proxmox_monitor` → `alfaedge_pulse`.** The package,
  every internal import, the ingest endpoint's dotted path, the frontend
  asset base path, and the console-relay process command all now use the
  real product name instead of the old working name — brings the
  underlying package in line with the product name, GitHub repo, and web
  route, all of which have said "alfaEdge Pulse" for a long time. Breaking
  for anything referencing the old import path directly; `pulse_agent`
  v0.3.0 carries the matching fix for its own ingest URL.

### Added
- **Resource & Capacity Monitoring.** Tracks what the hypervisor-side
  Proxmox Guest/Server data can't see from inside the guest OS: load
  average, swap usage, and per-mount disk/inode usage — on its own
  ~3-minute cadence via a new standalone `resource_agent.py` collector,
  independent of Host Health's 25s heartbeat. New `Client`,
  `Resource Monitor Settings`, `Resource Mount`, `Resource Metric Log`,
  and `Resource Disk Sample` doctypes; a new `push_resource_metrics`
  ingest endpoint reusing the existing confirmation-streak severity/
  alerting machinery verbatim (`High Load Average`, `High Swap Usage`,
  and disk/inode fullness reusing the existing `Critical Resource`/
  `Resource Warning` types); a capacity forecast (`get_disk_forecasts`)
  that fits a trend line over recent samples to estimate days-until-full;
  a daily retention purge for both new history tables.
- **Host Health alerts batched into three daily digests.** Service Down,
  Worker Degraded, Failed Job Threshold, Long Running Job, Scheduler
  Stalled, and Host Unreachable no longer send immediately — they're not
  urgent enough to interrupt for — and are instead summarized in three
  scheduled digests (07:00 previous-day recap, 13:00 midday, 20:00
  evening) via a new `alerts/digest.py`. Every other alert type (Uptime,
  resource usage, backups, server-offline) is unaffected and still sends
  immediately. Digest recipients are scoped correctly: the global
  recipient list gets the full fleet-wide picture, but a per-guest Alert
  Subscription only ever receives the hosts they actually subscribed to
  — never folded in with every other host's issues from the same window.
  Delivered by email (full itemized detail) and WhatsApp (a short
  count-only summary via a new `whatsapp_health_digest_template` Meta
  UTILITY template).

### Fixed
- **`Frappe Active Job`/`Frappe Failed Job Log`'s `rq_job_id`** was a
  plain `Data` field (140-char default) — some real scheduled-job dotted
  paths push RQ's own job-id format past that limit, silently failing to
  record that job every cycle it ran. Widened to 255 characters.
- **`push_resource_metrics` could wipe a host's entire disk-status history**
  if a single push reported an empty `disks` list (e.g. a transient
  `/proc/mounts` read failure on the agent) — the stale-mount cleanup's
  "not in" filter degenerated to matching every real mount. Now only
  prunes stale mounts when at least one real mount was actually seen that
  push.
- **`get_disk_forecasts`** issued one query per mount instead of one
  batched query across all of them — fixed for endpoints scaling with
  fleet size rather than mount count.
- A malformed disk-usage payload with `inodes_total` as a non-empty falsy
  string (e.g. `"0"`) could raise `ZeroDivisionError` instead of being
  handled like every other defensively-guarded numeric field.

## [3.5.1] - 2026-08-16

### Added
- **"Download Log" now includes Active Jobs.** The downloadable host
  health log (Host Health detail dialog) was scoped to failed jobs only —
  a debugging snapshot missing what's running *right now* is an
  incomplete one, so it now leads with an ACTIVE JOBS section (job name,
  bench, queue, worker PID, elapsed time, `[LONG RUNNING]` flag) before
  the failed-job summary and tracebacks. Renamed header to "Host Health
  Log" to match the broader scope; same endpoint/button, no frontend
  changes needed.

## [3.5.0] - 2026-08-16

### Added
- **Per-guest Console link.** VM/CT detail dialogs (Usage Metrics tab and
  now Host Health's own detail dialog, next to "Download Log") get an
  "Open Console" action that deep-links to that guest's console on its own
  Proxmox host's web UI in a new tab — never for host/PBS servers
  themselves, only guests. An earlier embedded-in-dashboard attempt
  (`console_relay/`, `ConsoleDialog.tsx`, `Proxmox Console Session Log`) is
  kept in the codebase but currently unused: Proxmox's `vncwebsocket`
  endpoint consistently rejected a standalone relay's `PVEAuthCookie`
  handshake with "invalid PVEVNC ticket" despite matching every documented
  requirement — a long-standing, still-unresolved issue in Proxmox's own
  community. The new-tab deep-link sidesteps it entirely by letting
  Proxmox's own JS handle its own console protocol. Adds `Proxmox
  Guest.node` (persisted by the poller) and `Proxmox Server` Console
  Username/Password fields (a real PVE login, separate from the read-only
  monitoring token).
- **Frappe worker/job "eagle-eye view."** Host Health now tracks every
  currently-executing RQ job per bench, not just aggregate worker/queue
  counts — surfaced in a new "Active Jobs" section (job name, queue,
  worker PID, elapsed time) in the host detail dialog, with a per-card
  badge and card-level critical styling once any job has been running
  past a configurable threshold. A new `Long Running Job` alert fires the
  same way `Worker Degraded`/`Failed Job Threshold` already do (streak +
  confirmation checks). New `Frappe Active Job` doctype — a live snapshot,
  not a history log, so a row disappears the moment its job finishes
  rather than being tombstoned. Requires the updated `pulse_agent` (this
  release, `agent_collect.collect_bench_health`) on each guest to actually
  report active jobs — older agents keep working, they just don't
  populate this data.
- **Bench Health trend chart date range.** The chart's history now comes
  from a query scoped to exactly the selected (host, bench) within a
  chosen window (1h/6h/24h/7d buttons) instead of a fleet-wide "most
  recent 300 rows shared across every host and bench," which could shrink
  to almost nothing per host as more of the fleet becomes active.

### Fixed
- **Bench Health stats flicker.** The new scoped date-range query's
  "since" timestamp was computed fresh from `Date.now()` on every render,
  changing the underlying SWR cache key on every re-render (not just every
  poll tick) and resetting the whole stats row to a loading state —
  visible as the numbers repeatedly blanking out and reappearing every few
  seconds. Fixed by bucketing/memoizing that timestamp to the minute.

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
