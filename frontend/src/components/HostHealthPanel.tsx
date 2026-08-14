import { useMemo, useState } from 'react'
import { Dialog } from '@rtcamp/frappe-ui-react'
import { useFrappePostCall } from 'frappe-react-sdk'
import {
  useFailedJobGroups,
  useFrappeFailedJobLogs,
  useFrappeWorkerHealthLogs,
  useGuests,
  useHostMonitorSettings,
  useMonitoredHostSites,
  useMonitoredHosts,
  useServers,
  useServiceStatusLogs,
} from '../lib/hooks'
import type { FrappeFailedJobLog, FrappeWorkerHealthLog, MonitoredHost, MonitoredHostSite, ServiceStatusLog } from '../lib/types'
import { getErrorMessage } from '../lib/errors'
import { timeAgo } from '../lib/format'
import { HeartbeatDot } from './HeartbeatDot'
import { StatusBadge } from './StatusBadge'
import { TrendChart } from './TrendChart'

const DEFAULT_HEARTBEAT_TIMEOUT_SECONDS = 90

/** Latest row per (monitored_host, bench_name) from an append-only log —
 * the same "latest row per bench" query the ingest endpoint's own streak
 * computation needs server-side (see host_health/ingest.py's
 * _process_bench), done here client-side since the fetched window already
 * comes back newest-first. */
function latestPerBench(logs: FrappeWorkerHealthLog[]): FrappeWorkerHealthLog[] {
  const seen = new Set<string>()
  const latest: FrappeWorkerHealthLog[] = []
  for (const row of logs) {
    const key = `${row.monitored_host}::${row.bench_name}`
    if (seen.has(key)) continue
    seen.add(key)
    latest.push(row)
  }
  return latest
}

function parseQueueDepths(json?: string): Record<string, number> {
  if (!json) return {}
  try {
    return JSON.parse(json) as Record<string, number>
  } catch {
    return {}
  }
}

/** Full Host Health dashboard tab — chip row + card grid + a per-host
 * detail dialog, mirroring UptimePanel's shape. The fleet-wide "what's
 * currently unhealthy" summary lives in the SeveritySidebar (shown on
 * every tab, not just this one); this panel is the full host grid and
 * per-host drill-down. */
export function HostHealthPanel() {
  const { data: hosts } = useMonitoredHosts()
  const { data: sites } = useMonitoredHostSites()
  const { data: services } = useServiceStatusLogs()
  const { data: workerLogs } = useFrappeWorkerHealthLogs()
  const { data: failedJobs } = useFrappeFailedJobLogs()
  const { data: settings } = useHostMonitorSettings()
  const { data: guests } = useGuests()
  const { data: servers } = useServers()
  const [selectedHost, setSelectedHost] = useState<MonitoredHost | null>(null)

  const allHosts = hosts ?? []
  const allSites = sites ?? []
  const allServices = services ?? []
  const allWorkerLogs = workerLogs ?? []
  const allFailedJobs = failedJobs ?? []
  const heartbeatTimeoutSeconds = settings?.heartbeat_timeout_seconds || DEFAULT_HEARTBEAT_TIMEOUT_SECONDS

  const latestBenches = useMemo(() => latestPerBench(allWorkerLogs), [allWorkerLogs])
  const guestByName = new Map((guests ?? []).map((g) => [g.name, g]))
  const serverByName = new Map((servers ?? []).map((s) => [s.name, s]))
  const roleFor = (host: MonitoredHost) => {
    const guest = guestByName.get(host.proxmox_guest)
    return guest ? serverByName.get(guest.server)?.role : undefined
  }

  if (allHosts.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-border-hairline p-10 text-center text-sm text-ink-muted">
        No hosts being monitored yet — add a <em>Monitored Host</em> from Desk and point its push agent at this
        site to get started.
      </div>
    )
  }

  return (
    <div>
      <div className="mb-6 flex flex-wrap gap-3">
        {allHosts.map((host) => (
          <button
            key={host.name}
            type="button"
            onClick={() => setSelectedHost(host)}
            className="flex items-center gap-2 rounded-full border border-border-hairline bg-surface-card px-4 py-2 text-sm transition-colors hover:bg-ink-primary/5"
          >
            {/* HeartbeatDot treats a reading as fresh for 4x its
             * pollIntervalSeconds — dividing the host's own heartbeat
             * timeout by 4 here makes the dot go stale at exactly the
             * same cutoff the server-side watchdog uses for Host
             * Unreachable, instead of an unrelated, made-up interval. */}
            <HeartbeatDot
              lastSynced={host.last_seen}
              pollIntervalSeconds={heartbeatTimeoutSeconds / 4}
              critical={!host.is_online || !!host.worker_health_critical || !!host.failed_job_critical}
            />
            <span className="font-medium text-ink-primary">{host.hostname || host.name}</span>
            <span className="text-xs text-ink-muted">{host.is_online ? timeAgo(host.last_seen) : 'unreachable'}</span>
          </button>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 xl:grid-cols-3">
        {allHosts.map((host) => (
          <HostCard
            key={host.name}
            host={host}
            role={roleFor(host)}
            services={allServices.filter((s) => s.monitored_host === host.name)}
            benches={latestBenches.filter((w) => w.monitored_host === host.name)}
            sites={allSites.filter((s) => s.parent === host.name)}
            openFailedJobCount={allFailedJobs.filter((j) => j.monitored_host === host.name).length}
            onOpenDetail={() => setSelectedHost(host)}
          />
        ))}
      </div>

      {selectedHost && (
        <HostDetailDialog
          host={selectedHost}
          role={roleFor(selectedHost)}
          services={allServices.filter((s) => s.monitored_host === selectedHost.name)}
          workerLogs={allWorkerLogs.filter((w) => w.monitored_host === selectedHost.name)}
          failedJobs={allFailedJobs.filter((j) => j.monitored_host === selectedHost.name)}
          sites={allSites.filter((s) => s.parent === selectedHost.name)}
          onClose={() => setSelectedHost(null)}
        />
      )}
    </div>
  )
}

function HostCard({
  host,
  role,
  services,
  benches,
  sites,
  openFailedJobCount,
  onOpenDetail,
}: {
  host: MonitoredHost
  role?: string
  services: ServiceStatusLog[]
  benches: FrappeWorkerHealthLog[]
  sites: MonitoredHostSite[]
  openFailedJobCount: number
  onOpenDetail: () => void
}) {
  const anyServiceDown = services.some((s) => s.is_down)
  const critical = !host.is_online || !!host.worker_health_critical || !!host.failed_job_critical
  const totalOrphans = benches.reduce((sum, b) => sum + (b.orphan_worker_count || 0), 0)

  return (
    <div
      className={`rounded-2xl border bg-surface-card p-5 shadow-sm ${
        critical ? 'border-l-4 border-l-status-critical' : anyServiceDown ? 'border-l-4 border-l-status-warning' : 'border-border-hairline'
      }`}
    >
      <div className="mb-3 flex items-start justify-between gap-2">
        <div className="min-w-0">
          <button type="button" onClick={onOpenDetail} className="truncate text-left text-sm font-semibold text-ink-primary hover:underline">
            {host.hostname || host.name}
          </button>
          <p className="mt-0.5 truncate text-xs text-ink-muted">{role || 'Unassigned role'}</p>
        </div>
        <StatusBadge status={host.is_online ? 'Online' : 'Offline'} />
      </div>

      {services.length > 0 && (
        <div className="mb-3 flex flex-wrap gap-1.5 border-y border-border-hairline py-2.5">
          {services.map((s) => (
            <span key={s.name} className="flex items-center gap-1 text-xs">
              <span className="text-ink-secondary">{s.service_name}</span>
              <StatusBadge status={s.current_state} />
            </span>
          ))}
        </div>
      )}

      {benches.length > 0 && (
        <div className="mb-3 space-y-1.5 text-xs text-ink-secondary">
          {benches.map((b) => (
            <div key={b.bench_name} className="flex items-center justify-between">
              <span className="truncate">{b.bench_name}</span>
              <span className={b.orphan_worker_count > 0 || b.failed_job_count > 0 ? 'text-status-warning' : 'text-ink-muted'}>
                {b.live_worker_count}/{b.registered_workers} workers
                {b.orphan_worker_count > 0 ? ` · ${b.orphan_worker_count} orphan` : ''}
                {b.failed_job_count > 0 ? ` · ${b.failed_job_count} failed` : ''}
              </span>
            </div>
          ))}
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-ink-muted">
        <span>
          {sites.length > 0 ? `${sites.length} site${sites.length === 1 ? '' : 's'}` : 'No sites reported'}
          {totalOrphans > 0 && <span className="text-status-warning"> · {totalOrphans} orphan worker{totalOrphans === 1 ? '' : 's'}</span>}
        </span>
        {openFailedJobCount > 0 && <span className="text-status-critical">{openFailedJobCount} open failed job{openFailedJobCount === 1 ? '' : 's'}</span>}
      </div>
    </div>
  )
}

function HostDetailDialog({
  host,
  role,
  services,
  workerLogs,
  failedJobs,
  sites,
  onClose,
}: {
  host: MonitoredHost
  role?: string
  services: ServiceStatusLog[]
  workerLogs: FrappeWorkerHealthLog[]
  failedJobs: FrappeFailedJobLog[]
  sites: MonitoredHostSite[]
  onClose: () => void
}) {
  const benchNames = [...new Set(workerLogs.map((w) => w.bench_name))]
  const [selectedBench, setSelectedBench] = useState(benchNames[0])
  const benchHistory = workerLogs
    .filter((w) => w.bench_name === selectedBench)
    .slice()
    .sort((a, b) => a.timestamp.localeCompare(b.timestamp))
  const x = benchHistory.map((w) => Math.floor(new Date(w.timestamp.replace(' ', 'T')).getTime() / 1000))
  const orphanSeries = benchHistory.map((w) => w.orphan_worker_count)
  const failedJobSeries = benchHistory.map((w) => w.failed_job_count)
  const latestForBench = benchHistory.at(-1)

  const { data: failedJobGroups } = useFailedJobGroups(host.name)

  // useFrappePostCall's `call` actually resolves to the raw
  // `{ message: T }` envelope Frappe wraps every whitelisted return value
  // in (frappe/handler.py: `frappe.response["message"] = data`) — its own
  // type declarations claim `Promise<T>`, but that's not what happens at
  // runtime, confirmed by a real downloaded file containing the literal
  // text "[object Object]" before this was unwrapped.
  const { call: getFailedJobLogText, loading: downloadingLog } = useFrappePostCall<{ message: string }>(
    'proxmox_monitor.host_health.api.get_failed_job_log_text',
  )
  const [downloadError, setDownloadError] = useState<string | null>(null)

  // Full tracebacks aren't part of the polled failedJobs list (kept light
  // for the ~10s refresh cycle) — fetched fresh, grouped by root cause,
  // and formatted server-side (see host_health/api.py) only when actually
  // requested, then handed to the browser as a plain-text file download.
  const downloadLog = async () => {
    setDownloadError(null)
    try {
      const response = await getFailedJobLogText({ monitored_host: host.name })
      const blob = new Blob([response.message], { type: 'text/plain' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${(host.hostname || host.name).replace(/[^a-zA-Z0-9._-]/g, '_')}-failed-jobs.log`
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } catch (e) {
      setDownloadError(getErrorMessage(e))
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()} options={{ title: host.hostname || host.name, size: '2xl' }}>
      <div className="flex flex-col gap-5 py-2">
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <StatusBadge status={host.is_online ? 'Online' : 'Offline'} />
          {role && <span className="text-ink-muted">{role}</span>}
          <span className="text-ink-muted">Last seen {timeAgo(host.last_seen)}</span>
          {!!host.scheduler_overdue && <span className="text-status-warning">Scheduler stalled</span>}
        </div>

        <div>
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-widest text-ink-muted">Services</h3>
          {services.length === 0 ? (
            <p className="text-sm text-ink-muted">No services reported yet.</p>
          ) : (
            <div className="overflow-hidden rounded-lg border border-border-hairline">
              {services.map((s) => (
                <div key={s.name} className="flex items-center justify-between gap-3 border-b border-border-hairline px-3 py-2 text-sm last:border-0">
                  <span className="truncate text-ink-primary">{s.service_name}</span>
                  <span className="flex items-center gap-3 text-xs text-ink-muted">
                    {s.down_streak > 0 && <span className="text-status-warning">streak {s.down_streak}</span>}
                    <span>{timeAgo(s.last_checked)}</span>
                    <StatusBadge status={s.current_state} />
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        {benchNames.length > 0 && (
          <div>
            <div className="mb-2 flex items-center justify-between gap-3">
              <h3 className="text-xs font-semibold uppercase tracking-widest text-ink-muted">Bench Health</h3>
              {benchNames.length > 1 && (
                <select
                  value={selectedBench}
                  onChange={(e) => setSelectedBench(e.target.value)}
                  className="rounded-lg border border-border-hairline bg-surface-page px-2 py-1 text-xs text-ink-primary outline-none focus:border-accent"
                >
                  {benchNames.map((b) => (
                    <option key={b} value={b}>{b}</option>
                  ))}
                </select>
              )}
            </div>
            {latestForBench && (
              <div className="mb-3 flex flex-wrap gap-6 text-sm">
                <Stat label="Workers" value={`${latestForBench.live_worker_count}/${latestForBench.registered_workers}`} />
                <Stat label="Orphans" value={String(latestForBench.orphan_worker_count)} warn={latestForBench.orphan_worker_count > 0} />
                <Stat label="Failed Jobs" value={String(latestForBench.failed_job_count)} warn={latestForBench.failed_job_count > 0} />
                <div>
                  <div className="text-lg font-semibold text-ink-primary">
                    {Object.entries(parseQueueDepths(latestForBench.queue_depths)).map(([q, n]) => `${q}:${n}`).join(' ') || '—'}
                  </div>
                  <div className="text-xs uppercase tracking-wide text-ink-muted">Queue Depths</div>
                </div>
              </div>
            )}
            <div className="rounded-xl border border-gridline p-3">
              {x.length === 0 ? (
                <p className="py-6 text-center text-sm text-ink-muted">No history yet for this bench.</p>
              ) : (
                <TrendChart
                  x={x}
                  series={[
                    { label: 'Failed jobs', data: failedJobSeries, colorVar: '--color-status-critical' },
                    { label: 'Orphan workers', data: orphanSeries, colorVar: '--color-status-warning' },
                  ]}
                />
              )}
            </div>
          </div>
        )}

        {failedJobs.length > 0 && (
          <div>
            <div className="mb-2 flex items-center justify-between gap-3">
              <h3 className="text-xs font-semibold uppercase tracking-widest text-ink-muted">
                Failed Job Root Causes ({(failedJobGroups ?? []).length || failedJobs.length})
              </h3>
              <button
                type="button"
                onClick={downloadLog}
                disabled={downloadingLog}
                className="rounded-lg border border-border-hairline px-2.5 py-1 text-xs text-ink-secondary hover:bg-ink-primary/5 disabled:opacity-50"
              >
                {downloadingLog ? 'Preparing…' : 'Download Log'}
              </button>
            </div>
            {downloadError && <p className="mb-2 text-xs text-status-critical">{downloadError}</p>}
            {/* Human-readable summary — the same grouping "Download Log" writes
             * to a file (host_health/api.py::get_failed_job_groups mirrors
             * get_failed_job_log_text's own grouping), but rendered right here
             * for a reader who just wants the gist without downloading
             * anything. Full tracebacks stay download-only — too long to
             * usefully inline. */}
            <div className="max-h-64 divide-y divide-border-hairline overflow-y-auto rounded-lg border border-border-hairline">
              {(failedJobGroups ?? []).map((g) => (
                <div key={`${g.exc_type}-${g.job_name}`} className="px-3 py-2.5 text-xs">
                  <div className="flex items-center justify-between gap-3">
                    <span className="truncate font-medium text-ink-primary">
                      {g.exc_type} <span className="font-normal text-ink-muted">· {g.job_name}</span>
                    </span>
                    <span className="shrink-0 text-status-critical">
                      {g.occurrence_count}× · last {timeAgo(g.last_seen)}
                    </span>
                  </div>
                  {g.message && <p className="mt-0.5 truncate text-ink-secondary">“{g.message}”</p>}
                </div>
              ))}
            </div>
          </div>
        )}

        <div>
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-widest text-ink-muted">Hosted Sites</h3>
          {sites.length === 0 ? (
            <p className="text-sm text-ink-muted">No sites reported.</p>
          ) : (
            <ul className="space-y-1 text-sm">
              {sites.map((s) => (
                <li key={s.name} className="flex items-center justify-between gap-3">
                  <span className="text-ink-primary">{s.site_name}</span>
                  <span className="text-xs text-ink-muted">{s.site_url || 'no URL configured'}</span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="flex justify-end">
          <button type="button" onClick={onClose} className="rounded-lg px-4 py-2 text-sm text-ink-secondary hover:bg-ink-primary/5">
            Close
          </button>
        </div>
      </div>
    </Dialog>
  )
}

function Stat({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <div>
      <div className={`text-lg font-semibold ${warn ? 'text-status-warning' : 'text-ink-primary'}`}>{value}</div>
      <div className="text-xs uppercase tracking-wide text-ink-muted">{label}</div>
    </div>
  )
}
