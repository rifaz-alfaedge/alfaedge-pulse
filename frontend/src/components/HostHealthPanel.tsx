import { useMemo, useState } from 'react'
import { Dialog } from '@rtcamp/frappe-ui-react'
import { useFrappePostCall } from 'frappe-react-sdk'
import {
  useFailedJobGroups,
  useFrappeActiveJobs,
  useFrappeFailedJobLogs,
  useFrappeWorkerHealthLogs,
  useHostHealthTrend,
  useHostOverview,
  useMonitoredHostSites,
  useMonitoredHosts,
  useServiceStatusLogs,
  useWorkerHealthHistory,
} from '../lib/hooks'
import type {
  FrappeActiveJob,
  FrappeFailedJobLog,
  FrappeWorkerHealthLog,
  HostOverview,
  HostOverviewMount,
  HostOverviewRow,
  MonitoredHost,
  MonitoredHostSite,
  ServiceStatusLog,
} from '../lib/types'
import { getErrorMessage } from '../lib/errors'
import { formatUptime, timeAgo } from '../lib/format'
import { MultiSelectFilter } from './MultiSelectFilter'
import { SearchInput } from './SearchInput'
import { StatusBadge } from './StatusBadge'
import { Tabs } from './Tabs'
import { TrendChart } from './TrendChart'
import {
  AttentionList,
  Card,
  Empty,
  Pill,
  Segmented,
  SortHeader,
  StatTile,
  UsageBar,
  type AttentionItem,
  type Tone,
} from './ui'
import { compareValues, nextSort, type SortDirection } from '../lib/sort'

/** Bench Health trend-chart window presets, in minutes — 6h default: wide
 * enough to see a real trend, narrow enough that a ~20-30s agent push
 * interval still renders as a readable line rather than a wall of points. */
const TREND_RANGE_OPTIONS: { label: string; minutes: number }[] = [
  { label: '1h', minutes: 60 },
  { label: '6h', minutes: 360 },
  { label: '24h', minutes: 1440 },
  { label: '7d', minutes: 10080 },
]

const FLEET_TREND_OPTIONS = [
  { label: '24h', value: 24 },
  { label: '3d', value: 72 },
  { label: '7d', value: 168 },
]

const SUB_TABS = ['Hosts', 'Services', 'Failed jobs', 'Disks & forecast'] as const
type SubTab = (typeof SUB_TABS)[number]
const STATUS_OPTIONS = ['Online', 'Offline', 'Needs attention', 'Healthy']

/** A disk forecast this close to full is worth flagging on its own. */
const DISK_FULL_SOON_DAYS = 14

function parseQueueDepths(json?: string): Record<string, number> {
  if (!json) return {}
  try {
    return JSON.parse(json) as Record<string, number>
  } catch {
    return {}
  }
}

function toUnixSeconds(value: string): number {
  return Math.floor(new Date(value.replace(' ', 'T')).getTime() / 1000)
}

function toneFor(value: number | null | undefined, warning: number, critical: number): Tone {
  if (value === null || value === undefined) return 'muted'
  return value >= critical ? 'critical' : value >= warning ? 'warning' : 'ok'
}

interface Issue {
  tone: 'critical' | 'warning'
  text: string
  weight: number
}

interface AssessedHost {
  row: HostOverviewRow
  issues: Issue[]
  score: number
  load: number | null
  swap: number | null
  fullest?: HostOverviewMount
  loadTone: Tone
  swapTone: Tone
  diskTone: Tone
}

/** Everything wrong with one host, most serious first, plus a score that
 * orders the table and the Needs attention list. Uses the watchdog's own
 * online/scheduler flags and the alert thresholds from settings, so this
 * agrees with what gets alerted. */
function assess(row: HostOverviewRow, t: HostOverview['thresholds']): AssessedHost {
  const issues: Issue[] = []
  const load = row.metrics?.load_normalized_percent ?? null
  const swap = row.metrics?.swap_usage_percent ?? null
  const fullest = row.mounts[0]
  const loadTone = row.is_online ? toneFor(load, t.load_warning, t.load_critical) : 'muted'
  const swapTone = row.is_online ? toneFor(swap, t.swap_warning, t.swap_critical) : 'muted'
  const diskTone = toneFor(fullest?.usage_percent, t.disk_warning, t.disk_critical)

  if (!row.is_online) {
    issues.push({ tone: 'critical', weight: 1000, text: `Offline — no agent report since ${timeAgo(row.last_seen)}` })
  } else {
    const down = row.services.filter((s) => s.is_down).map((s) => s.service_name)
    if (down.length) issues.push({ tone: 'critical', weight: 500, text: `${down.join(', ')} ${down.length === 1 ? 'is' : 'are'} ${row.services.find((s) => s.is_down)?.current_state || 'down'}` })
    if (row.scheduler_overdue) issues.push({ tone: 'critical', weight: 300, text: `Scheduler stalled — last ran ${timeAgo(row.scheduler_last_run)}` })
    if (row.workers && row.workers.live_worker_count < row.workers.registered_workers)
      issues.push({ tone: 'warning', weight: 120, text: `${row.workers.live_worker_count}/${row.workers.registered_workers} workers live` })
    if (loadTone === 'critical') issues.push({ tone: 'warning', weight: 60, text: `Load ${Math.round(load ?? 0)}% of CPU cores` })
    if (swapTone === 'critical') issues.push({ tone: 'warning', weight: 50, text: `Swap ${Math.round(swap ?? 0)}% used` })
  }
  if (row.open_failed_jobs >= t.failed_job_critical)
    issues.push({ tone: 'warning', weight: 100 + Math.min(row.open_failed_jobs, 99), text: `${row.open_failed_jobs} open failed jobs` })
  if (fullest && diskTone !== 'ok' && diskTone !== 'muted')
    issues.push({ tone: diskTone, weight: diskTone === 'critical' ? 400 : 80, text: `Disk ${fullest.mount_point} ${Math.round(fullest.usage_percent ?? 0)}% full` })
  for (const m of row.mounts) {
    const days = m.forecast.days_remaining
    if (days !== null && days <= DISK_FULL_SOON_DAYS)
      issues.push({ tone: 'warning', weight: 90, text: `${m.mount_point} full in ~${Math.max(0, Math.round(days))} days at current growth` })
  }
  issues.sort((a, b) => b.weight - a.weight)
  return { row, issues, score: issues.reduce((s, i) => s + i.weight, 0), load, swap, fullest, loadTone, swapTone, diskTone }
}

type HostSortField = 'attention' | 'hostname' | 'status' | 'services' | 'scheduler' | 'failed' | 'load' | 'disk' | 'swap'

function hostSortValue(h: AssessedHost, field: HostSortField): unknown {
  switch (field) {
    case 'attention':
      return h.score
    case 'hostname':
      return h.row.hostname
    case 'status':
      return h.row.is_online
    case 'services':
      return h.row.services_down
    case 'scheduler':
      return h.row.scheduler_overdue
    case 'failed':
      return h.row.open_failed_jobs
    case 'load':
      return h.load
    case 'disk':
      return h.fullest?.usage_percent
    case 'swap':
      return h.swap
  }
}

/** Host Health tab: filters, stat tiles, a fleet trend beside a ranked
 * Needs attention list, then a sortable table (with Services, Failed jobs
 * and Disks views) and a side panel for the selected host. The full
 * per-host drill-down (bench history, active jobs, failed-job root causes,
 * log download) opens from the side panel. */
export function HostHealthPanel() {
  const { data: overview, error } = useHostOverview()
  const [trendHours, setTrendHours] = useState(24)
  const { data: trend } = useHostHealthTrend(trendHours)
  const [roles, setRoles] = useState<string[]>([])
  const [statuses, setStatuses] = useState<string[]>([])
  const [servers, setServers] = useState<string[]>([])
  const [search, setSearch] = useState('')
  const [subTab, setSubTab] = useState<SubTab>('Hosts')
  const [sort, setSort] = useState<{ field: HostSortField; dir: SortDirection }>({ field: 'attention', dir: 'desc' })
  const [selected, setSelected] = useState<string | null>(null)
  const [detailHost, setDetailHost] = useState<string | null>(null)

  const thresholds = overview?.thresholds
  const assessed = useMemo(
    () => (overview && thresholds ? overview.hosts.map((h) => assess(h, thresholds)) : []),
    [overview, thresholds],
  )

  const roleOptions = [...new Set(assessed.map((h) => h.row.role).filter(Boolean) as string[])].sort()
  const serverOptions = [...new Set(assessed.map((h) => h.row.server).filter(Boolean) as string[])].sort()

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase()
    return assessed.filter((h) => {
      if (roles.length && !roles.includes(h.row.role ?? '')) return false
      if (servers.length && !servers.includes(h.row.server ?? '')) return false
      if (statuses.length) {
        const tags = [h.row.is_online ? 'Online' : 'Offline', h.issues.length ? 'Needs attention' : 'Healthy']
        if (!statuses.some((s) => tags.includes(s))) return false
      }
      if (needle) {
        const hay = [h.row.hostname, ...h.row.sites.map((s) => `${s.site_name} ${s.site_url ?? ''}`), ...h.row.services.map((s) => s.service_name)]
          .join(' ')
          .toLowerCase()
        if (!hay.includes(needle)) return false
      }
      return true
    })
  }, [assessed, roles, servers, statuses, search])

  const sorted = useMemo(
    () =>
      [...filtered].sort(
        (a, b) => compareValues(hostSortValue(a, sort.field), hostSortValue(b, sort.field), sort.dir) || a.row.hostname.localeCompare(b.row.hostname),
      ),
    [filtered, sort],
  )
  const byAttention = useMemo(() => [...filtered].sort((a, b) => b.score - a.score), [filtered])
  const current = sorted.find((h) => h.row.name === selected) ?? byAttention[0]

  if (error) {
    return (
      <div className="rounded-xl border border-status-critical/30 bg-status-critical/10 px-4 py-3 text-sm text-status-critical">
        {getErrorMessage(error)}
      </div>
    )
  }
  if (overview && overview.hosts.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-border-hairline p-10 text-center text-sm text-ink-muted">
        No hosts being monitored yet — add a <em>Monitored Host</em> from Desk and point its push agent at this site to get started.
      </div>
    )
  }

  const online = filtered.filter((h) => h.row.is_online)
  const offline = filtered.filter((h) => !h.row.is_online)
  const servicesDown = online.flatMap((h) => h.row.services.filter((s) => s.is_down).map((s) => ({ host: h.row.hostname, service: s.service_name })))
  const stalled = online.filter((h) => h.row.scheduler_overdue)
  const failedTotal = filtered.reduce((s, h) => s + h.row.open_failed_jobs, 0)
  const failedHosts = filtered.filter((h) => h.row.open_failed_jobs > 0).length
  const highLoad = online.filter((h) => h.loadTone === 'warning' || h.loadTone === 'critical')
  const highestLoad = [...online].sort((a, b) => (b.load ?? 0) - (a.load ?? 0))[0]
  const fullestDisk = [...filtered].filter((h) => h.fullest).sort((a, b) => (b.fullest?.usage_percent ?? 0) - (a.fullest?.usage_percent ?? 0))[0]

  const attention: AttentionItem[] = byAttention
    .filter((h) => h.issues.length)
    .slice(0, 8)
    .map((h) => ({
      key: h.row.name,
      title: h.row.hostname,
      text: h.issues[0].text + (h.issues.length > 1 ? ` · +${h.issues.length - 1} more` : ''),
      tone: h.issues[0].tone,
      onClick: () => {
        setSelected(h.row.name)
        setSubTab('Hosts')
      },
    }))

  const trendPoints = trend ?? []
  const lastReport = assessed.map((h) => h.row.last_seen).filter(Boolean).sort().at(-1)
  const sortHeader = (label: string, field: HostSortField, align: 'left' | 'right' = 'left') => (
    <SortHeader label={label} field={field} sortField={sort.field} sortDirection={sort.dir} onSort={(f) => setSort(nextSort(sort, f))} align={align} />
  )

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <span className="flex items-center gap-2 text-sm text-ink-muted">
          <span aria-hidden="true" className={`h-2 w-2 rounded-full ${online.length ? 'bg-status-good' : 'bg-ink-muted'}`} />
          Agent push · {assessed.length} host{assessed.length === 1 ? '' : 's'}
          {lastReport ? ` · last report ${timeAgo(lastReport)}` : ''}
        </span>
        <a
          href="/app/host-monitor-settings"
          className="rounded-lg border border-border-hairline bg-surface-card px-3 py-1.5 text-xs font-medium text-ink-primary hover:border-accent"
        >
          Settings
        </a>
      </div>

      <div className="mb-6 flex flex-wrap items-center gap-2">
        <MultiSelectFilter label="Role" options={roleOptions} selected={roles} onChange={setRoles} />
        <MultiSelectFilter label="Status" options={STATUS_OPTIONS} selected={statuses} onChange={setStatuses} />
        <MultiSelectFilter label="Proxmox server" options={serverOptions} selected={servers} onChange={setServers} />
        <SearchInput value={search} onChange={setSearch} placeholder="Search host, site or service…" />
      </div>

      <div className="mb-6 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
        <StatTile
          label="Hosts online"
          value={overview ? `${online.length} / ${filtered.length}` : undefined}
          sub={offline.length ? `${offline.length} offline` : 'all reporting'}
          tone={offline.length ? 'critical' : 'ok'}
        />
        <StatTile
          label="Services down"
          value={overview ? servicesDown.length : undefined}
          sub={servicesDown.slice(0, 2).map((s) => `${s.service} on ${s.host.split('.')[0]}`).join(', ') || 'none'}
          tone={servicesDown.length ? 'critical' : 'ok'}
        />
        <StatTile
          label="Scheduler stalled"
          value={overview ? stalled.length : undefined}
          sub={stalled.map((h) => h.row.hostname.split('.')[0]).join(', ') || 'none'}
          tone={stalled.length ? 'warning' : 'ok'}
        />
        <StatTile
          label="Open failed jobs"
          value={overview ? failedTotal : undefined}
          sub={`across ${failedHosts} host${failedHosts === 1 ? '' : 's'}`}
          tone={failedTotal ? 'critical' : 'ok'}
        />
        <StatTile
          label={`Load ≥ ${thresholds?.load_warning ?? 100}%`}
          value={overview ? highLoad.length : undefined}
          sub={highestLoad?.load != null ? `highest ${Math.round(highestLoad.load)}% (${highestLoad.row.hostname.split('.')[0]})` : '—'}
          tone={highLoad.length ? 'warning' : 'ok'}
        />
        <StatTile
          label="Fullest disk"
          value={fullestDisk?.fullest?.usage_percent != null ? `${Math.round(fullestDisk.fullest.usage_percent)}%` : undefined}
          sub={fullestDisk ? `${fullestDisk.row.hostname.split('.')[0]} ${fullestDisk.fullest?.mount_point}` : '—'}
          tone={fullestDisk ? fullestDisk.diskTone : 'ok'}
        />
      </div>

      <div className="mb-6 flex flex-wrap gap-4">
        <Card
          title="Fleet load & new failed jobs"
          actions={<Segmented label="Chart range" options={FLEET_TREND_OPTIONS} value={trendHours} onChange={setTrendHours} />}
          className="min-w-0 flex-[2_1_560px]"
        >
          {trendPoints.length === 0 ? (
            <Empty text="No resource or failed-job data in this range." />
          ) : (
            <TrendChart
              x={trendPoints.map((p) => toUnixSeconds(p.bucket))}
              series={[
                { label: 'Avg load % of cores', data: trendPoints.map((p) => p.avg_load_percent), colorVar: '--color-accent' },
                { label: 'New failed jobs', data: trendPoints.map((p) => p.new_failed_jobs), colorVar: '--color-status-critical', fill: true },
              ]}
              valueFormatter={(v) => Math.round(v).toLocaleString()}
            />
          )}
        </Card>
        <Card title={`Needs attention · ${byAttention.filter((h) => h.issues.length).length}`} noPadding className="min-w-0 flex-[1_1_340px]">
          <AttentionList items={attention} emptyText="Nothing needs attention." />
        </Card>
      </div>

      <div className="mb-4">
        <Tabs options={[...SUB_TABS]} value={subTab} onChange={(v) => setSubTab(v as SubTab)} />
      </div>

      <div className="flex flex-wrap items-start gap-4">
        <div className="min-w-0 flex-[999_1_640px]">
          {subTab === 'Hosts' && (
            <Card title={`${sorted.length} host${sorted.length === 1 ? '' : 's'}`} actions={<span className="text-xs text-ink-muted">Click a row for details</span>} noPadding>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[960px] text-sm">
                  <thead className="border-b border-border-hairline text-left">
                    <tr>
                      {sortHeader('Host', 'hostname')}
                      {sortHeader('Status', 'status')}
                      {sortHeader('Services', 'services')}
                      <th scope="col" className="px-3 py-3 text-xs font-medium uppercase tracking-wide text-ink-muted">Workers</th>
                      {sortHeader('Scheduler', 'scheduler')}
                      {sortHeader('Failed jobs', 'failed', 'right')}
                      {sortHeader('Load', 'load')}
                      {sortHeader('Fullest disk', 'disk')}
                      {sortHeader('Swap', 'swap', 'right')}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border-hairline">
                    {sorted.map((h) => (
                      <HostRow key={h.row.name} host={h} selected={current?.row.name === h.row.name} onSelect={() => setSelected(h.row.name)} />
                    ))}
                  </tbody>
                </table>
                {sorted.length === 0 && <Empty text={overview ? 'No hosts match these filters.' : 'Loading…'} />}
              </div>
            </Card>
          )}
          {subTab === 'Services' && <ServicesTable hosts={sorted} onSelect={setSelected} />}
          {subTab === 'Failed jobs' && (
            <FailedJobsTable hosts={sorted} critical={thresholds?.failed_job_critical ?? 20} onSelect={setSelected} onOpen={setDetailHost} />
          )}
          {subTab === 'Disks & forecast' && <DisksTable hosts={sorted} onSelect={setSelected} />}
        </div>

        <div className="min-w-0 flex-[1_1_340px]">
          {current ? <HostSidePanel host={current} onOpenDetails={() => setDetailHost(current.row.name)} /> : null}
        </div>
      </div>

      {detailHost && <HostDetailLoader hostName={detailHost} role={assessed.find((h) => h.row.name === detailHost)?.row.role ?? undefined} onClose={() => setDetailHost(null)} />}
    </div>
  )
}

function HostRow({ host, selected, onSelect }: { host: AssessedHost; selected: boolean; onSelect: () => void }) {
  const r = host.row
  const down = r.services.filter((s) => s.is_down)
  return (
    <tr onClick={onSelect} className={`cursor-pointer ${selected ? 'bg-ink-primary/5' : 'hover:bg-ink-primary/[0.03]'}`}>
      <td className="px-3 py-2.5">
        <button type="button" onClick={onSelect} className="text-left font-semibold text-ink-primary hover:underline">
          {r.hostname}
        </button>
        <div className="text-xs text-ink-muted">{[r.role, r.server].filter(Boolean).join(' · ') || 'No role'}</div>
      </td>
      <td className="px-3 py-2.5 whitespace-nowrap">
        <Pill tone={r.is_online ? 'ok' : 'critical'}>{r.is_online ? 'Online' : 'Offline'}</Pill>
        <div className="mt-0.5 text-xs text-ink-muted">{timeAgo(r.last_seen)}</div>
      </td>
      <td className={`px-3 py-2.5 ${down.length && r.is_online ? 'text-status-critical' : 'text-ink-secondary'}`}>
        {!r.services.length
          ? '—'
          : down.length
            ? `${down.map((s) => s.service_name).join(', ')} ${down[0].current_state}`
            : `${r.services.length}/${r.services.length} active`}
      </td>
      <td className="px-3 py-2.5 tabular-nums text-ink-secondary">
        {r.workers ? `${r.workers.live_worker_count}/${r.workers.registered_workers}` : '—'}
      </td>
      <td className={`px-3 py-2.5 whitespace-nowrap ${r.scheduler_overdue && r.is_online ? 'text-status-critical' : 'text-ink-secondary'}`}>
        {r.scheduler_overdue && r.is_online ? `Stalled · ${timeAgo(r.scheduler_last_run)}` : r.scheduler_last_run ? timeAgo(r.scheduler_last_run) : '—'}
      </td>
      <td className={`px-3 py-2.5 text-right font-semibold tabular-nums ${r.open_failed_jobs ? 'text-status-warning' : 'text-ink-muted'}`}>
        {r.open_failed_jobs}
      </td>
      <td className="w-36 px-3 py-2.5">
        <UsageBar value={host.load} max={300} tone={host.loadTone} />
      </td>
      <td className="w-36 px-3 py-2.5">
        <UsageBar value={host.fullest?.usage_percent} tone={host.diskTone} />
      </td>
      <td className="px-3 py-2.5 text-right tabular-nums text-ink-secondary">{host.swap != null ? `${Math.round(host.swap)}%` : '—'}</td>
    </tr>
  )
}

function ServicesTable({ hosts, onSelect }: { hosts: AssessedHost[]; onSelect: (name: string) => void }) {
  const rows = hosts
    .flatMap((h) => h.row.services.map((s) => ({ host: h.row, s })))
    .sort((a, b) => b.s.is_down - a.s.is_down || a.host.hostname.localeCompare(b.host.hostname) || a.s.service_name.localeCompare(b.s.service_name))
  return (
    <Card title={`${rows.length} services · ${rows.filter((r) => r.s.is_down).length} down`} noPadding>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-sm">
          <thead className="border-b border-border-hairline text-left text-xs uppercase tracking-wide text-ink-muted">
            <tr>
              <th scope="col" className="px-3 py-3 font-medium">Host</th>
              <th scope="col" className="px-3 py-3 font-medium">Service</th>
              <th scope="col" className="px-3 py-3 font-medium">State</th>
              <th scope="col" className="px-3 py-3 font-medium">Since</th>
              <th scope="col" className="px-3 py-3 font-medium">Checked</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border-hairline">
            {rows.map(({ host, s }) => (
              <tr key={`${host.name}-${s.service_name}`} onClick={() => onSelect(host.name)} className="cursor-pointer hover:bg-ink-primary/[0.03]">
                <td className="px-3 py-2.5 font-medium text-ink-primary">{host.hostname}</td>
                <td className="px-3 py-2.5 text-ink-secondary">{s.service_name}</td>
                <td className="px-3 py-2.5">
                  <Pill tone={!host.is_online ? 'muted' : s.is_down ? 'critical' : 'ok'}>{host.is_online ? s.current_state : 'unknown'}</Pill>
                </td>
                <td className="px-3 py-2.5 text-ink-muted">{timeAgo(s.last_state_change)}</td>
                <td className="px-3 py-2.5 text-ink-muted">{timeAgo(s.last_checked)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 && <Empty text="No services reported." />}
      </div>
    </Card>
  )
}

function FailedJobsTable({
  hosts,
  critical,
  onSelect,
  onOpen,
}: {
  hosts: AssessedHost[]
  critical: number
  onSelect: (name: string) => void
  onOpen: (name: string) => void
}) {
  const rows = hosts.filter((h) => h.row.open_failed_jobs > 0).sort((a, b) => b.row.open_failed_jobs - a.row.open_failed_jobs)
  const total = rows.reduce((s, h) => s + h.row.open_failed_jobs, 0)
  return (
    <Card title={`${total} open failed jobs on ${rows.length} host${rows.length === 1 ? '' : 's'}`} noPadding>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[560px] text-sm">
          <thead className="border-b border-border-hairline text-left text-xs uppercase tracking-wide text-ink-muted">
            <tr>
              <th scope="col" className="px-3 py-3 font-medium">Host</th>
              <th scope="col" className="px-3 py-3 text-right font-medium">Open</th>
              <th scope="col" className="px-3 py-3 font-medium">Bench</th>
              <th scope="col" className="px-3 py-3 font-medium"><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border-hairline">
            {rows.map((h) => (
              <tr key={h.row.name} onClick={() => onSelect(h.row.name)} className="cursor-pointer hover:bg-ink-primary/[0.03]">
                <td className="px-3 py-2.5 font-medium text-ink-primary">{h.row.hostname}</td>
                <td className={`px-3 py-2.5 text-right font-semibold tabular-nums ${h.row.open_failed_jobs >= critical ? 'text-status-critical' : 'text-status-warning'}`}>
                  {h.row.open_failed_jobs}
                </td>
                <td className="px-3 py-2.5 text-ink-secondary">
                  {h.row.workers ? `${h.row.workers.bench_name} · ${h.row.workers.live_worker_count}/${h.row.workers.registered_workers} workers` : '—'}
                </td>
                <td className="px-3 py-2.5 text-right">
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation()
                      onOpen(h.row.name)
                    }}
                    className="rounded-lg border border-border-hairline px-2.5 py-1 text-xs text-ink-primary hover:border-accent"
                  >
                    Root causes
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 && <Empty text="No open failed jobs." />}
      </div>
    </Card>
  )
}

function forecastText(m: HostOverviewMount): string {
  const f = m.forecast
  if (f.trend === 'insufficient_data') return 'Not enough data yet'
  if (f.days_remaining === null) return 'Stable or shrinking'
  return `Full in ~${Math.max(0, Math.round(f.days_remaining))} days`
}

function DisksTable({ hosts, onSelect }: { hosts: AssessedHost[]; onSelect: (name: string) => void }) {
  const rows = hosts
    .flatMap((h) => h.row.mounts.map((m) => ({ h, m })))
    .sort((a, b) => (a.m.forecast.days_remaining ?? 1e9) - (b.m.forecast.days_remaining ?? 1e9) || (b.m.usage_percent ?? 0) - (a.m.usage_percent ?? 0))
  return (
    <Card title={`${rows.length} mounts · soonest to fill first`} noPadding>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-sm">
          <thead className="border-b border-border-hairline text-left text-xs uppercase tracking-wide text-ink-muted">
            <tr>
              <th scope="col" className="px-3 py-3 font-medium">Host</th>
              <th scope="col" className="px-3 py-3 font-medium">Mount</th>
              <th scope="col" className="px-3 py-3 font-medium">Used</th>
              <th scope="col" className="w-48 px-3 py-3 font-medium">Usage</th>
              <th scope="col" className="px-3 py-3 font-medium">Forecast</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border-hairline">
            {rows.map(({ h, m }) => {
              const soon = m.forecast.days_remaining !== null && m.forecast.days_remaining <= DISK_FULL_SOON_DAYS
              return (
                <tr key={m.name} onClick={() => onSelect(h.row.name)} className="cursor-pointer hover:bg-ink-primary/[0.03]">
                  <td className="px-3 py-2.5 font-medium text-ink-primary">{h.row.hostname}</td>
                  <td className="px-3 py-2.5 font-mono text-xs text-ink-secondary">{m.mount_point}</td>
                  <td className="px-3 py-2.5 tabular-nums text-ink-secondary">
                    {m.used_gb != null && m.total_gb != null ? `${m.used_gb.toFixed(1)} / ${m.total_gb.toFixed(1)} GB` : '—'}
                  </td>
                  <td className="px-3 py-2.5">
                    <UsageBar value={m.usage_percent} tone={h.row.mounts[0]?.name === m.name ? h.diskTone : 'ok'} />
                  </td>
                  <td className={`px-3 py-2.5 ${soon ? 'font-semibold text-status-warning' : 'text-ink-muted'}`}>{forecastText(m)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {rows.length === 0 && <Empty text="No disk data reported." />}
      </div>
    </Card>
  )
}

function HostSidePanel({ host, onOpenDetails }: { host: AssessedHost; onOpenDetails: () => void }) {
  const r = host.row
  const queues = Object.entries(parseQueueDepths(r.workers?.queue_depths))
  return (
    <aside aria-label="Host details" className="rounded-2xl border border-border-hairline bg-surface-card shadow-sm">
      <div className="border-b border-border-hairline px-5 py-4">
        <div className="flex items-center justify-between gap-2">
          <h3 className="truncate text-base font-bold text-ink-primary">{r.hostname}</h3>
          <Pill tone={r.is_online ? 'ok' : 'critical'}>{r.is_online ? 'Online' : 'Offline'}</Pill>
        </div>
        <p className="mt-1 text-xs text-ink-muted">
          {[r.role, r.server].filter(Boolean).join(' · ')} · last report {timeAgo(r.last_seen)}
        </p>
      </div>

      {host.issues.length > 0 && (
        <ul className="space-y-1.5 border-b border-border-hairline px-5 py-3">
          {host.issues.map((i) => (
            <li key={i.text} className={`text-xs ${i.tone === 'critical' ? 'text-status-critical' : 'text-status-warning'}`}>
              {i.text}
            </li>
          ))}
        </ul>
      )}

      <section className="border-b border-border-hairline px-5 py-4">
        <h4 className="mb-2 text-xs font-semibold uppercase tracking-widest text-ink-muted">Services</h4>
        {r.services.length === 0 ? (
          <p className="text-sm text-ink-muted">No services reported.</p>
        ) : (
          <div className="grid grid-cols-2 gap-2">
            {r.services.map((s) => (
              <div key={s.service_name} className="flex items-center justify-between gap-2 rounded-lg border border-border-hairline px-2.5 py-1.5 text-xs">
                <span className="truncate text-ink-primary">{s.service_name}</span>
                <span className={`font-semibold ${!r.is_online ? 'text-ink-muted' : s.is_down ? 'text-status-critical' : 'text-status-good'}`}>
                  {r.is_online ? s.current_state : 'unknown'}
                </span>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="border-b border-border-hairline px-5 py-4">
        <h4 className="mb-2 text-xs font-semibold uppercase tracking-widest text-ink-muted">Bench{r.workers ? ` · ${r.workers.bench_name}` : ''}</h4>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
          <Fact label="Workers" value={r.workers ? `${r.workers.live_worker_count}/${r.workers.registered_workers} live` : '—'} />
          <Fact label="Queues" value={queues.map(([q, n]) => `${q} ${n}`).join(' · ') || '—'} />
          <Fact
            label="Scheduler"
            value={r.scheduler_overdue && r.is_online ? `Stalled · ${timeAgo(r.scheduler_last_run)}` : r.scheduler_last_run ? timeAgo(r.scheduler_last_run) : '—'}
            tone={r.scheduler_overdue && r.is_online ? 'critical' : undefined}
          />
          <Fact label="Open failed jobs" value={String(r.open_failed_jobs)} tone={r.open_failed_jobs ? 'warning' : undefined} />
          <Fact
            label="Load (of cores)"
            value={host.load != null ? `${Math.round(host.load)}%${r.metrics?.cpu_core_count ? ` · ${r.metrics.cpu_core_count} cores` : ''}` : '—'}
            tone={host.loadTone === 'critical' ? 'critical' : host.loadTone === 'warning' ? 'warning' : undefined}
          />
          <Fact label="Swap" value={host.swap != null ? `${Math.round(host.swap)}%` : '—'} />
        </dl>
      </section>

      {r.mounts.length > 0 && (
        <section className="border-b border-border-hairline px-5 py-4">
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-widest text-ink-muted">Disks</h4>
          <ul className="space-y-2">
            {r.mounts.map((m) => (
              <li key={m.name} className="text-xs">
                <div className="mb-1 flex justify-between gap-2">
                  <span className="font-mono text-ink-secondary">{m.mount_point}</span>
                  <span className="text-ink-muted">{forecastText(m)}</span>
                </div>
                <UsageBar value={m.usage_percent} tone={m.name === r.mounts[0]?.name ? host.diskTone : 'ok'} />
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="border-b border-border-hairline px-5 py-4">
        <h4 className="mb-2 text-xs font-semibold uppercase tracking-widest text-ink-muted">Sites on this host</h4>
        {r.sites.length === 0 ? (
          <p className="text-sm text-ink-muted">No sites reported.</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {r.sites.map((s) => (
              <li key={s.name} className="truncate text-ink-primary">
                {s.site_url ? (
                  <a href={s.site_url} target="_blank" rel="noreferrer" className="hover:underline">
                    {s.site_name}
                  </a>
                ) : (
                  s.site_name
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <div className="flex flex-wrap gap-2 px-5 py-4">
        <button
          type="button"
          onClick={onOpenDetails}
          className="rounded-lg border border-border-hairline bg-ink-primary/5 px-3 py-1.5 text-xs font-medium text-ink-primary hover:border-accent"
        >
          Full details & history
        </button>
        <a
          href={`/app/monitored-host/${encodeURIComponent(r.name)}`}
          className="rounded-lg border border-border-hairline px-3 py-1.5 text-xs font-medium text-ink-primary hover:border-accent"
        >
          Open in Desk
        </a>
      </div>
    </aside>
  )
}

function Fact({ label, value, tone }: { label: string; value: string; tone?: 'critical' | 'warning' }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-ink-muted">{label}</dt>
      <dd className={`truncate font-semibold ${tone === 'critical' ? 'text-status-critical' : tone === 'warning' ? 'text-status-warning' : 'text-ink-primary'}`}>
        {value}
      </dd>
    </div>
  )
}

/** Loads the full per-host data only while the details dialog is open —
 * the table itself runs off the single get_host_overview call. */
function HostDetailLoader({ hostName, role, onClose }: { hostName: string; role?: string; onClose: () => void }) {
  const { data: hosts } = useMonitoredHosts()
  const { data: sites } = useMonitoredHostSites()
  const { data: services } = useServiceStatusLogs()
  const { data: workerLogs } = useFrappeWorkerHealthLogs()
  const { data: failedJobs } = useFrappeFailedJobLogs()
  const { data: activeJobs } = useFrappeActiveJobs()
  const host = (hosts ?? []).find((h) => h.name === hostName)
  if (!host) return null
  return (
    <HostDetailDialog
      host={host}
      role={role}
      services={(services ?? []).filter((s) => s.monitored_host === hostName)}
      workerLogs={(workerLogs ?? []).filter((w) => w.monitored_host === hostName)}
      failedJobs={(failedJobs ?? []).filter((j) => j.monitored_host === hostName)}
      activeJobs={(activeJobs ?? []).filter((j) => j.monitored_host === hostName)}
      sites={(sites ?? []).filter((s) => s.parent === hostName)}
      onClose={onClose}
    />
  )
}

function HostDetailDialog({
  host,
  role,
  services,
  workerLogs,
  failedJobs,
  activeJobs,
  sites,
  onClose,
}: {
  host: MonitoredHost
  role?: string
  services: ServiceStatusLog[]
  workerLogs: FrappeWorkerHealthLog[]
  failedJobs: FrappeFailedJobLog[]
  activeJobs: FrappeActiveJob[]
  sites: MonitoredHostSite[]
  onClose: () => void
}) {
  const benchNames = [...new Set(workerLogs.map((w) => w.bench_name))]
  const [selectedBench, setSelectedBench] = useState(benchNames[0])
  const [rangeMinutes, setRangeMinutes] = useState(360)
  // Scoped to exactly this (host, bench) within the chosen window — unlike
  // the `workerLogs` prop above (a fleet-wide recent-300 cap shared across
  // every host/bench, only reliable for "what's the latest row," not a
  // real range), so picking a longer window here doesn't starve other
  // hosts out of their own share of that shared cap.
  const { data: rangeHistoryData } = useWorkerHealthHistory(host.name, selectedBench ?? '', rangeMinutes)
  const benchHistory = rangeHistoryData ?? []
  const x = benchHistory.map((w) => Math.floor(new Date(w.timestamp.replace(' ', 'T')).getTime() / 1000))
  const orphanSeries = benchHistory.map((w) => w.orphan_worker_count)
  const failedJobSeries = benchHistory.map((w) => w.failed_job_count)
  const latestForBench = benchHistory.at(-1)
  // Longest-running first, matching Frappe Active Job's own default sort —
  // the worst offender on this bench always leads the list.
  const benchActiveJobs = activeJobs
    .filter((j) => j.bench_name === selectedBench)
    .slice()
    .sort((a, b) => b.elapsed_seconds - a.elapsed_seconds)

  const { data: failedJobGroups } = useFailedJobGroups(host.name)

  // useFrappePostCall's `call` actually resolves to the raw
  // `{ message: T }` envelope Frappe wraps every whitelisted return value
  // in (frappe/handler.py: `frappe.response["message"] = data`) — its own
  // type declarations claim `Promise<T>`, but that's not what happens at
  // runtime, confirmed by a real downloaded file containing the literal
  // text "[object Object]" before this was unwrapped.
  const { call: getFailedJobLogText, loading: downloadingLog } = useFrappePostCall<{ message: string }>(
    'alfaedge_pulse.host_health.api.get_failed_job_log_text',
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
            <div className="mb-2 flex flex-wrap items-center justify-between gap-3">
              <h3 className="text-xs font-semibold uppercase tracking-widest text-ink-muted">Bench Health</h3>
              <div className="flex items-center gap-2">
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
                <div className="flex items-center rounded-lg border border-border-hairline p-0.5 text-xs">
                  {TREND_RANGE_OPTIONS.map(({ label, minutes }) => (
                    <button
                      key={minutes}
                      type="button"
                      onClick={() => setRangeMinutes(minutes)}
                      className={`rounded-md px-2 py-1 transition-colors ${
                        rangeMinutes === minutes ? 'bg-accent text-white' : 'text-ink-secondary hover:bg-ink-primary/5'
                      }`}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
            </div>
            {latestForBench && (
              <div className="mb-3 flex flex-wrap gap-6 text-sm">
                <Stat label="Workers" value={`${latestForBench.live_worker_count}/${latestForBench.registered_workers}`} />
                <Stat label="Orphans" value={String(latestForBench.orphan_worker_count)} warn={latestForBench.orphan_worker_count > 0} />
                <Stat label="Failed Jobs" value={String(latestForBench.failed_job_count)} warn={latestForBench.failed_job_count > 0} />
                <Stat label="Active Jobs" value={String(latestForBench.active_job_count)} warn={latestForBench.long_running_job_count > 0} />
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

        {benchActiveJobs.length > 0 && (
          <div>
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-widest text-ink-muted">
              Active Jobs ({benchActiveJobs.length})
            </h3>
            {/* Longest-running first (see benchActiveJobs' own sort) — an
             * eagle-eye view exists precisely to surface the worst offender
             * without having to scan a whole list, so it always leads. */}
            <div className="max-h-64 divide-y divide-border-hairline overflow-y-auto rounded-lg border border-border-hairline">
              {benchActiveJobs.map((j) => (
                <div key={j.name} className="flex items-center justify-between gap-3 px-3 py-2.5 text-xs">
                  <span className="min-w-0">
                    <span className="block truncate font-medium text-ink-primary">{j.job_name || 'Unknown job'}</span>
                    <span className="text-ink-muted">
                      {j.queue_name || 'default'} queue{j.worker_pid ? ` · worker ${j.worker_pid}` : ''}
                    </span>
                  </span>
                  <span className={`shrink-0 font-medium ${j.is_long_running ? 'text-status-critical' : 'text-ink-secondary'}`}>
                    running {formatUptime(j.elapsed_seconds)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {failedJobs.length > 0 && (
          <div>
            <div className="mb-2 flex items-center justify-between gap-3">
              <h3 className="text-xs font-semibold uppercase tracking-widest text-ink-muted">
                Failed Job Root Causes ({(failedJobGroups ?? []).length || failedJobs.length})
              </h3>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={downloadLog}
                  disabled={downloadingLog}
                  className="rounded-lg border border-border-hairline px-2.5 py-1 text-xs text-ink-secondary hover:bg-ink-primary/5 disabled:opacity-50"
                >
                  {downloadingLog ? 'Preparing…' : 'Download Log'}
                </button>
              </div>
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
