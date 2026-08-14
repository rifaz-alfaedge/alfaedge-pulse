import type { MonitoredHost, ProxmoxDatastore, ProxmoxServer } from './types'

/** A host's own is_critical only reflects CPU/RAM (see poller.py) — disk
 * criticality lives on its individual drives (Proxmox Datastore), so
 * "is this host critical" has to mean "itself or any of its drives," not
 * just the server doctype's own flag. Extracted out of App.tsx so both it
 * and AlertsPopup can share this without prop-drilling. */
export function isHostCritical(server: ProxmoxServer, datastores: ProxmoxDatastore[]): boolean {
  return !!server.is_critical || datastores.some((d) => d.server === server.name && d.is_critical)
}

/** Same idea one tier down — a host's own Warning status comes through
 * `status` (poller.py reuses that field's existing Warning option rather
 * than a separate flag), while a drive's Warning still needs its own
 * Proxmox Datastore row. */
export function isHostWarning(server: ProxmoxServer, datastores: ProxmoxDatastore[]): boolean {
  return server.status === 'Warning' || datastores.some((d) => d.server === server.name && d.is_warning)
}

/** Host Health has no separate warning tier of its own — a Monitored Host
 * is effectively binary (reachable-and-fine vs. actively wrong), unlike
 * Proxmox's warning/critical two-tier model. "Down" (unreachable) is kept
 * distinct from "Critical" (reachable, but its bench is in a sustained bad
 * state) since they call for different responses, even though both are
 * equally urgent. Shared by `AlertsPopup` (its own Host Health items) and
 * `ResourceCard` (a guest card's Host Health row + overall severity), so
 * both agree on the same classification. */
export function hostHealthStatus(host: MonitoredHost): 'Down' | 'Critical' | 'Online' {
  if (!host.is_online) return 'Down'
  if (host.worker_health_critical || host.failed_job_critical) return 'Critical'
  return 'Online'
}
