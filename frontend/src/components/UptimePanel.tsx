import { useMemo, useState } from 'react'
import { Dialog } from '@rtcamp/frappe-ui-react'
import { useFrappePostCall } from 'frappe-react-sdk'
import {
  useImportableMonitors,
  useUptimeHistory,
  useUptimeInstances,
  useUptimeMonitorSettings,
  useUptimeOverview,
  useUptimeSites,
  useUptimeSummary,
} from '../lib/hooks'
import type {
  ImportableMonitor,
  MaintenanceWindow,
  MaintenanceWindowType,
  StripCell,
  UptimeKumaInstance,
  UptimeMonitorType,
  UptimeOverview,
  UptimeOverviewRow,
} from '../lib/types'
import { type SiteGroup, groupSites } from '../lib/uptimeGroups'
import { timeAgo } from '../lib/format'
import { getErrorMessage } from '../lib/errors'
import { MultiSelectFilter } from './MultiSelectFilter'
import { SearchInput } from './SearchInput'
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
  type AttentionItem,
  type Tone,
} from './ui'
import { compareValues, nextSort, type SortDirection } from '../lib/sort'

const MONITOR_TYPES: UptimeMonitorType[] = ['HTTP(s)', 'TCP', 'Ping']

const inputClass =
  'w-full rounded-lg border border-border-hairline bg-surface-page px-3 py-2 text-sm text-ink-primary outline-none focus:border-accent'
const labelClass = 'mb-1.5 block text-xs font-medium uppercase tracking-wide text-ink-muted'

const RANGE_OPTIONS = [
  { label: '24h', value: 1 },
  { label: '7d', value: 7 },
  { label: '30d', value: 30 },
]
const STATUS_OPTIONS = ['Up', 'Down', 'Partial', 'Maintenance', 'Paused']
const SUB_TABS = ['Sites', 'Incidents', 'Instances'] as const
type SubTab = (typeof SUB_TABS)[number]
const WEEKDAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'] as const

/** Down first, then sites needing a look, healthy last. */
const STATUS_RANK: Record<string, number> = { Down: 0, Partial: 1, Maintenance: 2, Paused: 3, Up: 4 }
const STATUS_TONE: Record<string, Tone | 'info'> = { Up: 'ok', Down: 'critical', Partial: 'warning', Maintenance: 'info', Paused: 'muted' }

const STRIP_CLASS: Record<StripCell, string> = {
  up: 'bg-status-good',
  degraded: 'bg-status-warning',
  down: 'bg-status-critical',
  maintenance: 'bg-accent',
  none: 'bg-gridline',
}

/** Same envelope note as everywhere else: call() resolves to { message }. */
type SiteActionResult = { errors: string[]; notes?: string[] }

function formatMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—'
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${Math.round(ms)} ms`
}

function formatPct(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  return `${v >= 99.95 || v === 0 ? v.toFixed(0) : v.toFixed(1)}%`
}

function formatWhen(value: string): string {
  const d = new Date(value.replace(' ', 'T'))
  return d.toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}

type SiteSortField = 'status' | 'site' | 'uptime' | 'response' | 'failed'

function siteSortValue(s: UptimeOverviewRow, field: SiteSortField): unknown {
  switch (field) {
    case 'status':
      return -STATUS_RANK[s.status]
    case 'site':
      return s.site_name
    case 'uptime':
      return s.uptime_percent
    case 'response':
      return s.avg_response_ms
    case 'failed':
      return s.failed_checks
  }
}

/** Multi-instance Uptime Kuma monitoring, laid out like the Logs tab:
 * filters, stat tiles, then a sortable site table (with Incidents and
 * Instances views) beside a column with the selected site, a Needs
 * attention list and each Kuma instance's health. Status, uptime and the
 * availability strip come from one get_site_overview call, which leaves
 * scheduled downtime out of uptime and shows it as Maintenance. */
export function UptimePanel() {
  const [days, setDays] = useState(7)
  const { data: overview, error, mutate: mutateOverview } = useUptimeOverview(days)
  const { data: instances, mutate: mutateInstances } = useUptimeInstances()
  const { data: sites, mutate: mutateSites } = useUptimeSites()
  const { data: monitorSettings } = useUptimeMonitorSettings()
  const [statuses, setStatuses] = useState<string[]>([])
  const [types, setTypes] = useState<string[]>([])
  const [instanceFilter, setInstanceFilter] = useState<string[]>([])
  const [search, setSearch] = useState('')
  const [subTab, setSubTab] = useState<SubTab>('Sites')
  const [sort, setSort] = useState<{ field: SiteSortField; dir: SortDirection }>({ field: 'status', dir: 'desc' })
  const [selected, setSelected] = useState<string | null>(null)
  const [showAddModal, setShowAddModal] = useState(false)
  const [showImportModal, setShowImportModal] = useState(false)
  const [historyGroup, setHistoryGroup] = useState<SiteGroup | null>(null)

  const allInstances = instances ?? []
  const siteGroups = useMemo(() => groupSites(sites ?? []), [sites])
  const instanceLabel = (name: string) => allInstances.find((i) => i.name === name)?.instance_name ?? name
  const heartbeatIntervalSeconds = monitorSettings?.heartbeat_interval_seconds || 60
  const rows = useMemo(() => overview?.sites ?? [], [overview])
  const kuma = overview?.instances ?? []
  const canEdit = !!overview?.can_edit

  const refresh = () => {
    mutateOverview()
    mutateSites()
    mutateInstances()
  }

  const typeOptions = [...new Set(rows.map((r) => r.monitor_type))].sort()
  const instanceOptions = kuma.map((i) => i.instance_name)

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase()
    return rows.filter((r) => {
      if (statuses.length && !statuses.includes(r.status)) return false
      if (types.length && !types.includes(r.monitor_type)) return false
      if (instanceFilter.length && !r.instances.some((i) => instanceFilter.includes(i.instance_name))) return false
      if (needle && !`${r.site_name} ${r.target ?? ''}`.toLowerCase().includes(needle)) return false
      return true
    })
  }, [rows, statuses, types, instanceFilter, search])

  const sorted = useMemo(
    () =>
      [...filtered].sort(
        (a, b) => compareValues(siteSortValue(a, sort.field), siteSortValue(b, sort.field), sort.dir) || a.site_name.localeCompare(b.site_name),
      ),
    [filtered, sort],
  )
  const current = sorted.find((r) => r.site_name === selected) ?? sorted[0]

  if (error) {
    return (
      <div className="rounded-xl border border-status-critical/30 bg-status-critical/10 px-4 py-3 text-sm text-status-critical">
        {getErrorMessage(error)}
      </div>
    )
  }

  const count = (s: string) => filtered.filter((r) => r.status === s)
  const down = count('Down')
  const partial = count('Partial')
  const maint = count('Maintenance')
  const up = count('Up')
  const measured = filtered.filter((r) => r.uptime_percent !== null)
  const avgUptime = measured.length ? measured.reduce((s, r) => s + (r.uptime_percent ?? 0), 0) / measured.length : null
  const responding = filtered.filter((r) => r.avg_response_ms !== null)
  const avgResponse = responding.length ? responding.reduce((s, r) => s + (r.avg_response_ms ?? 0), 0) / responding.length : null
  const rangeLabel = RANGE_OPTIONS.find((o) => o.value === days)?.label ?? `${days}d`

  const attention: AttentionItem[] = [
    ...down.map((r) => ({
      key: `down-${r.site_name}`,
      title: r.site_name,
      text: `Down on ${r.instances.length > 1 ? 'every instance' : r.instances[0]?.instance_name} · ${formatPct(r.uptime_percent)} over ${rangeLabel}`,
      tone: 'critical' as const,
      onClick: () => setSelected(r.site_name),
    })),
    ...partial.map((r) => ({
      key: `partial-${r.site_name}`,
      title: r.site_name,
      text: `Down on ${r.instances.filter((i) => i.status === 'Down').map((i) => i.instance_name).join(', ')} only — likely a location issue`,
      tone: 'warning' as const,
      onClick: () => setSelected(r.site_name),
    })),
    ...kuma
      .filter((i) => i.enabled && (i.last_error || i.stale))
      .map((i) => ({
        key: `kuma-${i.name}`,
        title: i.instance_name,
        text: i.last_error ? `Poll failing: ${i.last_error.split('\n').pop()?.slice(0, 120)}` : `No checks since ${timeAgo(i.last_check)}`,
        tone: 'warning' as const,
      })),
    ...filtered
      .filter((r) => new Set(r.instances.map((i) => i.instance)).size < r.instances.length)
      .map((r) => ({
        key: `dup-${r.site_name}`,
        title: r.site_name,
        text: 'Monitored more than once on the same instance — duplicate monitor',
        tone: 'muted' as const,
        onClick: () => setSelected(r.site_name),
      })),
    ...filtered
      .filter((r) => r.status === 'Up' && r.uptime_percent !== null && r.uptime_percent < 99)
      .map((r) => ({
        key: `flaky-${r.site_name}`,
        title: r.site_name,
        text: `Up now, but ${formatPct(r.uptime_percent)} over ${rangeLabel}`,
        tone: 'muted' as const,
        onClick: () => setSelected(r.site_name),
      })),
  ]

  const sortHeader = (label: string, field: SiteSortField, align: 'left' | 'right' = 'left') => (
    <SortHeader label={label} field={field} sortField={sort.field} sortDirection={sort.dir} onSort={(f) => setSort(nextSort(sort, f))} align={align} />
  )

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-end gap-2">
        {canEdit && (
          <a
            href="/app/uptime-monitor-settings"
            className="rounded-lg border border-border-hairline bg-surface-card px-3 py-1.5 text-xs font-medium text-ink-primary hover:border-accent"
          >
            Settings
          </a>
        )}
        <button
          type="button"
          onClick={() => setShowImportModal(true)}
          disabled={allInstances.length === 0}
          className="rounded-lg border border-border-hairline bg-surface-card px-3 py-1.5 text-xs font-medium text-ink-primary hover:border-accent disabled:cursor-not-allowed disabled:opacity-40"
        >
          Import existing monitors
        </button>
        <button
          type="button"
          onClick={() => setShowAddModal(true)}
          disabled={allInstances.length === 0}
          className="rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
        >
          + Add site
        </button>
      </div>

      <div className="mb-6 flex flex-wrap items-center gap-2">
        <Segmented label="Range" options={RANGE_OPTIONS} value={days} onChange={setDays} />
        <MultiSelectFilter label="Status" options={STATUS_OPTIONS} selected={statuses} onChange={setStatuses} />
        <MultiSelectFilter label="Type" options={typeOptions} selected={types} onChange={setTypes} />
        <MultiSelectFilter label="Instance" options={instanceOptions} selected={instanceFilter} onChange={setInstanceFilter} />
        <SearchInput value={search} onChange={setSearch} placeholder="Search site or URL…" />
      </div>

      <div className="mb-6 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
        <StatTile
          label="Sites"
          value={overview ? filtered.length : undefined}
          sub={typeOptions.map((t) => `${filtered.filter((r) => r.monitor_type === t).length} ${t}`).join(' · ')}
        />
        <StatTile label="Up" value={overview ? up.length : undefined} sub="on every instance" />
        <StatTile
          label="Down"
          value={overview ? down.length : undefined}
          sub={down.map((r) => r.site_name).join(', ') || 'none'}
          tone={down.length ? 'critical' : 'ok'}
        />
        <StatTile
          label="Partial"
          value={overview ? partial.length : undefined}
          sub="instances disagree"
          tone={partial.length ? 'warning' : 'ok'}
        />
        <StatTile
          label="In maintenance"
          value={overview ? maint.length : undefined}
          sub={maint.map((r) => r.site_name).join(', ') || 'scheduled downtime'}
        />
        <StatTile
          label={`Avg uptime · ${rangeLabel}`}
          value={avgUptime === null ? undefined : formatPct(avgUptime)}
          sub={`avg response ${formatMs(avgResponse)}`}
          tone={avgUptime !== null && avgUptime < 99 ? 'warning' : 'ok'}
        />
      </div>

      <div className="mb-4">
        <Tabs options={[...SUB_TABS]} value={subTab} onChange={(v) => setSubTab(v as SubTab)} />
      </div>

      <div className="flex flex-wrap items-start gap-4">
        <div className="min-w-0 flex-[999_1_640px]">
          {subTab === 'Sites' && (
            <Card title={`${sorted.length} site${sorted.length === 1 ? '' : 's'}`} actions={<span className="text-xs text-ink-muted">Click a row for details</span>} noPadding>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[920px] text-sm">
                  <thead className="border-b border-border-hairline text-left">
                    <tr>
                      {sortHeader('Site', 'site')}
                      {sortHeader('Status', 'status')}
                      {kuma.map((i) => (
                        <th key={i.name} scope="col" className="px-3 py-3 text-xs font-medium uppercase tracking-wide text-ink-muted">
                          {i.instance_name}
                        </th>
                      ))}
                      {sortHeader(`Uptime ${rangeLabel}`, 'uptime', 'right')}
                      {sortHeader('Avg resp.', 'response', 'right')}
                      <th scope="col" className="px-3 py-3 text-xs font-medium uppercase tracking-wide text-ink-muted">
                        Last {rangeLabel}
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border-hairline">
                    {sorted.map((r) => (
                      <SiteRow
                        key={r.site_name}
                        row={r}
                        instances={kuma}
                        selected={current?.site_name === r.site_name}
                        onSelect={() => setSelected(r.site_name)}
                      />
                    ))}
                  </tbody>
                </table>
                {sorted.length === 0 && <Empty text={overview ? (rows.length ? 'No sites match these filters.' : 'No sites added yet.') : 'Loading…'} />}
              </div>
            </Card>
          )}
          {subTab === 'Incidents' && <IncidentsTable overview={overview} onSelect={setSelected} />}
          {subTab === 'Instances' && <InstancesTable overview={overview} />}
        </div>

        <div className="flex min-w-0 flex-[1_1_340px] flex-col gap-4">
          {current && (
            <SiteDetail
              key={current.site_name}
              row={current}
              canEdit={canEdit}
              disabledInstances={new Set(kuma.filter((i) => !i.enabled).map((i) => i.name))}
              onChanged={refresh}
              onHistory={() => {
                const group = siteGroups.find((g) => g.siteName === current.site_name)
                if (group) setHistoryGroup(group)
              }}
            />
          )}
          <Card title={`Needs attention · ${attention.length}`} noPadding>
            <AttentionList items={attention} emptyText="Nothing needs attention." />
          </Card>
          <Card title={`Uptime Kuma instances · ${kuma.length}`} noPadding>
            {kuma.length === 0 ? (
              <Empty text="No Uptime Kuma instances yet — add one from Desk." />
            ) : (
              <ul className="divide-y divide-border-hairline">
                {kuma.map((i) => {
                  const tone = !i.enabled ? 'bg-ink-muted' : i.last_error ? 'bg-status-critical' : i.stale ? 'bg-status-warning' : 'bg-status-good'
                  return (
                    <li key={i.name} className="flex gap-3 px-5 py-2.5">
                      <span aria-hidden="true" className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${tone}`} />
                      <span className="min-w-0">
                        <span className="block text-sm font-semibold text-ink-primary">{i.instance_name}</span>
                        <span className="block text-xs text-ink-secondary">
                          {!i.enabled
                            ? "Disabled — Pulse doesn't contact it"
                            : i.last_error
                              ? i.last_error.split('\n').pop()?.slice(0, 140)
                              : `Last check ${timeAgo(i.last_check)} · ${i.site_count} sites`}
                        </span>
                      </span>
                    </li>
                  )
                })}
              </ul>
            )}
          </Card>
        </div>
      </div>

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

function instanceCell(i: UptimeOverviewRow['instances'][number] | undefined): { text: string; cls: string } {
  if (!i) return { text: '—', cls: 'text-ink-muted' }
  if (!i.is_active) return { text: 'Paused', cls: 'text-ink-muted' }
  if (i.stale) return { text: `${i.status} · stale ${timeAgo(i.last_checked)}`, cls: 'text-ink-muted' }
  const cls = i.status === 'Down' ? 'text-status-critical' : i.status === 'Maintenance' ? 'text-accent' : i.status === 'Up' ? 'text-status-good' : 'text-ink-muted'
  return { text: `${i.status} · ${formatMs(i.avg_response_ms)}`, cls }
}

function SiteRow({
  row,
  instances,
  selected,
  onSelect,
}: {
  row: UptimeOverviewRow
  instances: UptimeOverview['instances']
  selected: boolean
  onSelect: () => void
}) {
  const upCount = row.strip.filter((c) => c === 'up').length
  return (
    <tr onClick={onSelect} className={`cursor-pointer ${selected ? 'bg-ink-primary/5' : 'hover:bg-ink-primary/[0.03]'}`}>
      <td className="max-w-[260px] px-3 py-2.5">
        <button type="button" onClick={onSelect} className="text-left font-semibold text-ink-primary hover:underline">
          {row.site_name}
        </button>
        <div className="truncate text-xs text-ink-muted">
          {row.monitor_type} · {row.target || '—'}
        </div>
      </td>
      <td className="px-3 py-2.5 whitespace-nowrap">
        <Pill tone={STATUS_TONE[row.status]}>{row.status}</Pill>
        {row.windows.length > 0 && !row.in_maintenance && row.next_window && (
          <div className="mt-0.5 text-xs text-ink-muted">next window {formatWhen(row.next_window.start)}</div>
        )}
      </td>
      {instances.map((k) => {
        const sibs = row.instances.filter((i) => i.instance === k.name)
        const cell = instanceCell(sibs[0])
        return (
          <td key={k.name} className={`px-3 py-2.5 whitespace-nowrap text-xs ${cell.cls}`}>
            {cell.text}
            {sibs.length > 1 ? ` ×${sibs.length}` : ''}
          </td>
        )
      })}
      <td
        className={`px-3 py-2.5 text-right font-semibold tabular-nums ${
          row.uptime_percent === null ? 'text-ink-muted' : row.uptime_percent >= 99 ? 'text-status-good' : row.uptime_percent >= 90 ? 'text-status-warning' : 'text-status-critical'
        }`}
      >
        {formatPct(row.uptime_percent)}
      </td>
      <td className="px-3 py-2.5 text-right tabular-nums text-ink-secondary">{formatMs(row.avg_response_ms)}</td>
      <td className="px-3 py-2.5">
        <div role="img" aria-label={`${upCount} of ${row.strip.length} periods fully up`} className="flex gap-0.5">
          {row.strip.map((c, idx) => (
            <span key={idx} className={`h-5 w-1.5 rounded-sm ${STRIP_CLASS[c]}`} />
          ))}
        </div>
      </td>
    </tr>
  )
}

function IncidentsTable({ overview, onSelect }: { overview?: UptimeOverview; onSelect: (site: string) => void }) {
  const rows = overview?.incidents ?? []
  return (
    <Card title={`${rows.length} Site Down event${rows.length === 1 ? '' : 's'} in range`} noPadding>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[620px] text-sm">
          <thead className="border-b border-border-hairline text-left text-xs uppercase tracking-wide text-ink-muted">
            <tr>
              <th scope="col" className="px-3 py-3 font-medium">When</th>
              <th scope="col" className="px-3 py-3 font-medium">Site</th>
              <th scope="col" className="px-3 py-3 font-medium">Event</th>
              <th scope="col" className="px-3 py-3 font-medium">Sent to</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border-hairline">
            {rows.map((r) => {
              const recovery = r.message.includes('back up')
              return (
                <tr key={r.name} onClick={() => onSelect(r.site_name)} className="cursor-pointer hover:bg-ink-primary/[0.03]">
                  <td className="px-3 py-2.5 whitespace-nowrap text-ink-secondary">{formatWhen(r.sent_at)}</td>
                  <td className="px-3 py-2.5 font-medium text-ink-primary">{r.site_name}</td>
                  <td className="px-3 py-2.5">
                    <Pill tone={recovery ? 'ok' : r.resolved ? 'muted' : 'critical'}>{recovery ? 'Recovered' : r.resolved ? 'Down · resolved' : 'Down · open'}</Pill>
                  </td>
                  <td className="px-3 py-2.5 text-xs text-ink-muted">{r.channels_sent || 'not sent'}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {rows.length === 0 && <Empty text="No Site Down events in this range." />}
      </div>
    </Card>
  )
}

function InstancesTable({ overview }: { overview?: UptimeOverview }) {
  const rows = overview?.instances ?? []
  return (
    <Card title={`${rows.length} Uptime Kuma instance${rows.length === 1 ? '' : 's'}`} noPadding>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[620px] text-sm">
          <thead className="border-b border-border-hairline text-left text-xs uppercase tracking-wide text-ink-muted">
            <tr>
              <th scope="col" className="px-3 py-3 font-medium">Instance</th>
              <th scope="col" className="px-3 py-3 font-medium">State</th>
              <th scope="col" className="px-3 py-3 text-right font-medium">Sites</th>
              <th scope="col" className="px-3 py-3 font-medium">Last check</th>
              <th scope="col" className="px-3 py-3 font-medium">Last poll</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border-hairline">
            {rows.map((i) => (
              <tr key={i.name}>
                <td className="px-3 py-2.5">
                  <div className="font-medium text-ink-primary">{i.instance_name}</div>
                  {i.last_error && <div className="max-w-md truncate text-xs text-status-critical" title={i.last_error}>{i.last_error.split('\n').pop()}</div>}
                </td>
                <td className="px-3 py-2.5">
                  <Pill tone={!i.enabled ? 'muted' : i.last_error ? 'critical' : i.stale ? 'warning' : 'ok'}>
                    {!i.enabled ? 'Disabled' : i.last_error ? 'Error' : i.stale ? 'Stale' : 'OK'}
                  </Pill>
                </td>
                <td className="px-3 py-2.5 text-right tabular-nums text-ink-secondary">{i.site_count}</td>
                <td className="px-3 py-2.5 text-ink-muted">{timeAgo(i.last_check)}</td>
                <td className="px-3 py-2.5 text-ink-muted">{timeAgo(i.last_synced)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

function SiteDetail({
  row,
  canEdit,
  disabledInstances,
  onChanged,
  onHistory,
}: {
  row: UptimeOverviewRow
  canEdit: boolean
  disabledInstances: Set<string>
  onChanged: () => void
  onHistory: () => void
}) {
  const { call: pauseSite, loading: pausing } = useFrappePostCall<{ message: SiteActionResult }>('alfaedge_pulse.uptime_monitor.api.pause_site')
  const { call: resumeSite, loading: resuming } = useFrappePostCall<{ message: SiteActionResult }>('alfaedge_pulse.uptime_monitor.api.resume_site')
  const { call: deleteSite, loading: deleting } = useFrappePostCall<{ message: SiteActionResult }>('alfaedge_pulse.uptime_monitor.api.delete_site')
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const [windowDraft, setWindowDraft] = useState<MaintenanceWindow | null>(null)
  const busy = pausing || resuming || deleting

  // call() resolves to Frappe's { message } envelope, not the method's
  // return value itself.
  const run = async (fn: () => Promise<{ message: SiteActionResult }>) => {
    setError(null)
    setNote(null)
    try {
      const res = (await fn()).message
      if (res.errors.length > 0) setError(`Some instances failed: ${res.errors.join('; ')}`)
      if (res.notes?.length) setNote([...new Set(res.notes)].join('; '))
      onChanged()
    } catch (e) {
      setError(getErrorMessage(e))
    }
  }

  const disabledLabels = [...new Set(row.instances.filter((i) => disabledInstances.has(i.instance)).map((i) => i.instance_name))]

  return (
    <aside aria-label="Site details" className="rounded-2xl border border-border-hairline bg-surface-card shadow-sm">
      <div className="border-b border-border-hairline px-5 py-4">
        <div className="flex items-center justify-between gap-2">
          <h3 className="truncate text-base font-bold text-ink-primary">{row.site_name}</h3>
          <Pill tone={STATUS_TONE[row.status]}>{row.status}</Pill>
        </div>
        <p className="mt-1 break-all text-xs text-ink-muted">
          {row.monitor_type} · {row.target || '—'}
        </p>
      </div>

      <section className="border-b border-border-hairline px-5 py-4">
        <h4 className="mb-2 text-xs font-semibold uppercase tracking-widest text-ink-muted">By instance</h4>
        <ul className="space-y-1.5">
          {row.instances.map((i) => {
            const cell = instanceCell(i)
            return (
              <li key={i.name} className="flex items-center justify-between gap-2 rounded-lg border border-border-hairline px-3 py-2 text-sm">
                <span className="text-ink-primary">{i.instance_name}</span>
                <span className={`text-xs font-semibold ${cell.cls}`}>{cell.text}</span>
              </li>
            )
          })}
        </ul>
      </section>

      <section className="border-b border-border-hairline px-5 py-4">
        <dl className="grid grid-cols-3 gap-3 text-sm">
          <div>
            <dt className="text-xs text-ink-muted">Uptime</dt>
            <dd className="font-semibold text-ink-primary">{formatPct(row.uptime_percent)}</dd>
          </div>
          <div>
            <dt className="text-xs text-ink-muted">Avg response</dt>
            <dd className="font-semibold text-ink-primary">{formatMs(row.avg_response_ms)}</dd>
          </div>
          <div>
            <dt className="text-xs text-ink-muted">Failed checks</dt>
            <dd className="font-semibold text-ink-primary">
              {row.failed_checks} of {row.checks}
            </dd>
          </div>
        </dl>
        <p className="mt-2 text-xs text-ink-muted">Scheduled downtime isn't counted.</p>
      </section>

      <section className="border-b border-border-hairline px-5 py-4">
        <div className="mb-2 flex items-center justify-between gap-2">
          <h4 className="text-xs font-semibold uppercase tracking-widest text-ink-muted">Scheduled downtime</h4>
          {canEdit && (
            <button type="button" onClick={() => setWindowDraft(newWindow(row.site_name))} className="text-xs font-medium text-accent hover:underline">
              + Add window
            </button>
          )}
        </div>
        {row.windows.length === 0 ? (
          <p className="text-sm text-ink-muted">None. Planned shutdowns would count as downtime and alert.</p>
        ) : (
          <ul className="space-y-1.5">
            {row.windows.map((w) => (
              <li key={w.name} className="flex items-center justify-between gap-2 rounded-lg border border-border-hairline px-3 py-2 text-sm">
                <span className="min-w-0">
                  <span className={`block truncate ${w.enabled ? 'text-ink-primary' : 'text-ink-muted line-through'}`}>{w.label}</span>
                  {w.note && <span className="block truncate text-xs text-ink-muted">{w.note}</span>}
                </span>
                <span className="flex shrink-0 items-center gap-2">
                  {w.active && <Pill tone="info">now</Pill>}
                  {canEdit && (
                    <button type="button" onClick={() => setWindowDraft(w)} className="text-xs text-ink-secondary hover:text-ink-primary hover:underline">
                      Edit
                    </button>
                  )}
                </span>
              </li>
            ))}
          </ul>
        )}
        {row.next_window && !row.in_maintenance && (
          <p className="mt-2 text-xs text-ink-muted">
            Next: {formatWhen(row.next_window.start)} → {formatWhen(row.next_window.end)}
          </p>
        )}
      </section>

      {error && <p className="px-5 pt-3 text-xs text-status-critical">{error}</p>}
      {note && <p className="px-5 pt-3 text-xs text-ink-muted">{note}</p>}

      <div className="flex flex-wrap gap-2 px-5 py-4">
        <SideButton onClick={onHistory}>History</SideButton>
        {canEdit && (
          <>
            <SideButton onClick={() => setEditing(true)} disabled={busy}>
              Edit
            </SideButton>
            {row.is_active ? (
              <SideButton disabled={busy} onClick={() => run(() => pauseSite({ name: row.name }))}>
                Pause
              </SideButton>
            ) : (
              <SideButton disabled={busy} onClick={() => run(() => resumeSite({ name: row.name }))}>
                Resume
              </SideButton>
            )}
            <SideButton
              tone="critical"
              disabled={busy}
              onClick={() => {
                const message = disabledLabels.length
                  ? `Delete "${row.site_name}"? It's also deleted from Uptime Kuma on enabled instances. On disabled ones (${disabledLabels.join(', ')}) it's removed from Pulse only.`
                  : `Delete "${row.site_name}"? This also deletes it from Uptime Kuma on every connected instance.`
                if (confirm(message)) run(() => deleteSite({ name: row.name }))
              }}
            >
              Delete
            </SideButton>
          </>
        )}
      </div>

      {editing && (
        <EditSiteDialog
          row={row}
          onClose={(changed) => {
            setEditing(false)
            if (changed) onChanged()
          }}
        />
      )}
      {windowDraft && (
        <MaintenanceWindowDialog
          initial={windowDraft}
          onClose={(changed) => {
            setWindowDraft(null)
            if (changed) onChanged()
          }}
        />
      )}
    </aside>
  )
}

function SideButton({
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
      className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
        tone === 'critical'
          ? 'border-status-critical/40 text-status-critical hover:bg-status-critical/10'
          : 'border-border-hairline text-ink-primary hover:border-accent'
      }`}
    >
      {children}
    </button>
  )
}

function newWindow(siteName: string): MaintenanceWindow {
  return {
    site_name: siteName,
    enabled: 1,
    window_type: 'Daily',
    all_day: 0,
    start_time: '21:00',
    end_time: '07:00',
    start_datetime: null,
    end_datetime: null,
    note: '',
    monday: 0,
    tuesday: 0,
    wednesday: 0,
    thursday: 0,
    friday: 0,
    saturday: 0,
    sunday: 0,
  }
}

/** "21:00:00" → "21:00" for <input type="time">. */
function hhmm(value?: string | null): string {
  return value ? value.slice(0, 5).padStart(5, '0') : ''
}

/** "2026-11-02 10:00:00" → "2026-11-02T10:00" for <input type="datetime-local">. */
function localInput(value?: string | null): string {
  return value ? value.replace(' ', 'T').slice(0, 16) : ''
}

function MaintenanceWindowDialog({ initial, onClose }: { initial: MaintenanceWindow; onClose: (changed: boolean) => void }) {
  const [w, setW] = useState<MaintenanceWindow>({
    ...initial,
    start_time: hhmm(initial.start_time),
    end_time: hhmm(initial.end_time),
    start_datetime: localInput(initial.start_datetime),
    end_datetime: localInput(initial.end_datetime),
  })
  const [error, setError] = useState<string | null>(null)
  const { call: save, loading: saving } = useFrappePostCall<{ message: MaintenanceWindow }>('alfaedge_pulse.uptime_monitor.api.save_maintenance_window')
  const { call: remove, loading: removing } = useFrappePostCall<{ message: { deleted: string } }>('alfaedge_pulse.uptime_monitor.api.delete_maintenance_window')
  const set = <K extends keyof MaintenanceWindow>(key: K, value: MaintenanceWindow[K]) => setW((prev) => ({ ...prev, [key]: value }))

  const submit = async () => {
    setError(null)
    try {
      await save({
        values: {
          ...w,
          start_time: w.start_time ? `${w.start_time}:00` : null,
          end_time: w.end_time ? `${w.end_time}:00` : null,
          start_datetime: w.start_datetime ? `${w.start_datetime.replace('T', ' ')}:00` : null,
          end_datetime: w.end_datetime ? `${w.end_datetime.replace('T', ' ')}:00` : null,
        },
      })
      onClose(true)
    } catch (e) {
      setError(getErrorMessage(e))
    }
  }

  const types: MaintenanceWindowType[] = ['Daily', 'Weekly', 'One-time']
  const showTimes = w.window_type === 'Daily' || (w.window_type === 'Weekly' && !w.all_day)

  return (
    <Dialog open onOpenChange={(open) => !open && onClose(false)} options={{ title: `${initial.name ? 'Edit' : 'Add'} scheduled downtime · ${initial.site_name}`, size: 'lg' }}>
      <div className="flex flex-col gap-4 py-2">
        <div>
          <span className={labelClass}>Repeats</span>
          <Segmented label="Repeats" options={types.map((t) => ({ label: t, value: t }))} value={w.window_type} onChange={(v) => set('window_type', v)} />
        </div>

        {w.window_type === 'Weekly' && (
          <fieldset>
            <legend className={labelClass}>Days</legend>
            <div className="flex flex-wrap gap-1.5">
              {WEEKDAYS.map((d) => (
                <button
                  key={d}
                  type="button"
                  aria-pressed={!!w[d]}
                  onClick={() => set(d, w[d] ? 0 : 1)}
                  className={`rounded-lg border px-3 py-1.5 text-xs font-medium ${
                    w[d] ? 'border-accent bg-accent text-white' : 'border-border-hairline text-ink-secondary hover:border-accent'
                  }`}
                >
                  {d.slice(0, 3).replace(/^./, (c) => c.toUpperCase())}
                </button>
              ))}
            </div>
            <label className="mt-3 flex items-center gap-2 text-sm text-ink-primary">
              <input type="checkbox" checked={!!w.all_day} onChange={(e) => set('all_day', e.target.checked ? 1 : 0)} />
              All day
            </label>
          </fieldset>
        )}

        {showTimes && (
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={labelClass} htmlFor="mw-start-time">From</label>
              <input id="mw-start-time" type="time" className={inputClass} value={w.start_time ?? ''} onChange={(e) => set('start_time', e.target.value)} />
            </div>
            <div>
              <label className={labelClass} htmlFor="mw-end-time">Until</label>
              <input id="mw-end-time" type="time" className={inputClass} value={w.end_time ?? ''} onChange={(e) => set('end_time', e.target.value)} />
            </div>
            <p className="col-span-2 text-xs text-ink-muted">
              Site timezone. An end at or before the start runs past midnight{w.window_type === 'Weekly' ? ' and belongs to the day it starts' : ''}.
            </p>
          </div>
        )}

        {w.window_type === 'One-time' && (
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={labelClass} htmlFor="mw-start">Start</label>
              <input id="mw-start" type="datetime-local" className={inputClass} value={w.start_datetime ?? ''} onChange={(e) => set('start_datetime', e.target.value)} />
            </div>
            <div>
              <label className={labelClass} htmlFor="mw-end">End</label>
              <input id="mw-end" type="datetime-local" className={inputClass} value={w.end_datetime ?? ''} onChange={(e) => set('end_datetime', e.target.value)} />
            </div>
          </div>
        )}

        <div>
          <label className={labelClass} htmlFor="mw-note">Note</label>
          <input id="mw-note" className={inputClass} value={w.note ?? ''} onChange={(e) => set('note', e.target.value)} placeholder="e.g. Nightly shutdown to save cost" />
        </div>
        <label className="flex items-center gap-2 text-sm text-ink-primary">
          <input type="checkbox" checked={!!w.enabled} onChange={(e) => set('enabled', e.target.checked ? 1 : 0)} />
          Enabled
        </label>

        {error && <p className="text-sm text-status-critical">{error}</p>}

        <div className="flex flex-wrap items-center justify-between gap-2">
          {initial.name ? (
            <button
              type="button"
              disabled={removing}
              onClick={async () => {
                if (!confirm('Delete this downtime window?')) return
                try {
                  await remove({ name: initial.name })
                  onClose(true)
                } catch (e) {
                  setError(getErrorMessage(e))
                }
              }}
              className="rounded-lg px-3 py-2 text-sm text-status-critical hover:bg-status-critical/10 disabled:opacity-40"
            >
              Delete
            </button>
          ) : (
            <span />
          )}
          <div className="flex gap-2">
            <button type="button" onClick={() => onClose(false)} className="rounded-lg px-4 py-2 text-sm text-ink-secondary hover:bg-ink-primary/5">
              Cancel
            </button>
            <button type="button" onClick={submit} disabled={saving} className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40">
              {saving ? 'Saving…' : 'Save'}
            </button>
          </div>
        </div>
      </div>
    </Dialog>
  )
}

function EditSiteDialog({ row, onClose }: { row: UptimeOverviewRow; onClose: (changed: boolean) => void }) {
  const isHttp = row.monitor_type === 'HTTP(s)'
  const [host, port] = isHttp ? ['', ''] : (row.target ?? '').split(':')
  const [siteName, setSiteName] = useState(row.site_name)
  const [url, setUrl] = useState(isHttp ? row.target ?? '' : '')
  const [hostname, setHostname] = useState(host ?? '')
  const [portValue, setPortValue] = useState(port ?? '')
  const [error, setError] = useState<string | null>(null)
  const { call: edit, loading } = useFrappePostCall<{ message: SiteActionResult & { updated: string[] } }>('alfaedge_pulse.uptime_monitor.api.edit_site')

  const submit = async () => {
    setError(null)
    try {
      const res = (
        await edit({
          name: row.name,
          site_name: siteName.trim(),
          url: isHttp ? url.trim() : undefined,
          hostname: !isHttp ? hostname.trim() : undefined,
          port: row.monitor_type === 'TCP' && portValue ? Number(portValue) : undefined,
        })
      ).message
      if (res.errors.length) {
        setError(`Some instances failed: ${res.errors.join('; ')}`)
        return
      }
      onClose(true)
    } catch (e) {
      setError(getErrorMessage(e))
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose(false)} options={{ title: `Edit ${row.site_name}`, size: 'lg' }}>
      <div className="flex flex-col gap-4 py-2">
        <div>
          <label className={labelClass} htmlFor="edit-site-name">Site name</label>
          <input id="edit-site-name" className={inputClass} value={siteName} onChange={(e) => setSiteName(e.target.value)} />
        </div>
        {isHttp ? (
          <div>
            <label className={labelClass} htmlFor="edit-site-url">URL</label>
            <input id="edit-site-url" className={inputClass} value={url} onChange={(e) => setUrl(e.target.value)} />
          </div>
        ) : (
          <div className="grid grid-cols-3 gap-3">
            <div className="col-span-2">
              <label className={labelClass} htmlFor="edit-site-host">Hostname / IP</label>
              <input id="edit-site-host" className={inputClass} value={hostname} onChange={(e) => setHostname(e.target.value)} />
            </div>
            {row.monitor_type === 'TCP' && (
              <div>
                <label className={labelClass} htmlFor="edit-site-port">Port</label>
                <input id="edit-site-port" type="number" className={inputClass} value={portValue} onChange={(e) => setPortValue(e.target.value)} />
              </div>
            )}
          </div>
        )}
        <p className="text-xs text-ink-muted">Applies to this site on every enabled Uptime Kuma instance.</p>
        {error && <p className="text-sm text-status-critical">{error}</p>}
        <div className="flex justify-end gap-2">
          <button type="button" onClick={() => onClose(false)} className="rounded-lg px-4 py-2 text-sm text-ink-secondary hover:bg-ink-primary/5">
            Cancel
          </button>
          <button type="button" onClick={submit} disabled={loading} className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40">
            {loading ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </Dialog>
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
  const { call: addSite, loading } = useFrappePostCall<{ message: { created: string[]; errors: string[] } }>(
    'alfaedge_pulse.uptime_monitor.api.add_site',
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
      setResult(res.message)
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
  const { call: importMonitors, loading } = useFrappePostCall<{ message: { created: string[]; skipped: string[] } }>(
    'alfaedge_pulse.uptime_monitor.api.import_monitors',
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
      setResult(res.message)
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
