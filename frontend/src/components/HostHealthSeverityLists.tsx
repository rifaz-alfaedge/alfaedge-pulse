import { useMonitoredHosts, useServiceStatusLogs } from '../lib/hooks'
import type { MonitoredHost, ServiceStatusLog } from '../lib/types'
import { StatusBadge } from './StatusBadge'

function ListCard<T>({
  title,
  icon,
  rows,
  emptyMessage,
  accentClass,
  rowKey,
  renderRow,
}: {
  title: string
  icon: string
  rows: T[]
  emptyMessage: string
  accentClass: string
  rowKey: (row: T) => string
  renderRow: (row: T) => React.ReactNode
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
            <li
              key={rowKey(row)}
              className="flex items-center justify-between gap-3 border-b border-border-hairline pb-3 last:border-0 last:pb-0"
            >
              {renderRow(row)}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** Mirrors UptimeSeverityLists for Host Health — shown above the main tab
 * content on every tab, not just Host Health's own, so a service outage or
 * an unreachable host is visible fleet-wide regardless of which page
 * you're on.
 *
 * Host Health's two natural severity buckets don't line up with Uptime's
 * "critical vs. down-but-not-yet-critical" split (there's no consensus
 * concept here) — instead this splits by *what* is unhealthy: the host
 * itself (unreachable, or its Frappe bench in a sustained bad state) vs.
 * an individual OS service on an otherwise-reachable host. */
export function HostHealthSeverityLists() {
  const { data: hosts } = useMonitoredHosts()
  const { data: services } = useServiceStatusLogs()

  if (!hosts || hosts.length === 0) return null

  const hostByName = new Map(hosts.map((h) => [h.name, h]))
  const criticalHosts = hosts.filter((h) => !h.is_online || h.worker_health_critical || h.failed_job_critical)
  const downServices = (services ?? []).filter((s) => s.is_down && hostByName.get(s.monitored_host)?.is_online)

  return (
    <div className="mb-8 grid grid-cols-1 gap-6 lg:grid-cols-2">
      <ListCard<MonitoredHost>
        title="Critical Hosts"
        icon="⛔"
        rows={criticalHosts}
        emptyMessage="No critical hosts 🎉"
        accentClass="border-l-4 border-l-status-critical"
        rowKey={(h) => h.name}
        renderRow={(h) => (
          <>
            <p className="truncate text-sm font-medium text-ink-primary">{h.hostname || h.name}</p>
            <StatusBadge status={!h.is_online ? 'Down' : 'Critical'} />
          </>
        )}
      />
      <ListCard<ServiceStatusLog>
        title="Services Down"
        icon="⚠️"
        rows={downServices}
        emptyMessage="No services currently down"
        accentClass="border-l-4 border-l-status-warning"
        rowKey={(s) => s.name}
        renderRow={(s) => (
          <>
            <p className="truncate text-sm font-medium text-ink-primary">
              {hostByName.get(s.monitored_host)?.hostname || s.monitored_host}
              <span className="ml-1 text-ink-muted">· {s.service_name}</span>
            </p>
            <StatusBadge status="Down" />
          </>
        )}
      />
    </div>
  )
}
