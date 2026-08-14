import { useUptimeInstances, useUptimeSites } from '../lib/hooks'
import { groupSites } from '../lib/uptimeGroups'

/** A full `ResourceCard`-weight summary tile for Uptime, sitting among/
 * above the host/guest grid on the Overview tab — Uptime Sites have no
 * link to any Proxmox Server/Guest (see lib/types.ts's UptimeSite), so
 * there's no way to fold uptime status into an individual host/guest card;
 * this is the standalone alternative, deliberately given the same visual
 * weight as those cards rather than being a lightweight footnote. Same
 * "nothing connected yet" guard as `AlertsPopup`'s own Host Health/Uptime
 * items — renders nothing with zero Uptime Kuma instances, rather than an
 * empty-looking "0 sites" tile. */
export function UptimeSummaryTile({ onOpen }: { onOpen: () => void }) {
  const { data: instances } = useUptimeInstances()
  const { data: sites } = useUptimeSites()

  if (!instances || instances.length === 0) return null

  const groups = groupSites(sites ?? [])
  const criticalCount = groups.filter((g) => g.sites.some((s) => s.is_critical)).length
  const downCount = groups.filter(
    (g) => !g.sites.some((s) => s.is_critical) && g.sites.some((s) => s.current_status === 'Down'),
  ).length

  const accentClass =
    criticalCount > 0 ? 'border-l-4 border-l-status-critical'
    : downCount > 0 ? 'border-l-4 border-l-status-warning'
    : ''
  const pulseClass = criticalCount > 0 ? 'card-critical-pulse' : ''

  return (
    <div
      onClick={onOpen}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && onOpen()}
      className={`cursor-pointer rounded-2xl border border-border-hairline bg-surface-card p-6 shadow-sm transition-shadow hover:shadow-md ${accentClass} ${pulseClass}`}
    >
      <div className="mb-5 flex items-center gap-2 text-base font-medium text-ink-primary">
        <span aria-hidden className="text-sm">🌐</span>
        <span>Uptime</span>
      </div>
      <div className="flex flex-wrap items-center justify-around gap-x-6 gap-y-4 py-2">
        <Stat value={groups.length} label="Sites Monitored" tone="ok" />
        <Stat value={criticalCount} label="Critical" tone="critical" />
        <Stat value={downCount} label="Down" tone="warning" />
      </div>
    </div>
  )
}

function Stat({ value, label, tone }: { value: number; label: string; tone: 'ok' | 'warning' | 'critical' }) {
  const dim = value === 0 && tone !== 'ok'
  const toneClass = dim ? 'text-ink-muted' : tone === 'critical' ? 'text-status-critical' : tone === 'warning' ? 'text-status-warning' : 'text-ink-primary'
  return (
    <div className="flex flex-col items-center gap-1">
      <span className={`text-2xl font-semibold ${toneClass}`}>{value}</span>
      <span className="text-sm text-ink-secondary">{label}</span>
    </div>
  )
}
