import { useMemo, useState } from 'react'
import {
  useAiRecentRequests,
  useAiUsageBreakdown,
  useAiUsageSummary,
  useAiUsageTrend,
  useBifrostSettings,
} from '../lib/hooks'
import type { LlmUsageLog, UsageBreakdownRow } from '../lib/types'
import { formatPercent, timeAgo } from '../lib/format'
import { TrendChart } from './TrendChart'
import { HeartbeatDot } from './HeartbeatDot'
import { StatusBadge } from './StatusBadge'
import { Tabs } from './Tabs'

const RANGE_OPTIONS = [
  { label: '7d', days: 7 },
  { label: '30d', days: 30 },
  { label: '90d', days: 90 },
]

type SortField = 'request_timestamp' | 'latency_ms' | 'total_tokens' | 'total_cost'
type SortDirection = 'asc' | 'desc'

function dateNDaysAgo(days: number): string {
  const d = new Date()
  d.setDate(d.getDate() - days)
  return d.toISOString().slice(0, 10)
}

function formatCost(v: number): string {
  return `$${v.toFixed(v < 1 ? 4 : 2)}`
}

function formatTokens(v: number): string {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}K`
  return String(Math.round(v))
}

/** Same "naive string is browser/site-local, not UTC" convention as
 * lib/format.ts — request_timestamp/bucket values already come out of the
 * backend converted to the site's system timezone. */
function bucketToUnixSeconds(bucket: string): number {
  return Math.floor(new Date(bucket.replace(' ', 'T')).getTime() / 1000)
}

/** In-depth Bifrost LLM usage analytics — cost, tokens, provider/model
 * breakdowns, and history, pulled from our own LLM Usage Log (synced from
 * Bifrost's /api/logs, see tasks/bifrost_sync.py) rather than proxying
 * Bifrost live, so the numbers stay available even if Bifrost's own log
 * retention doesn't go back as far as this view's selected range. */
export function AiUsagePanel() {
  const [rangeDays, setRangeDays] = useState(30)
  const [sortField, setSortField] = useState<SortField>('request_timestamp')
  const [sortDirection, setSortDirection] = useState<SortDirection>('desc')
  const startDate = useMemo(() => dateNDaysAgo(rangeDays), [rangeDays])

  const { data: settings } = useBifrostSettings()
  const { data: summary } = useAiUsageSummary(startDate)
  const { data: trend } = useAiUsageTrend(startDate)
  const { data: providerBreakdown } = useAiUsageBreakdown('provider', startDate)
  const { data: modelBreakdown } = useAiUsageBreakdown('model', startDate)
  const { data: virtualKeyBreakdown } = useAiUsageBreakdown('virtual_key_name', startDate)
  const { data: recent } = useAiRecentRequests(25, 0, sortField, sortDirection)

  const allTrend = trend ?? []
  const trendX = useMemo(() => allTrend.map((p) => bucketToUnixSeconds(p.bucket)), [allTrend])
  const costSeries = useMemo(() => allTrend.map((p) => p.total_cost), [allTrend])
  const tokenSeries = useMemo(() => allTrend.map((p) => p.total_tokens), [allTrend])

  const toggleSort = (field: SortField) => {
    if (field === sortField) {
      setSortDirection((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortField(field)
      setSortDirection('desc')
    }
  }

  return (
    <div>
      <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-2 text-sm text-ink-secondary">
          <HeartbeatDot
            lastSynced={settings?.last_synced}
            pollIntervalSeconds={(settings?.sync_interval_minutes || 15) * 60}
          />
          <span>
            {!settings?.enabled
              ? 'Bifrost sync is disabled — enable it in Bifrost Settings.'
              : settings?.last_synced
                ? `Bifrost synced ${timeAgo(settings.last_synced)}`
                : 'Waiting for the first Bifrost sync…'}
          </span>
        </div>
        <Tabs
          options={RANGE_OPTIONS.map((o) => o.label)}
          value={RANGE_OPTIONS.find((o) => o.days === rangeDays)?.label ?? '30d'}
          onChange={(label) => setRangeDays(RANGE_OPTIONS.find((o) => o.label === label)?.days ?? 30)}
        />
      </div>

      {settings?.last_error && (
        <div className="mb-6 rounded-xl border border-status-critical/30 bg-status-critical/10 px-4 py-3 text-sm text-status-critical">
          Last sync failed: {settings.last_error}
        </div>
      )}

      <div className="mb-8 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
        <StatTile label="Total Cost" value={summary ? formatCost(summary.total_cost) : '—'} />
        <StatTile label="Requests" value={summary ? summary.total_requests.toLocaleString() : '—'} />
        <StatTile label="Tokens" value={summary ? formatTokens(summary.total_tokens) : '—'} />
        <StatTile label="Avg Latency" value={summary ? `${Math.round(summary.average_latency)}ms` : '—'} />
        <StatTile
          label="Success Rate"
          value={summary ? formatPercent(summary.success_rate * 100) : '—'}
          tone={summary && summary.total_requests > 0 && summary.success_rate < 0.95 ? 'warning' : 'ok'}
        />
      </div>

      <div className="mb-8 grid grid-cols-1 gap-6 lg:grid-cols-2">
        <ChartCard title="Cost over time">
          {trendX.length === 0 ? (
            <EmptyChart />
          ) : (
            <TrendChart
              x={trendX}
              series={[{ label: 'Cost', data: costSeries, colorVar: '--color-accent', fill: true }]}
              valueFormatter={(v) => `$${v.toFixed(2)}`}
            />
          )}
        </ChartCard>
        <ChartCard title="Tokens over time">
          {trendX.length === 0 ? (
            <EmptyChart />
          ) : (
            <TrendChart
              x={trendX}
              series={[{ label: 'Tokens', data: tokenSeries, colorVar: '--color-status-good', fill: true }]}
              valueFormatter={(v) => formatTokens(v)}
            />
          )}
        </ChartCard>
      </div>

      <div className="mb-8 grid grid-cols-1 gap-6 lg:grid-cols-3">
        <ChartCard title="By Provider">
          <BreakdownBars rows={providerBreakdown ?? []} />
        </ChartCard>
        <ChartCard title="By Model">
          <BreakdownBars rows={modelBreakdown ?? []} />
        </ChartCard>
        <ChartCard title="By Virtual Key">
          <BreakdownBars rows={virtualKeyBreakdown ?? []} />
        </ChartCard>
      </div>

      <ChartCard title="Recent Requests" noPadding>
        <RecentRequestsTable
          rows={recent?.rows ?? []}
          sortField={sortField}
          sortDirection={sortDirection}
          onSort={toggleSort}
        />
      </ChartCard>
    </div>
  )
}

function StatTile({
  label,
  value,
  tone = 'ok',
}: {
  label: string
  value: string
  tone?: 'ok' | 'warning'
}) {
  return (
    <div className="rounded-2xl border border-border-hairline bg-surface-card p-4 shadow-sm">
      <div className={`text-xl font-semibold ${tone === 'warning' ? 'text-status-warning' : 'text-ink-primary'}`}>
        {value}
      </div>
      <div className="mt-1 text-xs font-medium uppercase tracking-wide text-ink-muted">{label}</div>
    </div>
  )
}

function ChartCard({
  title,
  children,
  noPadding,
}: {
  title: string
  children: React.ReactNode
  noPadding?: boolean
}) {
  return (
    <div className="rounded-2xl border border-border-hairline bg-surface-card shadow-sm">
      <div className="border-b border-border-hairline px-5 py-3">
        <h3 className="text-sm font-semibold text-ink-primary">{title}</h3>
      </div>
      <div className={noPadding ? '' : 'p-5'}>{children}</div>
    </div>
  )
}

function EmptyChart() {
  return <p className="py-10 text-center text-sm text-ink-muted">No data yet for this range.</p>
}

/** Simple proportional bar breakdown — no charting library needed for a
 * single-dimension share visual like this (see the frontend's charting
 * decision: uPlot only where genuine time-series exploration is worth it). */
function BreakdownBars({ rows }: { rows: UsageBreakdownRow[] }) {
  if (rows.length === 0) return <EmptyChart />
  const maxCost = Math.max(...rows.map((r) => r.total_cost), 0.0001)
  return (
    <div className="flex flex-col gap-3">
      {rows.map((row) => (
        <div key={row.label} className="grid grid-cols-[7rem_1fr_5rem] items-center gap-3">
          <span className="truncate text-sm text-ink-secondary" title={row.label}>
            {row.label}
          </span>
          <span className="h-2 overflow-hidden rounded-full bg-gridline">
            <span
              className="block h-full rounded-full bg-accent"
              style={{ width: `${Math.max((row.total_cost / maxCost) * 100, 2)}%` }}
            />
          </span>
          <span className="text-right text-xs tabular-nums text-ink-muted">{formatCost(row.total_cost)}</span>
        </div>
      ))}
    </div>
  )
}

function RecentRequestsTable({
  rows,
  sortField,
  sortDirection,
  onSort,
}: {
  rows: LlmUsageLog[]
  sortField: SortField
  sortDirection: SortDirection
  onSort: (field: SortField) => void
}) {
  if (rows.length === 0) {
    return <p className="p-5 text-sm text-ink-muted">No requests recorded yet.</p>
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[760px] text-sm">
        <thead className="text-left text-xs uppercase tracking-widest text-ink-muted">
          <tr>
            <th className="px-5 py-3">Provider</th>
            <th className="px-5 py-3">Model</th>
            <th className="px-5 py-3">Status</th>
            <SortableHeader label="Tokens" field="total_tokens" sortField={sortField} sortDirection={sortDirection} onSort={onSort} />
            <SortableHeader label="Cost" field="total_cost" sortField={sortField} sortDirection={sortDirection} onSort={onSort} />
            <SortableHeader label="Latency" field="latency_ms" sortField={sortField} sortDirection={sortDirection} onSort={onSort} />
            <SortableHeader label="Time" field="request_timestamp" sortField={sortField} sortDirection={sortDirection} onSort={onSort} />
          </tr>
        </thead>
        <tbody className="divide-y divide-border-hairline">
          {rows.map((r) => (
            <tr key={r.name} className="hover:bg-ink-primary/5">
              <td className="px-5 py-3 text-ink-primary">{r.provider || '—'}</td>
              <td className="px-5 py-3 font-mono text-xs text-ink-secondary">{r.model || '—'}</td>
              <td className="px-5 py-3"><StatusBadge status={r.status} /></td>
              <td className="px-5 py-3 tabular-nums text-ink-primary">{r.total_tokens ?? '—'}</td>
              <td className="px-5 py-3 tabular-nums text-ink-primary">
                {r.total_cost !== undefined && r.total_cost !== null ? formatCost(r.total_cost) : '—'}
              </td>
              <td className="px-5 py-3 tabular-nums text-ink-secondary">
                {r.latency_ms !== undefined && r.latency_ms !== null ? `${Math.round(r.latency_ms)}ms` : '—'}
              </td>
              <td className="px-5 py-3 text-ink-secondary">{timeAgo(r.request_timestamp)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function SortableHeader({
  label,
  field,
  sortField,
  sortDirection,
  onSort,
}: {
  label: string
  field: SortField
  sortField: SortField
  sortDirection: SortDirection
  onSort: (field: SortField) => void
}) {
  const active = field === sortField
  return (
    <th
      onClick={() => onSort(field)}
      className={`cursor-pointer select-none px-5 py-3 hover:text-ink-primary ${active ? 'text-ink-primary' : ''}`}
    >
      <span className="inline-flex items-center gap-1">
        {label}
        <span className={active ? 'opacity-100' : 'opacity-30'}>{active && sortDirection === 'asc' ? '▲' : '▼'}</span>
      </span>
    </th>
  )
}
