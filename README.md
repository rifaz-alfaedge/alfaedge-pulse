# alfaEdge Pulse

**Version 2.0.0** — see [CHANGELOG.md](CHANGELOG.md) for release history.

A standalone Frappe 16 app (no ERPNext dependency) that gives you a single,
real-time dashboard for a Proxmox fleet — PVE hosts, every VM/CT running on
them, and a Proxmox Backup Server instance. Connect a host once and its
VMs/CTs are discovered and kept in sync automatically; nothing is added by
hand. As of v2.0, the same dashboard also tracks LLM usage/cost through a
self-hosted [Bifrost](https://getbifrost.ai) gateway (see
[AI Usage](#ai-usage-bifrost)) and uptime across any number of
[Uptime Kuma](https://github.com/louislam/uptime-kuma) instances (see
[Uptime Monitoring](#uptime-monitoring-uptime-kuma)).

## Features

- **Auto-discovery.** Add a `Proxmox Server` (a PVE host or a PBS instance)
  with a read-only API token and everything else — every VM/CT, every
  storage pool/datastore, every backup — is discovered and kept in sync on
  its own. Nothing is entered by hand, and anything removed from Proxmox
  disappears from the dashboard on the next cycle.
- **Two-phase resource alerting.** A reading at or above the **Warning**
  threshold (default 85%) or the **Critical** threshold (default 95%) only
  fires once it's stayed there continuously — no dip below it, even
  briefly — for the configured **Confirmation Checks** (default 3
  consecutive polls), so a single transient spike never triggers an alert.
  See [How it works](#how-it-works) for exactly how the two tiers interact.
- **Role-aware noise control.** Per-guest resource alerts (Warning and
  Critical) only apply to servers whose `role` is **Production** — a
  Development/Staging/Backup host's individual VMs/CTs are expected to
  spike routinely and won't page anyone for it. The host itself, its
  storage pools, and its backups are still alerted on regardless of role.
- **Backup tracking that matches how Proxmox actually reports it.** Both
  local vzdump backups and PBS-remote backups are tracked per guest for
  history (grouped and sortable in the Backups tab, one table per
  server/PBS destination), but *alerting* judges the underlying backup
  **task** as a whole — a bulk nightly job covering the whole fleet is one
  pass/fail outcome, not one alert per affected guest, and a host that has
  never run a single backup task is flagged just as critically as one
  whose latest task failed.
- **Multi-channel alerting without owning any credentials.** Email uses
  whatever Email Account is already configured in core Frappe. Telegram
  and WhatsApp defer entirely to separate, already-installed integration
  apps — this app never stores a bot token or a Cloud API credential
  itself; see [Alerting](#alerting). "Back to normal" recovery messages
  fire when a critical condition clears, and individual engineers can
  self-subscribe to specific VM/CT instances on top of the global
  recipients — see [Alert Subscriptions](#alert-subscriptions).
- **A live, glanceable dashboard.** CPU/RAM/Swap/storage rings with
  at-a-glance icons, a heartbeat indicator tied to how recently each node
  actually synced, click-through detail views, and dashboard ordering
  that groups every server (and its guests) by role — Production first,
  then Staging, then Development, then PBS — so what matters most is
  always at the top. A ring past its warning/critical threshold swaps its
  percentage for a warning/critical icon (with a pulsing glow on
  critical), two persistent summary cards list every critical/warning
  instance by name, and VM/CT cards can be sorted by CPU, RAM, or Storage —
  each card's own meter rings reorder to lead with whichever metric you're
  currently sorting by.
- **AI Usage analytics for a self-hosted Bifrost gateway.** Cost, tokens,
  provider/model breakdowns, and historical trend charts, synced from
  Bifrost's own request logs into this app's permanent storage — see
  [AI Usage](#ai-usage-bifrost).
- **Uptime monitoring across any number of Uptime Kuma instances.** Add,
  pause, resume, and delete monitored sites from this dashboard; alerts
  fire through this app's own Email/WhatsApp mechanism, not Kuma's — see
  [Uptime Monitoring](#uptime-monitoring-uptime-kuma).

## Requirements

- Frappe Framework **16.x** (no ERPNext or other app dependency)
- Python **3.14+**
- Node.js (for building the frontend — any recent LTS)
- Read-only API tokens for each Proxmox VE host and/or Proxmox Backup
  Server instance you want to monitor (see
  [Setting up a Proxmox Server connection](#setting-up-a-proxmox-server-connection))

## Installation

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch version-16
bench --site your-site install-app proxmox_monitor
```

The dashboard's frontend is a separate React build and isn't committed to
this repo (see `.gitignore`) — build it once after installing:

```bash
cd apps/proxmox_monitor/frontend
npm install
npm run build
bench build --app proxmox_monitor
```

Then visit `https://your-site/alfaedge-pulse` (an authenticated Frappe
session is required — Guests are redirected to `/login`).

`npm install`/`npm run build` need write access to `frontend/node_modules`
and to `proxmox_monitor/public/alfaedge-pulse` — run them as the bench's
own user (usually `frappe`), not root or an unrelated account, or the
build will silently fail to produce output and the dashboard route will
500 with a `manifest.json` not found error.

### Permissions

The **Administrator** account has full access by default, but any other
Frappe user needs one of this app's two roles explicitly assigned
(*User → Roles*) before they can see or do anything here — with neither
role, every Proxmox doctype is invisible to them:

- **Proxmox Monitor Manager** — full read/write, including **Proxmox
  Monitor Settings**. Needed to add/edit `Proxmox Server` connections or
  change thresholds.
- **Proxmox Monitor Viewer** — read-only across guests, datastores,
  servers, and logs; can view the dashboard but not configure it (no
  access to Settings at all).

## Deploying backend or frontend changes

After editing any Python file, `bench build` alone is **not** enough —
Python doesn't reload changed modules in an already-running process, so
both the gunicorn web workers and the background job workers keep
executing the old code in memory until restarted:

```bash
bench build --app proxmox_monitor   # relinks/rebuilds static assets
supervisorctl restart frappe-bench-web:frappe-bench-frappe-web
supervisorctl restart frappe-bench-workers:
```

Forgetting the `frappe-bench-web` restart specifically means any
web-triggered path (e.g. the **Test Connection & Sync Now** button, or
the `/alfaedge-pulse` page itself) keeps serving the previous version even
though the file on disk is already updated — the CLI (`bench execute`,
`bench console`) runs in a fresh process each time, so testing a fix that
way can look correct while the live site is still stale.

Frontend (`frontend/`) changes need `npm run build` before the
`bench build` step above, to regenerate `public/alfaedge-pulse`. Built
filenames are content-hashed and looked up per-request from
`public/alfaedge-pulse/.vite/manifest.json` (see `www/alfaedge-pulse/index.py`) —
deliberately, since nginx caches everything under `/assets` for a full
year with no revalidation (`config/nginx.conf`); a fixed filename would
mean a deploy silently keeps serving stale JS/CSS to anyone who already
loaded the page.

**If a checkout misbehaves in a way that doesn't match this repo** (a
dependency resolving to a version that shouldn't be reachable, a build
error nobody else can reproduce, etc.), check for local drift *before*
assuming it's a code or environment bug:

```bash
git status --short
git diff HEAD -- frontend/package.json frontend/package-lock.json
```

An uncommitted local edit to either file (e.g. from someone running
`npm install <package>@<version>` directly on that server at some point)
silently overrides what's actually committed — `git show HEAD:...` will
still show the correct, intended version even while the real working
files on disk have drifted, which is what actually happened once during
this app's development: a locally-downgraded `frappe-react-sdk` produced
a runtime `ReactCurrentOwner` crash that looked like an environment or
npm-version issue until `git status` revealed the real cause. Restore
with `git checkout -- <file>` and reinstall.

## How it works

- A background loop (`proxmox_monitor.tasks.poller.run_poll_loop`) polls
  every enabled `Proxmox Server` roughly every 20 seconds (configurable in
  **Proxmox Monitor Settings**), using the Proxmox VE / PBS REST APIs
  directly — no extra services (no InfluxDB, no Grafana, no `proxmoxer`)
  required. Each invocation runs for a few minutes at a time and then
  hands off to the next one (a 1-minute watchdog cron restarts it) rather
  than looping forever in a single job — this keeps `bench restart` /
  deploys fast instead of waiting on a job that can never finish on its
  own.
- One server failing to respond (e.g. Beta being down) never blocks
  the sync of the others — each host is synced and committed independently.
- `Proxmox Guest`, `Proxmox Datastore`, and `Proxmox Backup Log` records are
  fully auto-managed: created, updated, and removed by the poller. Don't
  create or delete them by hand — your edits will be overwritten on the next
  cycle. The exceptions are a guest's `assigned_engineer`, `network_mode`,
  `public_ip`, and `access_url` fields, which are yours to set.
- A guest's `is_critical`/`is_warning` are CPU/RAM/disk only, and are
  tracked for every guest regardless of its server's `role` — see
  `_upsert_guest`. What's role-gated is only whether the **global**
  Proxmox Monitor Settings recipient list gets notified (Production
  only); an individual Alert Subscription still gets notified for a
  Dev/Staging/any-role instance it specifically watches — see
  [Alert Subscriptions](#alert-subscriptions).
- Neither tier is immediate. `_track_severity` only sets `is_warning`/
  `is_critical` once a reading has stayed at or above the relevant
  threshold continuously — tracked via each doctype's `warning_streak`/
  `critical_streak` fields, reset to 0 the instant a reading dips below
  the line — for the full `confirmation_checks` consecutive polls.
  Escalating from Warning to Critical resolves the open Warning alert
  (Critical supersedes it); dropping back from Critical into the warning
  band re-opens Warning immediately rather than restarting the count,
  since the reading's already been at-or-above the warning line the
  whole time.
- A server's `is_critical` combines two independent things, for every
  role: its own CPU/RAM (`_apply_host_status`), and whether its most
  recently processed backup task failed (`backup_critical`, set in
  `_handle_backup_task_result`). Either one alone is enough to flag it,
  and fixing one never silently clears the other's flag or alert. Backup
  task health is judged per task, not per guest — a bulk job covering the
  whole fleet is one pass/fail outcome, not one per VM/CT.

## Setting up a Proxmox Server connection

Each host needs a **read-only API token** — never use a full admin
account/password for this integration.

**Proxmox VE (Alpha, Beta, Gamma):**

1. In the Proxmox web UI: *Datacenter → Permissions → Roles* — confirm the
   built-in `PVEAuditor` role exists (it ships by default).
2. *Datacenter → Users* — create a dedicated user, e.g. `monitor@pve`, or
   reuse an existing service account.
3. *Datacenter → Permissions → Add* — grant that user the `PVEAuditor` role
   at path `/` (so it can read every node/VM/CT), **without** the
   "Privilege Separation" restriction you'd want for a token that can make
   changes — this token should only ever read.
4. *Datacenter → API Tokens → Add* — Token ID e.g. `dashboard`, uncheck
   "Privilege Separation" only if you want the token to inherit the user's
   read-only role directly; otherwise grant the same `PVEAuditor` role to
   the token itself under *Permissions*. Copy the generated **Token ID**
   (`monitor@pve!dashboard`) and **Secret** — the secret is shown once.
5. In Frappe, create a new **Proxmox Server**: `server_type = PVE`,
   `hostname` = e.g. `alpha.example.com`, `port = 8006`,
   `api_token_id = monitor@pve!dashboard`, `api_token_secret` = the secret
   from step 4, `verify_ssl` unchecked (self-signed cert, the norm for a
   private host). Save, then click **Test Connection & Sync Now**.

**Proxmox Backup Server (backups.example.com):**

Same idea, using PBS's built-in `Audit` role instead of `PVEAuditor`:
*Configuration → Access Control → Add API Token*, grant it the `Audit`
role on `/` via an ACL entry, then create a **Proxmox Server** record with
`server_type = PBS` and `port = 8007`.

## Configuration

Everything below lives in the single **Proxmox Monitor Settings** doctype
(*Desk → Proxmox Monitor Settings*):

| Field | Default | Meaning |
| --- | --- | --- |
| Poll Interval (seconds) | 20 | How often the background loop refreshes every connected server. |
| Warning Threshold (%) | 85 | See [How it works](#how-it-works) — the lower of the two tiers. |
| Critical Threshold (%) | 95 | The higher, more urgent tier. |
| Confirmation Checks | 3 | How many consecutive polls a reading must stay at/above the Warning/Critical Threshold, with no dip below it, before that alert fires. Applies to both tiers. |
| Enable Email/Telegram/WhatsApp Alerts + recipients | off | See [Alerting](#alerting) below. |

## Alerting

Configured in **Proxmox Monitor Settings**. This app never stores
third-party notification credentials itself:

- **Email** — just check "Enable Email Alerts" and add recipients; it uses
  whichever Email Account is already configured in core Frappe
  (*Settings → Email Account*).
- **Telegram** — install and configure a Telegram bot app (e.g.
  [`leam-tech/frappe_telegram`](https://github.com/leam-tech/frappe_telegram))
  separately, with its own bot token. Then enable Telegram alerts here and
  enter the chat/group ID you want notified.
- **WhatsApp** — install and configure
  [`frappe_whatsapp`](https://github.com/shridarpatil/frappe_whatsapp)
  separately, with its own WhatsApp Cloud API credentials. Then enable
  WhatsApp alerts here, enter recipient numbers, and set **WhatsApp
  Template Name** to a Meta-approved **UTILITY**-category template (in
  `frappe_whatsapp`'s **WhatsApp Templates** list, synced via its "Sync
  from Meta" button) with exactly one body variable — that variable
  receives the alert text. You can enter either the template's document
  name (e.g. `alert_notification-en_US`) or just its Template Name (e.g.
  `alert_notification`). WhatsApp Business API requires business-initiated
  messages outside an open customer conversation to use a pre-approved
  template; free-text sends get flagged as non-compliant marketing
  traffic by Meta. Without a template configured, WhatsApp alerts are
  treated as not fully configured and skipped, same as with no recipients.

If a channel's app isn't installed, or isn't fully configured, alerts for
that channel are silently skipped (and still logged to **Proxmox Alert
Log**) — the other channels are unaffected.

Use the **Send Test Alert** button on the Proxmox Monitor Settings form to
send a one-off test message through every currently enabled and fully
configured channel, without waiting for a real Warning/Critical
condition — useful for verifying SMTP/Telegram/WhatsApp setup end-to-end.

### Recovery ("back to normal") notifications

When a Critical Resource (CPU/RAM/storage above the critical threshold) or
Server Offline condition clears, every enabled channel also gets a
follow-up "resolved" message. WhatsApp recovery messages use a second,
separate template — set **WhatsApp Recovery Template Name** in Proxmox
Monitor Settings to a Meta-approved UTILITY template distinct from the
main alert template, so a resolved message doesn't reuse the original
alert's framing. Without it set, WhatsApp recovery sends are skipped
(Email/Telegram recovery still send). Backup Failure does **not** get a
recovery notification — backups often only run on a ~24h cadence, so
"back to normal" would just be a stale, confusing non-sequitur.

### Alert Subscriptions

Beyond the global recipients above, individual engineers can self-subscribe
to specific VM/CT alerts via *Desk → Alert Subscription* — one document per
user. Set your own Email/WhatsApp contact info once at the top (Telegram is
fleet-wide-only — see above — and isn't offered here), then add rows to
the **Servers / Instances** table below: pick a Proxmox
Server, then pick one of its VMs/CTs. Add as many rows as you like to
watch multiple instances. Subscriptions are **additive** — they don't
replace or affect the global recipients above. Each engineer only sees
and manages their own subscription document (`Proxmox Monitor Manager`/
System Manager can see everyone's, for audit). Use the **Send Test Alert**
button on a saved subscription to verify your own channel/contact setup.
Subscriptions are VM/CT-only — watching a server itself, or a datastore,
is handled purely by the global recipients above, not per-user
subscriptions. Only PVE servers are offered in the Server picker — PBS
hosts don't run VMs/CTs, so they have nothing to subscribe to.

**Subscriptions reach Dev/Staging/Backup instances too.** Per-guest
resource alerting (Critical Resource/Resource Warning) is normally scoped
to Production servers — a Development/Staging VM is expected to spike
routinely and the global recipient list is never notified about it. But if
you specifically subscribe to a Dev/Staging VM/CT, you'll be notified about
it regardless of its server's role — subscribing is a deliberate, per-
instance opt-in. This also means every guest's warning/critical status is
now tracked and shown on the dashboard honestly, whatever its server's
role, even though the global list still only hears about Production ones.

## AI Usage (Bifrost)

In-depth cost/token/provider analytics for a self-hosted
[Bifrost](https://getbifrost.ai) LLM gateway, on the **AI Usage** tab.

**Setup:**
1. Generate a **Management API token** in Bifrost's own UI (Settings →
   API Keys) — this is separate from any provider/virtual key, and never
   passes through this app to any provider.
2. Open *Desk → Bifrost Settings*, set **Base URL** (e.g.
   `https://llm.alfaedge.in`) and the token, and save.
3. Click **Sync Now** for an immediate first pull, or wait for the
   background job (every **Sync Interval (minutes)**, default 15, live-
   editable with no restart) to pick it up on its own.

**How it works:** a background job pulls `GET /api/logs` from Bifrost's
Management API — paginated, filtered by time range, sorted ascending —
and upserts each row into a new `LLM Usage Log` doctype, keyed by
Bifrost's own log ID so re-processing the same row (a 5-minute overlap
window is re-pulled every cycle, deliberately) never creates a
duplicate. This is why the dashboard keeps working even if Bifrost's own
log retention doesn't go back as far as you want to look: alfaEdge Pulse
becomes the permanent record. A request still `processing` at sync time
is automatically re-checked later once it completes, so its final
cost/token numbers land without needing a manual re-sync. **Initial
Backfill (days)** (default 30) controls how far back the very first sync
reaches.

The `LLM Usage Log` schema includes a `Source` field (currently always
`Bifrost`) so a future direct-provider tracker (calling OpenAI/Google/
etc.'s own billing APIs directly, bypassing Bifrost) could feed the same
table and dashboard without a rework — not built yet, just left room for.

## Uptime Monitoring (Uptime Kuma)

Add, monitor, and manage sites across any number of self-hosted
[Uptime Kuma](https://github.com/louislam/uptime-kuma) instances from the
**Uptime** tab — alerting through this app's own Email/WhatsApp mechanism,
with Kuma's own alerting left off entirely.

**Setup:**
1. In Kuma, note (or create) a login with permission to manage monitors.
   If that instance already has an **API Key** configured, note it too —
   Kuma permanently disables Basic Auth on `/metrics` once any API Key
   exists on an instance.
2. In Desk, create an **Uptime Kuma Instance** record per Kuma deployment:
   Base URL, Username/Password (and API Key, only if the step above
   applies to that instance).
3. From the dashboard's **Uptime** tab, click **+ Add Site**, pick the
   instance, and fill in the monitor details (HTTP(s)/TCP/Ping supported).

**How it works:** two separate paths, deliberately kept apart —

- **Adding/editing/deleting/pausing/resuming a monitor** uses Kuma's
  internal Socket.IO API. Kuma's own docs call this API unofficial
  ("not supported for third-party integrations... may break without
  notice") — accepted deliberately, and scoped to just this
  admin-initiated convenience feature.
- **Status polling** — the one thing alerting depends on — instead uses
  Kuma's official, stable Prometheus `/metrics` endpoint, polled every
  **Poll Interval (minutes)** (`Uptime Monitor Settings`, default 1,
  live-editable with no restart). Every poll is recorded into this app's
  own `Uptime Check Log`, independent of whatever heartbeat history Kuma
  itself retains. If a future Kuma upgrade ever breaks the Socket.IO
  side, only "manage sites from this dashboard" needs a fix — monitoring
  and alerting keep working regardless.

A site is flagged **Critical** once more than half of its last **N**
checks (`Uptime Monitor Settings` → **Alert Window (checks)**, default 3)
report Down — a single flaky check never triggers an alert on its own. A
site with fewer than a full window of checks on record is never flagged,
to avoid a false positive on insufficient data. Maintenance/Pending
readings from Kuma are excluded from that count entirely, not treated as
"down." Crossing the threshold dispatches a **Site Down** alert through
the existing Email/WhatsApp channels; dropping back below it sends the
same "back to normal" recovery message other alert types get.

## Known Proxmox API limitations (not bugs in this app)

- **VM disk usage** is only available if the QEMU guest agent is installed
  and running inside that VM — Proxmox itself cannot see inside a VM's
  filesystem otherwise. LXC containers always report real disk usage.
- **Which storage a backup targeted** isn't in the Proxmox task-list API —
  this app fetches the task log text once per new backup (and only once,
  ever, per backup) to tell a local-disk backup apart from a PBS-remote one.
- **Matching a PBS snapshot to a specific PVE host** isn't possible from
  the PBS API alone (a `vmid` isn't globally unique across hosts) — this is
  a non-issue for this fleet since only Alpha ships to the shared PBS
  datastore, but is worth knowing if that ever changes.

## Future phases (not implemented yet)

- **One-click CT deployment from templates** — listing available CT
  templates per server and creating a new container directly from the
  dashboard, rather than the read-only monitoring this version provides.
- **Direct-provider (OpenAI/Google/etc.) billing API tracking**, feeding
  the same `LLM Usage Log` table the Bifrost sync already populates — see
  [AI Usage](#ai-usage-bifrost).

## Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/proxmox_monitor
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

## Credits

The entirety of this app's code — backend, frontend, DocTypes, and this
README — was written by [Claude Code](https://claude.com/claude-code),
Anthropic's agentic coding tool, working iteratively with the maintainer
against a real Proxmox fleet over the course of its development. Every
feature described above was implemented, deployed, and verified against
live infrastructure as part of that process.

## License

[MIT](license.txt) © 2026 AlfaEdge
