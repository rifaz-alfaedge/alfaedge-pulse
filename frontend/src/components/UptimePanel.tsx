import { useState } from 'react'
import { Dialog } from '@rtcamp/frappe-ui-react'
import { useFrappePostCall } from 'frappe-react-sdk'
import { useUptimeHistory, useUptimeInstances, useUptimeSites, useUptimeSummary } from '../lib/hooks'
import type { UptimeKumaInstance, UptimeMonitorType, UptimeSite } from '../lib/types'
import { timeAgo } from '../lib/format'
import { HeartbeatDot } from './HeartbeatDot'
import { StatusBadge } from './StatusBadge'
import { TrendChart } from './TrendChart'

const MONITOR_TYPES: UptimeMonitorType[] = ['HTTP(s)', 'TCP', 'Ping']

const inputClass =
  'w-full rounded-lg border border-border-hairline bg-surface-page px-3 py-2 text-sm text-ink-primary outline-none focus:border-accent'
const labelClass = 'mb-1.5 block text-xs font-medium uppercase tracking-wide text-ink-muted'

/** Multi-instance Uptime Kuma monitoring: add/manage sites across every
 * connected instance from one dashboard, and see the >50%-of-last-N-checks
 * alert rule's live effect (see tasks/uptime_kuma_poller.py) without
 * needing to open Kuma itself. */
export function UptimePanel() {
  const { data: instances, mutate: mutateInstances } = useUptimeInstances()
  const { data: sites, mutate: mutateSites } = useUptimeSites()
  const [showAddModal, setShowAddModal] = useState(false)
  const [historySite, setHistorySite] = useState<UptimeSite | null>(null)

  const allInstances = instances ?? []
  const allSites = sites ?? []
  const instanceLabel = (name: string) => allInstances.find((i) => i.name === name)?.instance_name ?? name

  const criticalSites = allSites.filter((s) => s.is_critical)
  const downOnlySites = allSites.filter((s) => s.current_status === 'Down' && !s.is_critical)

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
          allInstances.map((instance) => <InstanceChip key={instance.name} instance={instance} />)
        )}
      </div>

      {(criticalSites.length > 0 || downOnlySites.length > 0) && (
        <div className="mb-8 grid grid-cols-1 gap-6 lg:grid-cols-2">
          <SeverityCard
            title="Critical Sites"
            icon="⛔"
            accentClass="border-l-4 border-l-status-critical"
            sites={criticalSites}
            instanceLabel={instanceLabel}
            emptyMessage="No critical sites 🎉"
          />
          <SeverityCard
            title="Down (not yet critical)"
            icon="⚠️"
            accentClass="border-l-4 border-l-status-warning"
            sites={downOnlySites}
            instanceLabel={instanceLabel}
            emptyMessage="No sites currently down"
          />
        </div>
      )}

      <div className="mb-5 flex items-center justify-between gap-4">
        <h2 className="text-sm font-semibold uppercase tracking-widest text-ink-muted">Monitored Sites</h2>
        <button
          type="button"
          onClick={() => setShowAddModal(true)}
          disabled={allInstances.length === 0}
          className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
        >
          + Add Site
        </button>
      </div>

      {allSites.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-border-hairline p-10 text-center text-sm text-ink-muted">
          No sites added yet.
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 xl:grid-cols-3">
          {allSites.map((site) => (
            <SiteCard
              key={site.name}
              site={site}
              instanceLabel={instanceLabel(site.instance)}
              onOpenHistory={() => setHistorySite(site)}
              onChanged={refresh}
            />
          ))}
        </div>
      )}

      {showAddModal && (
        <AddSiteModal
          instances={allInstances}
          onClose={() => setShowAddModal(false)}
          onAdded={() => {
            setShowAddModal(false)
            refresh()
          }}
        />
      )}

      {historySite && <SiteHistoryDialog site={historySite} onClose={() => setHistorySite(null)} />}
    </div>
  )
}

function InstanceChip({ instance }: { instance: UptimeKumaInstance }) {
  return (
    <div className="flex items-center gap-2 rounded-full border border-border-hairline bg-surface-card px-4 py-2 text-sm">
      <HeartbeatDot lastSynced={instance.last_synced} pollIntervalSeconds={60} critical={!!instance.last_error} />
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

function SeverityCard({
  title,
  icon,
  accentClass,
  sites,
  instanceLabel,
  emptyMessage,
}: {
  title: string
  icon: string
  accentClass: string
  sites: UptimeSite[]
  instanceLabel: (instance: string) => string
  emptyMessage: string
}) {
  return (
    <div className={`rounded-2xl border border-border-hairline bg-surface-card p-6 shadow-sm ${accentClass}`}>
      <h3 className="mb-4 flex items-center gap-2 text-sm font-semibold uppercase tracking-widest text-ink-muted">
        <span aria-hidden>{icon}</span>
        {title} ({sites.length})
      </h3>
      {sites.length === 0 ? (
        <p className="text-sm text-ink-muted">{emptyMessage}</p>
      ) : (
        <ul className="max-h-64 space-y-3 overflow-y-auto">
          {sites.map((site) => (
            <li key={site.name} className="border-b border-border-hairline pb-3 last:border-0 last:pb-0">
              <p className="truncate text-sm font-medium text-ink-primary">{site.site_name}</p>
              <p className="mt-0.5 text-xs text-ink-secondary">
                {instanceLabel(site.instance)} · {site.url || site.hostname || '—'}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function SiteCard({
  site,
  instanceLabel,
  onOpenHistory,
  onChanged,
}: {
  site: UptimeSite
  instanceLabel: string
  onOpenHistory: () => void
  onChanged: () => void
}) {
  const { call: pauseSite, loading: pausing } = useFrappePostCall('proxmox_monitor.uptime_monitor.api.pause_site')
  const { call: resumeSite, loading: resuming } = useFrappePostCall('proxmox_monitor.uptime_monitor.api.resume_site')
  const { call: deleteSite, loading: deleting } = useFrappePostCall('proxmox_monitor.uptime_monitor.api.delete_site')
  const [error, setError] = useState<string | null>(null)
  const busy = pausing || resuming || deleting

  const target = site.monitor_type === 'HTTP(s)' ? site.url : site.monitor_type === 'TCP' ? `${site.hostname}:${site.port}` : site.hostname

  const run = async (fn: () => Promise<unknown>) => {
    setError(null)
    try {
      await fn()
      onChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div
      className={`rounded-2xl border bg-surface-card p-5 shadow-sm ${
        site.is_critical ? 'border-l-4 border-l-status-critical' : site.current_status === 'Down' ? 'border-l-4 border-l-status-warning' : 'border-border-hairline'
      }`}
    >
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <button type="button" onClick={onOpenHistory} className="truncate text-left text-sm font-semibold text-ink-primary hover:underline">
            {site.site_name}
          </button>
          <p className="mt-0.5 truncate text-xs text-ink-muted">
            {instanceLabel} · {site.monitor_type} · {target || '—'}
          </p>
        </div>
        <StatusBadge status={site.current_status} />
      </div>

      <div className="mb-3 flex items-center justify-between text-xs text-ink-muted">
        <span>Checked {timeAgo(site.last_checked)}</span>
        <span>{site.is_active ? 'Active' : 'Paused'}</span>
      </div>

      {error && <p className="mb-2 text-xs text-status-critical">{error}</p>}

      <div className="flex flex-wrap gap-2 text-xs">
        {site.is_active ? (
          <ActionButton disabled={busy} onClick={() => run(() => pauseSite({ name: site.name }))}>
            Pause
          </ActionButton>
        ) : (
          <ActionButton disabled={busy} onClick={() => run(() => resumeSite({ name: site.name }))}>
            Resume
          </ActionButton>
        )}
        <ActionButton
          disabled={busy}
          tone="critical"
          onClick={() => {
            if (confirm(`Delete "${site.site_name}"? This also deletes it from Uptime Kuma.`)) {
              run(() => deleteSite({ name: site.name }))
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
  onClose,
  onAdded,
}: {
  instances: UptimeKumaInstance[]
  onClose: () => void
  onAdded: () => void
}) {
  const { call: addSite, loading } = useFrappePostCall('proxmox_monitor.uptime_monitor.api.add_site')
  const [instance, setInstance] = useState(instances[0]?.name ?? '')
  const [siteName, setSiteName] = useState('')
  const [monitorType, setMonitorType] = useState<UptimeMonitorType>('HTTP(s)')
  const [url, setUrl] = useState('')
  const [hostname, setHostname] = useState('')
  const [port, setPort] = useState('')
  const [checkInterval, setCheckInterval] = useState('60')
  const [error, setError] = useState<string | null>(null)

  const submit = async () => {
    setError(null)
    try {
      await addSite({
        instance,
        site_name: siteName,
        monitor_type: monitorType,
        url: monitorType === 'HTTP(s)' ? url : undefined,
        hostname: monitorType !== 'HTTP(s)' ? hostname : undefined,
        port: monitorType === 'TCP' ? Number(port) : undefined,
        check_interval_seconds: Number(checkInterval) || 60,
      })
      onAdded()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()} options={{ title: 'Add Site', size: 'md' }}>
      <div className="flex flex-col gap-4 py-2">
        <div>
          <label className={labelClass} htmlFor="add-site-instance">Instance</label>
          <select id="add-site-instance" className={inputClass} value={instance} onChange={(e) => setInstance(e.target.value)}>
            {instances.map((i) => (
              <option key={i.name} value={i.name}>{i.instance_name}</option>
            ))}
          </select>
        </div>

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

        {error && <p className="text-sm text-status-critical">{error}</p>}

        <div className="mt-2 flex justify-end gap-3">
          <button type="button" onClick={onClose} className="rounded-lg px-4 py-2 text-sm text-ink-secondary hover:bg-ink-primary/5">
            Cancel
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={loading || !instance || !siteName || (monitorType === 'HTTP(s)' ? !url : !hostname)}
            className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
          >
            {loading ? 'Adding…' : 'Add Site'}
          </button>
        </div>
      </div>
    </Dialog>
  )
}

function SiteHistoryDialog({ site, onClose }: { site: UptimeSite; onClose: () => void }) {
  const [days, setDays] = useState(7)
  const { data: summary } = useUptimeSummary(site.name, days)
  const { data: history } = useUptimeHistory(site.name, days)

  const points = history ?? []
  const x = points.map((p) => Math.floor(new Date(p.bucket.replace(' ', 'T')).getTime() / 1000))
  const uptimeSeries = points.map((p) => p.uptime_percent)

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()} options={{ title: site.site_name, size: '2xl' }}>
      <div className="flex flex-col gap-5 py-2">
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
