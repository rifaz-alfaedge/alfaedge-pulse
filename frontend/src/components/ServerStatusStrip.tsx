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

/** Always-visible ticker, sticky above the header so it stays on screen
 * across every tab — unlike the host cards themselves, which only render on
 * the Usage Metrics tab. Two parts: a fixed (non-scrolling) group of
 * fleet-wide Critical/Offline/Open Alerts counts — clickable, same filter/
 * tab-toggling behavior as the old header stat cards they replaced — then a
 * horizontally-scrolling row of every non-PBS Proxmox host's own CPU/RAM/
 * Storage. Deliberately text/badge-based rather than the card meters'
 * rings: this has to stay one thin row even with a couple dozen hosts in
 * the fleet. The host pills themselves stay plain glanceable status only,
 * not clickable. Rendered even with zero PVE hosts — the fleet-wide counts
 * are meaningful (e.g. an open Uptime alert) independent of whether any
 * Proxmox host exists yet. */
export function ServerStatusStrip({
  servers,
  datastores,
  warningThreshold,
  criticalThreshold,
  criticalCount,
  offlineCount,
  openAlertCount,
  criticalActive,
  offlineActive,
  openAlertsActive,
  onCriticalClick,
  onOfflineClick,
  onOpenAlertsClick,
}: {
  servers: ProxmoxServer[]
  datastores: ProxmoxDatastore[]
  warningThreshold: number
  criticalThreshold: number
  criticalCount: number
  offlineCount: number
  openAlertCount: number
  criticalActive: boolean
  offlineActive: boolean
  openAlertsActive: boolean
  onCriticalClick: () => void
  onOfflineClick: () => void
  onOpenAlertsClick: () => void
}) {
  return (
    <div className="sticky top-0 z-30 -mx-6 mb-6 border-b border-border-hairline bg-surface-card/95 px-6 backdrop-blur sm:-mx-10 sm:px-10 lg:-mx-16 lg:px-16">
      <div className="flex items-center gap-5 py-2.5">
        <div className="flex shrink-0 items-center gap-2">
          <StripStat
            label="Critical"
            value={criticalCount}
            tone={criticalCount > 0 ? 'critical' : 'ok'}
            active={criticalActive}
            onClick={onCriticalClick}
          />
          <StripStat
            label="Offline"
            value={offlineCount}
            tone={offlineCount > 0 ? 'warning' : 'ok'}
            active={offlineActive}
            onClick={onOfflineClick}
          />
          <StripStat
            label="Open Alerts"
            value={openAlertCount}
            tone={openAlertCount > 0 ? 'warning' : 'ok'}
            active={openAlertsActive}
            onClick={onOpenAlertsClick}
          />
        </div>

        {servers.length > 0 && (
          <>
            <div className="h-5 w-px shrink-0 bg-border-hairline" aria-hidden />
            <div className="flex items-center gap-5 overflow-x-auto [scrollbar-width:thin]">
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
                      <Metric
                        label="Disk"
                        value={averageDriveUsage(s, datastores)}
                        warningThreshold={warningThreshold}
                        criticalThreshold={criticalThreshold}
                      />
                    </span>
                  )}
                </div>
              ))}
            </div>
          </>
        )}
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

/** Compact clickable pill version of the old header's SummaryStat cards —
 * sized to fit this strip's one-thin-row constraint rather than the
 * larger rounded-2xl cards it replaced. Same tone/active/onClick contract
 * (toggles a filter or switches tabs in App.tsx) as before the move. */
function StripStat({
  label,
  value,
  tone,
  active,
  onClick,
}: {
  label: string
  value: number
  tone: 'critical' | 'warning' | 'ok'
  active: boolean
  onClick: () => void
}) {
  const toneClass =
    tone === 'critical' ? 'text-status-critical' : tone === 'warning' ? 'text-status-warning' : 'text-ink-primary'
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      title={`Show ${label.toLowerCase()}`}
      className={`flex shrink-0 items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
        active ? 'border-accent bg-accent/10' : 'border-border-hairline bg-surface-base hover:bg-ink-primary/5'
      }`}
    >
      <span className={`font-semibold tabular-nums ${toneClass}`}>{value}</span>
      <span className="text-ink-secondary">{label}</span>
    </button>
  )
}
