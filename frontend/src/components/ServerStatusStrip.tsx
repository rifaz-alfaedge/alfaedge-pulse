import { formatPercent } from '../lib/format'
import type { ProxmoxDatastore, ProxmoxServer } from '../lib/types'

/** Mean of each of this server's physical drives' own usage% (the same
 * `Proxmox Datastore` rows the host card's disk meters already read from —
 * see `driveLabel` in App.tsx) — deliberately NOT `server.storage_usage`,
 * which is only the root filesystem and explicitly documented server-side
 * as excluded from alerting because it doesn't reflect this fleet's real
 * disk layout (two separate physical drives per host; see poller.py's
 * `_apply_host_status`). `null` when a server has no datastore rows yet,
 * not 0% — no data is not the same as an empty disk. */
function averageDriveUsage(server: ProxmoxServer, datastores: ProxmoxDatastore[]): number | null {
  const values = datastores
    .filter((d) => d.server === server.name)
    .map((d) => d.usage_percent)
    .filter((v): v is number => v !== undefined && v !== null)
  if (values.length === 0) return null
  return values.reduce((sum, v) => sum + v, 0) / values.length
}

/** Always-visible, view-only ticker of every non-PBS Proxmox host's
 * CPU/RAM/Storage, sticky above the header so it stays on screen across
 * every tab — unlike the host cards themselves, which only render on the
 * Usage Metrics tab. Deliberately text/badge-based rather than the card
 * meters' rings: this has to stay one thin row even with a couple dozen
 * hosts in the fleet. Not interactive — plain glanceable status only. */
export function ServerStatusStrip({
  servers,
  datastores,
  warningThreshold,
  criticalThreshold,
}: {
  servers: ProxmoxServer[]
  datastores: ProxmoxDatastore[]
  warningThreshold: number
  criticalThreshold: number
}) {
  if (servers.length === 0) return null

  return (
    <div className="sticky top-0 z-30 -mx-6 mb-6 border-b border-border-hairline bg-surface-card/95 px-6 backdrop-blur sm:-mx-10 sm:px-10 lg:-mx-16 lg:px-16">
      <div className="flex items-center gap-5 overflow-x-auto py-2.5 [scrollbar-width:thin]">
        {servers.map((s) => (
          <div
            key={s.name}
            className="flex shrink-0 items-center gap-3 rounded-full border border-border-hairline bg-surface-base px-3 py-1"
          >
            <span className="text-xs font-semibold text-ink-primary">{s.server_name}</span>
            {s.status === 'Offline' ? (
              <span className="text-xs font-medium text-status-warning">Offline</span>
            ) : (
              <span className="flex items-center gap-2.5 text-xs tabular-nums">
                <Metric label="CPU" value={s.cpu_usage} warningThreshold={warningThreshold} criticalThreshold={criticalThreshold} />
                <Metric label="RAM" value={s.memory_usage} warningThreshold={warningThreshold} criticalThreshold={criticalThreshold} />
                <Metric label="Disk" value={averageDriveUsage(s, datastores)} warningThreshold={warningThreshold} criticalThreshold={criticalThreshold} />
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

function Metric({
  label,
  value,
  warningThreshold,
  criticalThreshold,
}: {
  label: string
  value?: number | null
  warningThreshold: number
  criticalThreshold: number
}) {
  const toneClass =
    value === undefined || value === null
      ? 'text-ink-muted'
      : value >= criticalThreshold
        ? 'font-semibold text-status-critical'
        : value >= warningThreshold
          ? 'font-semibold text-status-warning'
          : 'text-ink-secondary'
  return (
    <span className={toneClass}>
      {label} {formatPercent(value)}
    </span>
  )
}
