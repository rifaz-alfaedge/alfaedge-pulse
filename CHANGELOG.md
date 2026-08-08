# Changelog

All notable changes to this project are documented here.

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
