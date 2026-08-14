import { useState } from 'react'
import {
  useDatastores,
  useGuests,
  useMonitoredHosts,
  useServers,
  useServiceStatusLogs,
  useUptimeInstances,
  useUptimeSites,
} from '../lib/hooks'
import { hostHealthStatus, isHostCritical, isHostWarning } from '../lib/severity'
import { groupSites } from '../lib/uptimeGroups'
import { formatPercent } from '../lib/format'
import type { ProxmoxDatastore, ProxmoxGuest, ProxmoxServer } from '../lib/types'
import { StatusBadge } from './StatusBadge'

interface SeverityItem {
  key: string
  source: 'Instance' | 'Site' | 'Host Health' | 'Service'
  title: string
  detail?: string
  badge?: 'Down' | 'Critical'
}

/** Same "worst drive wins" reasoning as the old InstanceSeverityLists —
 * a host's own storage_usage is root-filesystem-only and can look fine
 * while a different drive is the actual problem. */
function worstDatastoreUsage(server: ProxmoxServer, datastores: ProxmoxDatastore[]): number | null {
  const own = datastores.filter((d) => d.server === server.name)
  if (!own.length) return server.storage_usage ?? null
  return Math.max(...own.map((d) => d.usage_percent ?? 0))
}

function serverItem(s: ProxmoxServer, datastores: ProxmoxDatastore[]): SeverityItem {
  return {
    key: `server-${s.name}`,
    source: 'Instance',
    title: `${s.server_name} · ${s.hostname}`,
    detail: `CPU ${formatPercent(s.cpu_usage)}  RAM ${formatPercent(s.memory_usage)}  Storage ${formatPercent(worstDatastoreUsage(s, datastores))}`,
  }
}

function guestItem(g: ProxmoxGuest): SeverityItem {
  return {
    key: `guest-${g.name}`,
    source: 'Instance',
    title: `${g.server} · ${g.vmid} · ${g.guest_name}`,
    detail: `CPU ${formatPercent(g.cpu_usage)}  RAM ${formatPercent(g.memory_usage)}  Storage ${formatPercent(g.disk_usage)}`,
  }
}

/** Replaces InstanceSeverityLists + UptimeSeverityLists + HostHealthSeverityLists
 * (and this component's own earlier sidebar-column incarnation) — same
 * three data sources, same per-source filter logic (moved wholesale, not
 * rewritten), merged into one Critical list and one Warning list, now
 * surfaced as a fixed bottom-right chat-widget-style popup instead of a
 * layout column, so it doesn't take permanent space from any tab. Each
 * item keeps a `source` tag so merging doesn't lose which subsystem it's
 * from. Rendered outside the mainTab-gated block in App.tsx, so — like its
 * three predecessors — it's available no matter which tab is active. */
type Filter = 'critical' | 'warning'

export function AlertsPopup() {
  const [filter, setFilter] = useState<Filter | null>(null)
  const { data: servers } = useServers()
  const { data: guests } = useGuests()
  const { data: datastores } = useDatastores()
  const { data: uptimeInstances } = useUptimeInstances()
  const { data: uptimeSites } = useUptimeSites()
  const { data: hosts } = useMonitoredHosts()
  const { data: services } = useServiceStatusLogs()

  const allServers = servers ?? []
  const allGuests = guests ?? []
  const allDatastores = datastores ?? []
  const allHosts = hosts ?? []
  const allServices = services ?? []
  const hostByName = new Map(allHosts.map((h) => [h.name, h]))
  // Same guard UptimeSeverityLists used — no connected Uptime Kuma instance
  // at all means "not set up," not "everything's fine," but there's also
  // nothing meaningful to group/show either way.
  const siteGroups = (uptimeInstances ?? []).length > 0 ? groupSites(uptimeSites ?? []) : []

  const criticalItems: SeverityItem[] = [
    ...allServers.filter((s) => isHostCritical(s, allDatastores)).map((s) => serverItem(s, allDatastores)),
    ...allGuests.filter((g) => g.is_critical).map(guestItem),
    ...siteGroups
      .filter((g) => g.sites.some((s) => s.is_critical))
      .map((g) => ({ key: `site-${g.siteName}`, source: 'Site' as const, title: g.siteName, badge: 'Down' as const })),
    ...allHosts
      .filter((h) => hostHealthStatus(h) !== 'Online')
      .map((h) => ({
        key: `host-${h.name}`,
        source: 'Host Health' as const,
        title: h.hostname || h.name,
        badge: hostHealthStatus(h) as 'Down' | 'Critical',
      })),
  ]

  const warningItems: SeverityItem[] = [
    ...allServers
      .filter((s) => isHostWarning(s, allDatastores) && !isHostCritical(s, allDatastores))
      .map((s) => serverItem(s, allDatastores)),
    ...allGuests.filter((g) => g.is_warning && !g.is_critical).map(guestItem),
    ...siteGroups
      .filter((g) => !g.sites.some((s) => s.is_critical) && g.sites.some((s) => s.current_status === 'Down'))
      .map((g) => ({ key: `site-${g.siteName}`, source: 'Site' as const, title: g.siteName, badge: 'Down' as const })),
    ...allServices
      .filter((s) => s.is_down && hostByName.get(s.monitored_host)?.is_online)
      .map((s) => ({
        key: `service-${s.name}`,
        source: 'Service' as const,
        title: `${hostByName.get(s.monitored_host)?.hostname || s.monitored_host} · ${s.service_name}`,
        badge: 'Down' as const,
      })),
  ]

  const activeItems = filter === 'critical' ? criticalItems : filter === 'warning' ? warningItems : []

  // Clicking the already-active filter closes the panel (toggle); clicking
  // the other one switches which list is shown without closing it.
  const toggle = (next: Filter) => setFilter((current) => (current === next ? null : next))

  return (
    <div className="fixed bottom-6 right-6 z-50 flex flex-col items-end gap-3">
      {filter && (
        <div className="flex max-h-[70vh] w-[22rem] flex-col rounded-2xl border border-border-hairline bg-surface-card shadow-lg">
          <div className="flex items-center justify-between gap-3 border-b border-border-hairline px-4 py-3">
            <span className={`flex items-center gap-2 text-sm font-semibold ${filter === 'critical' ? 'text-status-critical' : 'text-status-warning'}`}>
              <span aria-hidden>{filter === 'critical' ? '⛔' : '⚠️'}</span>
              {filter === 'critical' ? 'Critical' : 'Warning'} ({activeItems.length})
            </span>
            <button type="button" onClick={() => setFilter(null)} aria-label="Close alerts" className="text-ink-muted hover:text-ink-primary">
              ✕
            </button>
          </div>
          <div className="flex-1 overflow-y-auto px-4 py-4">
            <SeverityList items={activeItems} emptyMessage={filter === 'critical' ? 'No critical items 🎉' : 'No warnings'} />
          </div>
        </div>
      )}

      <div className="flex items-center gap-2">
        <FilterChip label="Critical" icon="⛔" count={criticalItems.length} tone="critical" active={filter === 'critical'} onClick={() => toggle('critical')} />
        <FilterChip label="Warning" icon="⚠️" count={warningItems.length} tone="warning" active={filter === 'warning'} onClick={() => toggle('warning')} />
      </div>
    </div>
  )
}

function SeverityList({ items, emptyMessage }: { items: SeverityItem[]; emptyMessage: string }) {
  if (items.length === 0) {
    return <p className="text-sm text-ink-muted">{emptyMessage}</p>
  }
  return (
    <ul className="space-y-3">
      {items.map((item) => (
        <li key={item.key} className="border-b border-border-hairline pb-3 last:border-0 last:pb-0">
          <p className="text-[10px] font-medium uppercase tracking-wide text-ink-muted">{item.source}</p>
          <div className="flex items-center justify-between gap-2">
            <p className="truncate text-sm font-medium text-ink-primary">{item.title}</p>
            {item.badge && <StatusBadge status={item.badge} />}
          </div>
          {item.detail && <p className="mt-0.5 text-xs text-ink-secondary">{item.detail}</p>}
        </li>
      ))}
    </ul>
  )
}

function FilterChip({
  label,
  icon,
  count,
  tone,
  active,
  onClick,
}: {
  label: string
  icon: string
  count: number
  tone: 'critical' | 'warning'
  active: boolean
  onClick: () => void
}) {
  const toneClass = tone === 'critical' ? 'text-status-critical' : 'text-status-warning'
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      aria-label={`${active ? 'Hide' : 'Show'} ${label.toLowerCase()} alerts`}
      className={`flex items-center gap-2 rounded-full border bg-surface-card px-4 py-2.5 text-sm font-medium shadow-lg transition-shadow hover:shadow-xl ${
        active ? 'border-accent ring-2 ring-accent/40' : 'border-border-hairline'
      }`}
    >
      <span aria-hidden>{icon}</span>
      <span className={toneClass}>{label}</span>
      <span className="text-ink-muted">({count})</span>
    </button>
  )
}
