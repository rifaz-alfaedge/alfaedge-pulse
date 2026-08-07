import { HeartbeatDot } from './HeartbeatDot'
import { MeterRow } from './MeterRow'
import { StatusBadge } from './StatusBadge'
import { timeAgo } from '../lib/format'

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
  onClick,
}: ResourceCardProps) {
  const accentClass =
    severity === 'critical' ? 'border-l-4 border-l-status-critical'
    : severity === 'warning' ? 'border-l-4 border-l-status-warning'
    : ''

  return (
    <div
      onClick={onClick}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && onClick()}
      className={`cursor-pointer rounded-2xl border border-border-hairline bg-surface-card p-6 shadow-sm transition-shadow hover:shadow-md ${accentClass}`}
    >
      <div className="mb-5 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-base font-medium text-ink-primary">
            <span aria-hidden>{KIND_ICON[kind]}</span>
            <span className="truncate">{title}</span>
          </div>
          {subtitle && <p className="mt-0.5 truncate text-sm text-ink-secondary">{subtitle}</p>}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <HeartbeatDot lastSynced={lastSynced} pollIntervalSeconds={pollIntervalSeconds} critical={severity === 'critical'} />
          <StatusBadge status={status} />
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-around gap-x-6 gap-y-6 py-2">
        <MeterRow label="CPU" value={cpu} warningThreshold={warningThreshold} criticalThreshold={criticalThreshold} />
        <MeterRow label="RAM" value={memory} warningThreshold={warningThreshold} criticalThreshold={criticalThreshold} />
        {disks.map((d) => (
          <MeterRow key={d.label} label={d.label} value={d.value} warningThreshold={warningThreshold} criticalThreshold={criticalThreshold} />
        ))}
      </div>

      <div className="mt-5 flex items-center justify-between text-xs text-ink-muted">
        <span>synced {timeAgo(lastSynced)}</span>
        {tags && tags.length > 0 && <span className="truncate">{tags.join(', ')}</span>}
      </div>
    </div>
  )
}
