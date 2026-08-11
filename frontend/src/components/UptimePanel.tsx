import { useState } from 'react'
import { Dialog } from '@rtcamp/frappe-ui-react'
import { useFrappePostCall } from 'frappe-react-sdk'
import {
  useImportableMonitors,
  useUptimeHistory,
  useUptimeInstances,
  useUptimeMonitorSettings,
  useUptimeSites,
  useUptimeSummary,
} from '../lib/hooks'
import type { ImportableMonitor, UptimeKumaInstance, UptimeMonitorType } from '../lib/types'
import { type SiteGroup, groupSites } from '../lib/uptimeGroups'
import { timeAgo } from '../lib/format'
import { getErrorMessage } from '../lib/errors'
import { HeartbeatDot } from './HeartbeatDot'
import { StatusBadge } from './StatusBadge'
import { Tabs } from './Tabs'
import { TrendChart } from './TrendChart'

const MONITOR_TYPES: UptimeMonitorType[] = ['HTTP(s)', 'TCP', 'Ping']

const inputClass =
  'w-full rounded-lg border border-border-hairline bg-surface-page px-3 py-2 text-sm text-ink-primary outline-none focus:border-accent'
const labelClass = 'mb-1.5 block text-xs font-medium uppercase tracking-wide text-ink-muted'

/** Multi-instance Uptime Kuma monitoring: add/manage sites across every
 * connected instance from one dashboard, and see the cross-instance-
 * consensus alert rule's live effect (see tasks/uptime_kuma_poller.py)
 * without needing to open Kuma itself. The fleet-wide Critical/Down
 * summary lives in UptimeSeverityLists (shown on every tab, not just this
 * one) — this panel is just the full site grid and per-site management. */
export function UptimePanel() {
  const { data: instances, mutate: mutateInstances } = useUptimeInstances()
  const { data: sites, mutate: mutateSites } = useUptimeSites()
  const { data: monitorSettings } = useUptimeMonitorSettings()
  const [showAddModal, setShowAddModal] = useState(false)
  const [showImportModal, setShowImportModal] = useState(false)
  const [historyGroup, setHistoryGroup] = useState<SiteGroup | null>(null)

  const allInstances = instances ?? []
  const allSites = sites ?? []
  const siteGroups = groupSites(allSites)
  const instanceLabel = (name: string) => allInstances.find((i) => i.name === name)?.instance_name ?? name
  const pollIntervalSeconds = monitorSettings?.poll_interval_seconds || 60
  // What a *new* site's own check_interval_seconds should default to —
  // Kuma's own heartbeat cadence, not how often we happen to poll for a
  // result (that's pollIntervalSeconds above, used only for the
  // freshness dot).
  const heartbeatIntervalSeconds = monitorSettings?.heartbeat_interval_seconds || 60

  const refresh = () => {
    mutateSites()
    mutateInstances()
  }

  return (
    <div>
      <div className="mb-6 flex flex-wrap gap-3">
        {allInstances.length === 0 ? (
          <p className="text-sm text-ink-muted">
            No Uptime Kuma instances configured yet — add one from Desk (<em>Uptime Kuma Instance</em>) to get started.
          </p>
        ) : (
          allInstances.map((instance) => (
            <InstanceChip key={instance.name} instance={instance} pollIntervalSeconds={pollIntervalSeconds} />
          ))
        )}
      </div>

      <div className="mb-5 flex items-center justify-between gap-4">
        <h2 className="text-sm font-semibold uppercase tracking-widest text-ink-muted">Monitored Sites</h2>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => setShowImportModal(true)}
            disabled={allInstances.length === 0}
            className="rounded-lg border border-border-hairline px-4 py-2 text-sm font-medium text-ink-secondary transition-colors hover:bg-ink-primary/5 disabled:cursor-not-allowed disabled:opacity-40"
          >
            Import Existing Monitors
          </button>
          <button
            type="button"
            onClick={() => setShowAddModal(true)}
            disabled={allInstances.length === 0}
            className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
          >
            + Add Site
          </button>
        </div>
      </div>

      {siteGroups.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-border-hairline p-10 text-center text-sm text-ink-muted">
          No sites added yet.
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 xl:grid-cols-3">
          {siteGroups.map((group) => (
            <SiteGroupCard
              key={group.siteName}
              group={group}
              instanceLabel={instanceLabel}
              onOpenHistory={() => setHistoryGroup(group)}
              onChanged={refresh}
            />
          ))}
        </div>
      )}

      {showAddModal && (
        <AddSiteModal
          instances={allInstances}
          defaultCheckIntervalSeconds={heartbeatIntervalSeconds}
          onClose={() => {
            setShowAddModal(false)
            refresh()
          }}
        />
      )}

      {showImportModal && (
        <ImportMonitorsModal
          instances={allInstances}
          onClose={() => {
            setShowImportModal(false)
            refresh()
          }}
        />
      )}

      {historyGroup && <SiteHistoryDialog group={historyGroup} instanceLabel={instanceLabel} onClose={() => setHistoryGroup(null)} />}
    </div>
  )
}

function InstanceChip({ instance, pollIntervalSeconds }: { instance: UptimeKumaInstance; pollIntervalSeconds: number }) {
  return (
    <div className="flex items-center gap-2 rounded-full border border-border-hairline bg-surface-card px-4 py-2 text-sm">
      <HeartbeatDot lastSynced={instance.last_synced} pollIntervalSeconds={pollIntervalSeconds} critical={!!instance.last_error} />
      <span className="font-medium text-ink-primary">{instance.instance_name}</span>
      {instance.last_error ? (
        <span className="text-xs text-status-critical" title={instance.last_error}>
          error
        </span>
      ) : (
        <span className="text-xs text-ink-muted">{timeAgo(instance.last_synced)}</span>
      )}
    </div>
  )
}

function SiteGroupCard({
  group,
  instanceLabel,
  onOpenHistory,
  onChanged,
}: {
  group: SiteGroup
  instanceLabel: (instance: string) => string
  onOpenHistory: () => void
  onChanged: () => void
}) {
  const { call: pauseSite, loading: pausing } = useFrappePostCall<{ errors: string[] }>('proxmox_monitor.uptime_monitor.api.pause_site')
  const { call: resumeSite, loading: resuming } = useFrappePostCall<{ errors: string[] }>('proxmox_monitor.uptime_monitor.api.resume_site')
  const { call: deleteSite, loading: deleting } = useFrappePostCall<{ errors: string[] }>('proxmox_monitor.uptime_monitor.api.delete_site')
  const [error, setError] = useState<string | null>(null)
  const busy = pausing || resuming || deleting

  const anyActive = group.sites.some((s) => s.is_active)
  const anyCritical = group.sites.some((s) => s.is_critical)
  const anyDown = group.sites.some((s) => s.current_status === 'Down' && !s.is_critical)
  // Pause/resume/delete fan out server-side to every sibling sharing this
  // site_name (see uptime_monitor/api.py) — any one docname in the group
  // is enough to identify it, so the first is used as a representative.
  const primaryName = group.sites[0].name

  const run = async (fn: () => Promise<{ errors: string[] }>) => {
    setError(null)
    try {
      const res = await fn()
      if (res.errors.length > 0) setError(`Some instances failed: ${res.errors.join('; ')}`)
      onChanged()
    } catch (e) {
      setError(getErrorMessage(e))
    }
  }

  return (
    <div
      className={`rounded-2xl border bg-surface-card p-5 shadow-sm ${
        anyCritical ? 'border-l-4 border-l-status-critical' : anyDown ? 'border-l-4 border-l-status-warning' : 'border-border-hairline'
      }`}
    >
      <div className="mb-3">
        <button type="button" onClick={onOpenHistory} className="truncate text-left text-sm font-semibold text-ink-primary hover:underline">
          {group.siteName}
        </button>
        <p className="mt-0.5 truncate text-xs text-ink-muted">
          {group.monitorType} · {group.target || '—'}
        </p>
      </div>

      <div className="mb-3 flex flex-col gap-1.5 border-y border-border-hairline py-2.5">
        {group.sites.map((s) => (
          <div key={s.name} className="flex items-center justify-between gap-2 text-xs">
            <span className="truncate text-ink-secondary">{instanceLabel(s.instance)}</span>
            <div className="flex items-center gap-2 text-ink-muted">
              <span>{s.is_active ? timeAgo(s.last_checked) : 'Paused'}</span>
              <StatusBadge status={s.current_status} />
            </div>
          </div>
        ))}
      </div>

      {error && <p className="mb-2 text-xs text-status-critical">{error}</p>}

      <div className="flex flex-wrap gap-2 text-xs">
        {anyActive ? (
          <ActionButton disabled={busy} onClick={() => run(() => pauseSite({ name: primaryName }))}>
            Pause
          </ActionButton>
        ) : (
          <ActionButton disabled={busy} onClick={() => run(() => resumeSite({ name: primaryName }))}>
            Resume
          </ActionButton>
        )}
        <ActionButton
          disabled={busy}
          tone="critical"
          onClick={() => {
            if (confirm(`Delete "${group.siteName}"? This also deletes it from Uptime Kuma on every connected instance.`)) {
              run(() => deleteSite({ name: primaryName }))
            }
          }}
        >
          Delete
        </ActionButton>
      </div>
    </div>
  )
}

function ActionButton({
  children,
  onClick,
  disabled,
  tone = 'default',
}: {
  children: React.ReactNode
  onClick: () => void
  disabled?: boolean
  tone?: 'default' | 'critical'
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`rounded-full border px-3 py-1 font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
        tone === 'critical'
          ? 'border-status-critical/30 text-status-critical hover:bg-status-critical/10'
          : 'border-border-hairline text-ink-secondary hover:bg-ink-primary/5'
      }`}
    >
      {children}
    </button>
  )
}

function AddSiteModal({
  instances,
  defaultCheckIntervalSeconds,
  onClose,
}: {
  instances: UptimeKumaInstance[]
  defaultCheckIntervalSeconds: number
  onClose: () => void
}) {
  const { call: addSite, loading } = useFrappePostCall<{ created: string[]; errors: string[] }>(
    'proxmox_monitor.uptime_monitor.api.add_site',
  )
  const [siteName, setSiteName] = useState('')
  const [monitorType, setMonitorType] = useState<UptimeMonitorType>('HTTP(s)')
  const [url, setUrl] = useState('')
  const [hostname, setHostname] = useState('')
  const [port, setPort] = useState('')
  const [checkInterval, setCheckInterval] = useState(String(defaultCheckIntervalSeconds))
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<{ created: string[]; errors: string[] } | null>(null)

  const submit = async () => {
    setError(null)
    try {
      const res = await addSite({
        site_name: siteName,
        monitor_type: monitorType,
        url: monitorType === 'HTTP(s)' ? url : undefined,
        hostname: monitorType !== 'HTTP(s)' ? hostname : undefined,
        port: monitorType === 'TCP' ? Number(port) : undefined,
        check_interval_seconds: Number(checkInterval) || 60,
      })
      setResult(res)
    } catch (e) {
      setError(getErrorMessage(e))
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()} options={{ title: 'Add Site', size: 'md' }}>
      <div className="flex flex-col gap-4 py-2">
        {!result && (
          <>
            <p className="text-xs text-ink-muted">
              Added to all {instances.length} enabled instance{instances.length === 1 ? '' : 's'} (
              {instances.map((i) => i.instance_name).join(', ')}), so it's monitored from every connected location.
            </p>

            <div>
              <label className={labelClass} htmlFor="add-site-name">Site Name</label>
              <input id="add-site-name" className={inputClass} value={siteName} onChange={(e) => setSiteName(e.target.value)} placeholder="e.g. Support Portal" />
            </div>

            <div>
              <label className={labelClass} htmlFor="add-site-type">Monitor Type</label>
              <select id="add-site-type" className={inputClass} value={monitorType} onChange={(e) => setMonitorType(e.target.value as UptimeMonitorType)}>
                {MONITOR_TYPES.map((t) => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
            </div>

            {monitorType === 'HTTP(s)' ? (
              <div>
                <label className={labelClass} htmlFor="add-site-url">URL</label>
                <input id="add-site-url" className={inputClass} value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://example.com" />
              </div>
            ) : (
              <div className="grid grid-cols-2 gap-4">
                <div className={monitorType === 'Ping' ? 'col-span-2' : ''}>
                  <label className={labelClass} htmlFor="add-site-hostname">Hostname / IP</label>
                  <input id="add-site-hostname" className={inputClass} value={hostname} onChange={(e) => setHostname(e.target.value)} placeholder="10.0.0.5" />
                </div>
                {monitorType === 'TCP' && (
                  <div>
                    <label className={labelClass} htmlFor="add-site-port">Port</label>
                    <input id="add-site-port" type="number" className={inputClass} value={port} onChange={(e) => setPort(e.target.value)} placeholder="443" />
                  </div>
                )}
              </div>
            )}

            <div>
              <label className={labelClass} htmlFor="add-site-interval">Check Interval (seconds)</label>
              <input id="add-site-interval" type="number" className={inputClass} value={checkInterval} onChange={(e) => setCheckInterval(e.target.value)} />
            </div>
          </>
        )}

        {result && (
          <div className="rounded-lg bg-ink-primary/5 p-3 text-xs text-ink-secondary">
            <p>Added "{siteName}" to {result.created.length} of {instances.length} instance{instances.length === 1 ? '' : 's'}.</p>
            {result.errors.length > 0 && (
              <ul className="mt-1 list-disc pl-4 text-status-critical">
                {result.errors.map((e) => <li key={e}>{e}</li>)}
              </ul>
            )}
          </div>
        )}

        {error && <p className="text-sm text-status-critical">{error}</p>}

        <div className="mt-2 flex justify-end gap-3">
          <button type="button" onClick={onClose} className="rounded-lg px-4 py-2 text-sm text-ink-secondary hover:bg-ink-primary/5">
            {result ? 'Close' : 'Cancel'}
          </button>
          {!result && (
            <button
              type="button"
              onClick={submit}
              disabled={loading || !siteName || (monitorType === 'HTTP(s)' ? !url : !hostname)}
              className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
            >
              {loading ? 'Adding…' : 'Add Site'}
            </button>
          )}
        </div>
      </div>
    </Dialog>
  )
}

/** Picks up monitors that already exist on a Kuma instance but have no
 * matching local Uptime Site — the case where an instance had sites added
 * directly in Kuma before it was ever connected here. Nothing is created
 * on Kuma's side; this only mirrors what's already there into our own doc. */
function ImportMonitorsModal({
  instances,
  onClose,
}: {
  instances: UptimeKumaInstance[]
  onClose: () => void
}) {
  const { call: importMonitors, loading } = useFrappePostCall<{ created: string[]; skipped: string[] }>(
    'proxmox_monitor.uptime_monitor.api.import_monitors',
  )
  const [instance, setInstance] = useState(instances[0]?.name ?? '')
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<{ created: string[]; skipped: string[] } | null>(null)

  const { data: monitors, isLoading, mutate: mutateMonitors } = useImportableMonitors(instance)
  const allMonitors = monitors ?? []

  const changeInstance = (next: string) => {
    setInstance(next)
    setSelected(new Set())
    setResult(null)
    setError(null)
  }

  const toggle = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const toggleAll = () => {
    setSelected((prev) => (prev.size === allMonitors.length ? new Set() : new Set(allMonitors.map((m) => m.kuma_monitor_id))))
  }

  const submit = async () => {
    setError(null)
    try {
      const res = await importMonitors({ instance, kuma_monitor_ids: Array.from(selected) })
      setResult(res)
      setSelected(new Set())
      mutateMonitors()
    } catch (e) {
      setError(getErrorMessage(e))
    }
  }

  const target = (m: ImportableMonitor) => (m.monitor_type === 'HTTP(s)' ? m.url : m.monitor_type === 'TCP' ? `${m.hostname}:${m.port}` : m.hostname)

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()} options={{ title: 'Import Existing Monitors', size: 'lg' }}>
      <div className="flex flex-col gap-4 py-2">
        <div>
          <label className={labelClass} htmlFor="import-instance">Instance</label>
          <select id="import-instance" className={inputClass} value={instance} onChange={(e) => changeInstance(e.target.value)}>
            {instances.map((i) => (
              <option key={i.name} value={i.name}>{i.instance_name}</option>
            ))}
          </select>
        </div>

        {isLoading ? (
          <p className="py-6 text-center text-sm text-ink-muted">Loading monitors from Kuma…</p>
        ) : allMonitors.length === 0 ? (
          <p className="py-6 text-center text-sm text-ink-muted">
            Nothing to import — every HTTP(s)/TCP/Ping monitor on this instance is already added here.
          </p>
        ) : (
          <>
            <div className="flex items-center justify-between text-xs text-ink-muted">
              <span>{allMonitors.length} monitor{allMonitors.length === 1 ? '' : 's'} found on Kuma, not yet added here</span>
              <button type="button" onClick={toggleAll} className="font-medium text-accent hover:underline">
                {selected.size === allMonitors.length ? 'Deselect all' : 'Select all'}
              </button>
            </div>
            <div className="max-h-72 overflow-y-auto rounded-lg border border-border-hairline">
              {allMonitors.map((m) => (
                <label
                  key={m.kuma_monitor_id}
                  className="flex cursor-pointer items-center gap-3 border-b border-border-hairline px-3 py-2.5 last:border-0 hover:bg-ink-primary/5"
                >
                  <input
                    type="checkbox"
                    checked={selected.has(m.kuma_monitor_id)}
                    onChange={() => toggle(m.kuma_monitor_id)}
                    className="h-4 w-4 accent-accent"
                  />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm text-ink-primary">{m.site_name}</p>
                    <p className="truncate text-xs text-ink-muted">{m.monitor_type} · {target(m) || '—'}</p>
                  </div>
                </label>
              ))}
            </div>
          </>
        )}

        {result && (
          <div className="rounded-lg bg-ink-primary/5 p-3 text-xs text-ink-secondary">
            {result.created.length > 0 && <p>Imported {result.created.length} monitor{result.created.length === 1 ? '' : 's'}.</p>}
            {result.skipped.length > 0 && (
              <ul className="mt-1 list-disc pl-4 text-ink-muted">
                {result.skipped.map((s) => <li key={s}>{s}</li>)}
              </ul>
            )}
          </div>
        )}

        {error && <p className="text-sm text-status-critical">{error}</p>}

        <div className="mt-2 flex justify-end gap-3">
          <button type="button" onClick={onClose} className="rounded-lg px-4 py-2 text-sm text-ink-secondary hover:bg-ink-primary/5">
            {result ? 'Close' : 'Cancel'}
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={loading || selected.size === 0}
            className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
          >
            {loading ? 'Importing…' : `Import ${selected.size || ''}`.trim()}
          </button>
        </div>
      </div>
    </Dialog>
  )
}

function SiteHistoryDialog({
  group,
  instanceLabel,
  onClose,
}: {
  group: SiteGroup
  instanceLabel: (instance: string) => string
  onClose: () => void
}) {
  const [days, setDays] = useState(7)
  const [selectedSite, setSelectedSite] = useState(group.sites[0])
  const { data: summary } = useUptimeSummary(selectedSite.name, days)
  const { data: history } = useUptimeHistory(selectedSite.name, days)

  const points = history ?? []
  const x = points.map((p) => Math.floor(new Date(p.bucket.replace(' ', 'T')).getTime() / 1000))
  const uptimeSeries = points.map((p) => p.uptime_percent)

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()} options={{ title: group.siteName, size: '2xl' }}>
      <div className="flex flex-col gap-5 py-2">
        {group.sites.length > 1 && (
          <Tabs
            options={group.sites.map((s) => instanceLabel(s.instance))}
            value={instanceLabel(selectedSite.instance)}
            onChange={(label) => {
              const next = group.sites.find((s) => instanceLabel(s.instance) === label)
              if (next) setSelectedSite(next)
            }}
          />
        )}

        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex gap-6 text-sm">
            <div>
              <div className="text-lg font-semibold text-ink-primary">
                {summary?.uptime_percent !== undefined && summary?.uptime_percent !== null ? `${summary.uptime_percent}%` : '—'}
              </div>
              <div className="text-xs uppercase tracking-wide text-ink-muted">Uptime</div>
            </div>
            <div>
              <div className="text-lg font-semibold text-ink-primary">
                {summary ? `${Math.round(summary.avg_response_time_ms)}ms` : '—'}
              </div>
              <div className="text-xs uppercase tracking-wide text-ink-muted">Avg Response</div>
            </div>
          </div>
          <div className="flex gap-1 text-xs">
            {[7, 30, 90].map((d) => (
              <button
                key={d}
                type="button"
                onClick={() => setDays(d)}
                className={`rounded-full px-3 py-1 ${days === d ? 'bg-accent text-white' : 'text-ink-secondary hover:bg-ink-primary/5'}`}
              >
                {d}d
              </button>
            ))}
          </div>
        </div>

        <div className="rounded-xl border border-gridline p-3">
          {x.length === 0 ? (
            <p className="py-10 text-center text-sm text-ink-muted">No history yet for this range.</p>
          ) : (
            <TrendChart
              x={x}
              series={[{ label: 'Uptime %', data: uptimeSeries, colorVar: '--color-status-good', fill: true }]}
              valueFormatter={(v) => `${v}%`}
            />
          )}
        </div>
      </div>
    </Dialog>
  )
}
