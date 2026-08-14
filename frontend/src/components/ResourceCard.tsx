import { HeartbeatDot } from './HeartbeatDot'
import { MeterRow, meterNeedsAttention } from './MeterRow'
import { StatusBadge } from './StatusBadge'
import { timeAgo } from '../lib/format'
import { hostHealthStatus } from '../lib/severity'
import type { MonitoredHost } from '../lib/types'

export type ResourceKind = 'host' | 'pbs' | 'guest'
export type Severity = 'ok' | 'warning' | 'critical'

export interface DiskMeter {
  label: string
  value?: number | null
}

export interface ResourceCardProps {
  kind: ResourceKind
  title: string
  subtitle?: string
  status: string
  severity: Severity
  cpu?: number | null
  memory?: number | null
  /** One entry per physical drive/datastore — a guest has exactly one (its virtual
   * disk), a PVE host has one per drive (OS+backup, LVM-Thin guest storage), a PBS
   * instance has one per datastore. Never the root `/` filesystem — that's tracked
   * separately (see NodeDetailDialog) but isn't the meaningful number for this fleet. */
  disks: DiskMeter[]
  lastSynced?: string
  pollIntervalSeconds: number
  warningThreshold: number
  criticalThreshold: number
  tags?: string[]
  /** Reorders the CPU/RAM/first-disk meters (e.g. to lead with whatever
   * metric the caller is currently sorting by). Extra disks beyond the
   * first (host cards only) always render after these three, unaffected.
   * Omitted entirely for host/PBS cards, which keep the fixed CPU→RAM→
   * Swap→drives order. */
  meterOrder?: Array<'cpu' | 'ram' | 'disk'>
  /** Only ever set for guest cards (Host Health has no concept of a
   * physical Proxmox Server) — omitted whenever this guest has no matching
   * Monitored Host record (Host Health is opt-in, not universal). */
  hostHealth?: MonitoredHost
  onClick: () => void
}

const KIND_ICON: Record<ResourceKind, string> = {
  host: '🖥️',
  pbs: '🗄️',
  guest: '📦',
}

/** One card in the dashboard grid — a PVE host, the PBS instance, or a single VM/CT.
 * Critical nodes get a colored left-edge accent (the same status-color-as-signal
 * convention a priority ticket list uses) and sort to the top of their section
 * (handled by the caller); this component only renders the visual state.
 *
 * Hand-rolled rather than the library's `Card` component: that component's public
 * API only takes a plain string `title`/`subtitle` and has no `onClick`/`className`
 * passthrough, which doesn't fit the icon + badge header or click-to-drill-down
 * behaviour this dashboard needs. */
export function ResourceCard({
  kind,
  title,
  subtitle,
  status,
  severity,
  cpu,
  memory,
  disks,
  lastSynced,
  pollIntervalSeconds,
  warningThreshold,
  criticalThreshold,
  tags,
  meterOrder,
  hostHealth,
  onClick,
}: ResourceCardProps) {
  const baseMeters: Record<'cpu' | 'ram' | 'disk', DiskMeter> = {
    cpu: { label: 'CPU', value: cpu },
    ram: { label: 'RAM', value: memory },
    disk: disks[0] ?? { label: 'Disk', value: undefined },
  }
  const order = meterOrder ?? (['cpu', 'ram', 'disk'] as const)
  const extraDisks = disks.slice(1)

  // Only the meters that actually need attention render — a card with
  // nothing wrong doesn't need every ring taking up space all the time.
  // Missing data (N/A) is treated the same as "fine" here for the same
  // reason (see `meterNeedsAttention`).
  const allMeters = [...order.map((key) => ({ id: key, ...baseMeters[key] })), ...extraDisks.map((d) => ({ id: d.label, ...d }))]
  const noteworthyMeters = allMeters.filter((m) => meterNeedsAttention(m.value, warningThreshold))

  const accentClass =
    severity === 'critical' ? 'border-l-4 border-l-status-critical'
    : severity === 'warning' ? 'border-l-4 border-l-status-warning'
    : ''
  const pulseClass = severity === 'critical' ? 'card-critical-pulse' : ''

  return (
    <div
      onClick={onClick}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && onClick()}
      className={`cursor-pointer rounded-2xl border border-border-hairline bg-surface-card p-6 shadow-sm transition-shadow hover:shadow-md ${accentClass} ${pulseClass}`}
    >
      <div className="mb-5 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-base font-medium text-ink-primary">
            <span aria-hidden className="text-sm">{KIND_ICON[kind]}</span>
            <span className="truncate">{title}</span>
          </div>
          {subtitle && <p className="mt-0.5 truncate text-sm text-ink-secondary">{subtitle}</p>}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <HeartbeatDot lastSynced={lastSynced} pollIntervalSeconds={pollIntervalSeconds} critical={severity === 'critical'} />
          <StatusBadge status={status} />
        </div>
      </div>

      {noteworthyMeters.length === 0 ? (
        <div className="flex items-center justify-center gap-2 py-4 text-sm text-status-good">
          <span aria-hidden>✓</span>
          <span>All metrics normal</span>
        </div>
      ) : (
        <div className="flex flex-wrap items-center justify-around gap-x-6 gap-y-6 py-2">
          {noteworthyMeters.map((m) => (
            <MeterRow key={m.id} label={m.label} value={m.value} warningThreshold={warningThreshold} criticalThreshold={criticalThreshold} />
          ))}
        </div>
      )}

      {hostHealth && (
        <div className="mt-4 flex items-center justify-between gap-2 border-t border-border-hairline pt-4 text-sm">
          <span className="flex items-center gap-1.5 text-ink-secondary">
            <span aria-hidden>🩺</span> Host Health
          </span>
          <StatusBadge status={hostHealthStatus(hostHealth)} />
        </div>
      )}

      <div className="mt-5 flex items-center justify-between text-xs text-ink-muted">
        <span>synced {timeAgo(lastSynced)}</span>
        {tags && tags.length > 0 && <span className="truncate">{tags.join(', ')}</span>}
      </div>
    </div>
  )
}
