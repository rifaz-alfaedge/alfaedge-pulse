# alfaEdge Pulse

**Version 1.0.0**

A standalone Frappe 16 app (no ERPNext dependency) that gives you a single,
real-time dashboard for a Proxmox fleet — PVE hosts, every VM/CT running on
them, and a Proxmox Backup Server instance. Connect a host once and its
VMs/CTs are discovered and kept in sync automatically; nothing is added by
hand.

## Features

- **Auto-discovery.** Add a `Proxmox Server` (a PVE host or a PBS instance)
  with a read-only API token and everything else — every VM/CT, every
  storage pool/datastore, every backup — is discovered and kept in sync on
  its own. Nothing is entered by hand, and anything removed from Proxmox
  disappears from the dashboard on the next cycle.
- **Two-phase resource alerting.** A reading at or above the **Warning**
  threshold (default 85%) only fires once it's stayed there continuously —
  no dip below it, even briefly — for the configured **Warning Duration**
  (default 5 minutes). A reading at or above the **Critical** threshold
  (default 95%) fires immediately, no waiting period. See
  [How it works](#how-it-works) for exactly how the two tiers interact.
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
  itself; see [Alerting](#alerting).
- **A live, glanceable dashboard.** CPU/RAM/Swap/storage rings with
  at-a-glance icons, a heartbeat indicator tied to how recently each node
  actually synced, click-through detail views, and dashboard ordering
  that groups every server (and its guests) by role — Production first,
  then Staging, then Development, then PBS — so what matters most is
  always at the top.

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
- A guest's `is_critical`/`is_warning` are CPU/RAM/disk only, and only
  ever set for servers whose `role` is **Production** — see the role
  check in `_upsert_guest`. Other roles never flag individual guests,
  regardless of actual usage.
- Warning is deliberately not immediate. `_track_severity` only sets
  `is_warning` once a reading has stayed at or above `warning_threshold_percent`
  continuously — tracked via each doctype's `warning_since` field, reset
  to null the instant a reading dips back below the line — for the full
  `warning_duration_minutes`. Critical (`critical_threshold_percent`)
  short-circuits that wait entirely. Escalating from Warning to Critical
  resolves the open Warning alert (Critical supersedes it); dropping back
  from Critical into the warning band re-opens Warning immediately rather
  than restarting the 5-minute wait, since the reading's already been
  elevated the whole time.
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
| Warning Threshold (%) | 85 | See [How it works](#how-it-works) — the sustained-for-a-while tier. |
| Warning Duration (minutes) | 5 | How long a reading must stay at/above the Warning Threshold, with no dip below it, before a Warning alert fires. |
| Critical Threshold (%) | 95 | The immediate, no-waiting-period tier. |
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
  WhatsApp alerts here and enter recipient numbers.

If a channel's app isn't installed, alerts for that channel are silently
skipped (and still logged to **Proxmox Alert Log**) — the other channels
are unaffected.

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

Two directions were scoped out but deliberately left for a later release:

- **One-click CT deployment from templates** — listing available CT
  templates per server and creating a new container directly from the
  dashboard, rather than the read-only monitoring this version provides.
- **Uptime monitoring via self-hosted Uptime Kuma instances**, with
  majority-vote down detection across multiple instances so a single
  regional network blip doesn't false-positive a guest as down.

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
