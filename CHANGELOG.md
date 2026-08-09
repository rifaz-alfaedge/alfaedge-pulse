# Changelog

All notable changes to this project are documented here.

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
- **Alert Subscription module.** Engineers can now self-subscribe to a
  specific Proxmox Server, or one specific VM/CT/datastore on it, and
  choose their own Email/WhatsApp/Telegram contact per subscription
  (*Desk → Alert Subscription → New*). Subscriptions are additive to the
  existing global recipients in Proxmox Monitor Settings — nothing about
  the existing global alerting changes. Each engineer only sees/manages
  their own subscriptions (Managers see all, for audit). Subscribing to
  a server does **not** cascade to its guests/datastores — subscribe to
  those separately if wanted.
- **"Send Test Alert" button on Alert Subscription** — lets a subscriber
  verify their own channel/contact setup without needing Manager access.
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
