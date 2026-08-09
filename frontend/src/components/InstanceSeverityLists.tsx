import { formatPercent } from '../lib/format'
import type { ProxmoxDatastore, ProxmoxGuest, ProxmoxServer } from '../lib/types'

interface Row {
  key: string
  title: string
  cpu?: number | null
  ram?: number | null
  storage?: number | null
}

/** A host's storage isn't one number — it's a set of independent drives
 * (see Proxmox Datastore), each with its own usage and its own
 * critical/warning state (this is exactly why isHostCritical/isHostWarning
 * in App.tsx check the server's drives, not just its own is_critical flag).
 * So the one "Storage" figure shown here is the worst drive's usage — the
 * same drive actually responsible for the host appearing in this list —
 * rather than `server.storage_usage` (root filesystem only), which can
 * look deceptively low while a different drive is the real problem. */
function worstDatastoreUsage(server: ProxmoxServer, datastores: ProxmoxDatastore[]): number | null {
  const own = datastores.filter((d) => d.server === server.name)
  if (!own.length) return server.storage_usage ?? null
  return Math.max(...own.map((d) => d.usage_percent ?? 0))
}

function serverRow(s: ProxmoxServer, datastores: ProxmoxDatastore[]): Row {
  return {
    key: `server-${s.name}`,
    title: `${s.server_name} · ${s.hostname}`,
    cpu: s.cpu_usage,
    ram: s.memory_usage,
    storage: worstDatastoreUsage(s, datastores),
  }
}

function guestRow(g: ProxmoxGuest): Row {
  return {
    key: `guest-${g.name}`,
    title: `${g.server} · ${g.vmid} · ${g.guest_name}`,
    cpu: g.cpu_usage,
    ram: g.memory_usage,
    storage: g.disk_usage,
  }
}

function SeverityListCard({
  title,
  icon,
  rows,
  emptyMessage,
  accentClass,
}: {
  title: string
  icon: string
  rows: Row[]
  emptyMessage: string
  accentClass: string
}) {
  return (
    <div className={`rounded-2xl border border-border-hairline bg-surface-card p-6 shadow-sm ${accentClass}`}>
      <h3 className="mb-4 flex items-center gap-2 text-sm font-semibold uppercase tracking-widest text-ink-muted">
        <span aria-hidden>{icon}</span>
        {title} ({rows.length})
      </h3>
      {rows.length === 0 ? (
        <p className="text-sm text-ink-muted">{emptyMessage}</p>
      ) : (
        <ul className="max-h-64 space-y-3 overflow-y-auto">
          {rows.map((row) => (
            <li key={row.key} className="border-b border-border-hairline pb-3 last:border-0 last:pb-0">
              <p className="truncate text-sm font-medium text-ink-primary">{row.title}</p>
              <p className="mt-0.5 text-xs text-ink-secondary">
                CPU {formatPercent(row.cpu)} &nbsp; RAM {formatPercent(row.ram)} &nbsp; Storage{' '}
                {formatPercent(row.storage)}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** Two persistent "fleet health at a glance" cards, shown above the main
 * tab content regardless of which tab (Overview/Backups/Alerts) is active
 * — Critical Instances and Warning Instances, each a flat list of
 * servers/guests currently in that severity tier. Deliberately excludes
 * datastores (the user's spec named "main server or vm/ct" only); a
 * datastore's own critical/warning state still surfaces through its
 * owning server's card via isHostCritical/isHostWarning. */
export function InstanceSeverityLists({
  servers,
  guests,
  datastores,
  isHostCritical,
  isHostWarning,
}: {
  servers: ProxmoxServer[]
  guests: ProxmoxGuest[]
  datastores: ProxmoxDatastore[]
  isHostCritical: (s: ProxmoxServer) => boolean
  isHostWarning: (s: ProxmoxServer) => boolean
}) {
  const criticalRows = [
    ...servers.filter(isHostCritical).map((s) => serverRow(s, datastores)),
    ...guests.filter((g) => g.is_critical).map(guestRow),
  ]
  const warningRows = [
    ...servers.filter((s) => isHostWarning(s) && !isHostCritical(s)).map((s) => serverRow(s, datastores)),
    ...guests.filter((g) => g.is_warning && !g.is_critical).map(guestRow),
  ]

  return (
    <div className="mb-8 grid grid-cols-1 gap-6 lg:grid-cols-2">
      <SeverityListCard
        title="Critical Instances"
        icon="⛔"
        rows={criticalRows}
        emptyMessage="No critical instances 🎉"
        accentClass="border-l-4 border-l-status-critical"
      />
      <SeverityListCard
        title="Warning Instances"
        icon="⚠️"
        rows={warningRows}
        emptyMessage="No warnings"
        accentClass="border-l-4 border-l-status-warning"
      />
    </div>
  )
}
