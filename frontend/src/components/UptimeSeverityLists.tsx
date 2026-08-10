import { useUptimeInstances, useUptimeSites } from '../lib/hooks'
import { type SiteGroup, groupSites } from '../lib/uptimeGroups'
import { StatusBadge } from './StatusBadge'

function SiteListCard({
  title,
  icon,
  groups,
  emptyMessage,
  accentClass,
}: {
  title: string
  icon: string
  groups: SiteGroup[]
  emptyMessage: string
  accentClass: string
}) {
  return (
    <div className={`rounded-2xl border border-border-hairline bg-surface-card p-6 shadow-sm ${accentClass}`}>
      <h3 className="mb-4 flex items-center gap-2 text-sm font-semibold uppercase tracking-widest text-ink-muted">
        <span aria-hidden>{icon}</span>
        {title} ({groups.length})
      </h3>
      {groups.length === 0 ? (
        <p className="text-sm text-ink-muted">{emptyMessage}</p>
      ) : (
        <ul className="max-h-64 space-y-3 overflow-y-auto">
          {groups.map((g) => (
            <li key={g.siteName} className="flex items-center justify-between gap-3 border-b border-border-hairline pb-3 last:border-0 last:pb-0">
              <p className="truncate text-sm font-medium text-ink-primary">{g.siteName}</p>
              <StatusBadge status="Down" />
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** Mirrors InstanceSeverityLists (Proxmox) for Uptime Kuma sites — shown
 * above the main tab content on every tab, not just the Uptime tab, so a
 * site going down is visible fleet-wide no matter which page you're on.
 *
 * A site's `is_critical` is now a cross-instance consensus value (at least
 * half of its monitoring instances report it down — see
 * tasks/uptime_kuma_poller.py's `_reconcile_site_criticality`), so every
 * sibling doc for a site already agrees; grouping just collapses the
 * repeats into one row per site. Only the site name and its status show
 * here — the per-instance breakdown stays on the Uptime tab's own cards. */
export function UptimeSeverityLists() {
  const { data: instances } = useUptimeInstances()
  const { data: sites } = useUptimeSites()

  if (!instances || instances.length === 0) return null

  const groups = groupSites(sites ?? [])
  const criticalGroups = groups.filter((g) => g.sites.some((s) => s.is_critical))
  const downOnlyGroups = groups.filter(
    (g) => !g.sites.some((s) => s.is_critical) && g.sites.some((s) => s.current_status === 'Down'),
  )

  return (
    <div className="mb-8 grid grid-cols-1 gap-6 lg:grid-cols-2">
      <SiteListCard
        title="Critical Sites"
        icon="⛔"
        groups={criticalGroups}
        emptyMessage="No critical sites 🎉"
        accentClass="border-l-4 border-l-status-critical"
      />
      <SiteListCard
        title="Down (not yet critical)"
        icon="⚠️"
        groups={downOnlyGroups}
        emptyMessage="No sites currently down"
        accentClass="border-l-4 border-l-status-warning"
      />
    </div>
  )
}
