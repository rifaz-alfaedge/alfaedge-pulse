import { useMemo, useState } from 'react'
import {
  useOpenRouterBalanceHistory,
  useOpenRouterBreakdown,
  useOpenRouterFilterOptions,
  useOpenRouterModelTable,
  useOpenRouterSettings,
  useOpenRouterSummary,
  useOpenRouterTrend,
} from '../lib/hooks'
import type { OpenRouterModelRow } from '../lib/types'
import { bucketToUnixSeconds, formatCost, formatTokens, timeAgo } from '../lib/format'
import { type DateRangePreset, resolveDateRange } from '../lib/dateRange'
import { compareValues, nextSort, type SortDirection } from '../lib/sort'
import { TrendChart } from './TrendChart'
import { HeartbeatDot } from './HeartbeatDot'
import { DateRangeFilter } from './DateRangeFilter'
import { MultiSelectFilter } from './MultiSelectFilter'
import { BreakdownBars, Card, Empty, SortHeader, StatTile } from './ui'

type SortField = 'model' | 'total_requests' | 'prompt_tokens' | 'completion_tokens' | 'reasoning_tokens' | 'total_cost'

/** OpenRouter spend, per-model usage and remaining credit — its own tab,
 * laid out like the AI Usage (Bifrost) tab. Data comes from the daily
 * activity sync and the 30-minute balance snapshots (tasks/openrouter_sync.py),
 * never from OpenRouter live. Latency, success rate and the request log are
 * absent because OpenRouter's activity data has none of them. */
export function OpenRouterPanel() {
  const [preset, setPreset] = useState<DateRangePreset>('30d')
  const [customStart, setCustomStart] = useState('')
  const [customEnd, setCustomEnd] = useState('')
  const [provider, setProvider] = useState<string[]>([])
  const [model, setModel] = useState<string[]>([])
  const [sort, setSort] = useState<{ field: SortField; dir: SortDirection }>({ field: 'total_cost', dir: 'desc' })

  const { startDate, endDate } = useMemo(() => resolveDateRange(preset, customStart, customEnd), [preset, customStart, customEnd])
  const filters = useMemo(() => ({ provider, model }), [provider, model])

  const { data: settings } = useOpenRouterSettings()
  const { data: filterOptions } = useOpenRouterFilterOptions()
  const { data: summary } = useOpenRouterSummary(startDate, endDate, filters)
  const { data: trend } = useOpenRouterTrend(startDate, endDate, filters)
  const { data: providerBreakdown } = useOpenRouterBreakdown('provider', startDate, endDate, filters)
  const { data: modelBreakdown } = useOpenRouterBreakdown('model', startDate, endDate, filters)
  const { data: modelTable } = useOpenRouterModelTable(startDate, endDate, filters)
  const { data: balance } = useOpenRouterBalanceHistory(startDate, endDate)

  const allTrend = trend ?? []
  const trendX = useMemo(() => allTrend.map((p) => bucketToUnixSeconds(p.bucket)), [allTrend])
  const costSeries = useMemo(() => allTrend.map((p) => p.total_cost), [allTrend])
  const promptSeries = useMemo(() => allTrend.map((p) => p.prompt_tokens), [allTrend])
  const completionSeries = useMemo(() => allTrend.map((p) => p.completion_tokens), [allTrend])
  const reasoningSeries = useMemo(() => allTrend.map((p) => p.reasoning_tokens), [allTrend])

  const allBalance = balance ?? []
  const balanceX = useMemo(() => allBalance.map((p) => bucketToUnixSeconds(p.taken_at)), [allBalance])
  const balanceSeries = useMemo(() => allBalance.map((p) => p.remaining), [allBalance])

  const rows = useMemo(
    () => [...(modelTable ?? [])].sort((a, b) => compareValues(a[sort.field], b[sort.field], sort.dir)),
    [modelTable, sort],
  )

  const credits = summary?.credits
  const threshold = settings?.low_balance_threshold ?? 0
  const low = !!credits && threshold > 0 && credits.remaining < threshold
  const lastSynced = settings?.last_activity_synced

  return (
    <div>
      <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-2 text-sm text-ink-secondary">
          <HeartbeatDot lastSynced={lastSynced} pollIntervalSeconds={24 * 3600} />
          <span>
            {!settings?.enabled
              ? 'OpenRouter sync is disabled — enable it in OpenRouter Settings.'
              : lastSynced
                ? `OpenRouter usage synced ${timeAgo(lastSynced)}`
                : 'Waiting for the first OpenRouter sync…'}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <DateRangeFilter
            preset={preset}
            customStart={customStart}
            customEnd={customEnd}
            onPresetChange={setPreset}
            onCustomChange={(start, end) => {
              setCustomStart(start)
              setCustomEnd(end)
            }}
          />
          <MultiSelectFilter label="Providers" options={filterOptions?.providers ?? []} selected={provider} onChange={setProvider} />
          <MultiSelectFilter label="Models" options={filterOptions?.models ?? []} selected={model} onChange={setModel} />
        </div>
      </div>

      {settings?.last_activity_error && (
        <div className="mb-6 rounded-xl border border-status-critical/30 bg-status-critical/10 px-4 py-3 text-sm text-status-critical">
          Last usage sync failed: {settings.last_activity_error}
        </div>
      )}
      {settings?.last_balance_error && (
        <div className="mb-6 rounded-xl border border-status-critical/30 bg-status-critical/10 px-4 py-3 text-sm text-status-critical">
          Last balance check failed: {settings.last_balance_error}
        </div>
      )}
      {low && credits && (
        <div className="mb-6 rounded-xl border border-status-warning/30 bg-status-warning/10 px-4 py-3 text-sm text-status-warning">
          Low credit balance: {formatCost(credits.remaining)} remaining, below the {formatCost(threshold)} threshold.
        </div>
      )}

      <div className="mb-8 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
        <StatTile
          label="Total Spend"
          value={summary ? formatCost(summary.total_cost) : '—'}
          sub={summary && summary.byok_cost > 0 ? `+ ${formatCost(summary.byok_cost)} BYOK` : undefined}
        />
        <StatTile label="Requests" value={summary ? summary.total_requests.toLocaleString() : '—'} />
        <StatTile
          label="Tokens"
          value={summary ? formatTokens(summary.total_tokens) : '—'}
          sub={summary && summary.reasoning_tokens > 0 ? `${formatTokens(summary.reasoning_tokens)} reasoning` : undefined}
        />
        <StatTile
          label="Credits Remaining"
          value={credits ? formatCost(credits.remaining) : '—'}
          tone={low ? 'warning' : 'ok'}
          sub={credits ? `as of ${timeAgo(credits.taken_at)}` : undefined}
        />
        <StatTile label="Credits Purchased" value={credits ? formatCost(credits.total_purchased) : '—'} />
      </div>

      <div className="mb-8 grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card title="Spend over time">
          {trendX.length === 0 ? (
            <Empty text="No data yet for this range." />
          ) : (
            <TrendChart
              x={trendX}
              series={[{ label: 'Spend', data: costSeries, colorVar: '--color-accent', fill: true }]}
              valueFormatter={(v) => `$${v.toFixed(2)}`}
            />
          )}
        </Card>
        <Card title="Tokens over time">
          {trendX.length === 0 ? (
            <Empty text="No data yet for this range." />
          ) : (
            <TrendChart
              x={trendX}
              series={[
                { label: 'Prompt', data: promptSeries, colorVar: '--color-status-good', fill: true },
                { label: 'Completion', data: completionSeries, colorVar: '--color-accent' },
                { label: 'Reasoning', data: reasoningSeries, colorVar: '--color-status-warning' },
              ]}
              valueFormatter={(v) => formatTokens(v)}
            />
          )}
        </Card>
      </div>

      <div className="mb-8 grid grid-cols-1 gap-6 lg:grid-cols-3">
        <Card title="Credits remaining over time">
          {balanceX.length === 0 ? (
            <Empty text="No balance checks yet for this range." />
          ) : (
            <TrendChart
              x={balanceX}
              series={[{ label: 'Remaining', data: balanceSeries, colorVar: '--color-status-good', fill: true }]}
              valueFormatter={(v) => `$${v.toFixed(2)}`}
            />
          )}
        </Card>
        <Card title="By Provider">
          <BreakdownBars rows={(providerBreakdown ?? []).map((r) => ({ label: r.label, value: r.total_cost }))} format={formatCost} />
        </Card>
        <Card title="By Model">
          <BreakdownBars rows={(modelBreakdown ?? []).map((r) => ({ label: r.label, value: r.total_cost }))} format={formatCost} />
        </Card>
      </div>

      <Card title="Usage by model" noPadding>
        <ModelTable rows={rows} sortField={sort.field} sortDirection={sort.dir} onSort={(f) => setSort((s) => nextSort(s, f))} />
      </Card>

      <p className="mt-4 text-xs text-ink-muted">
        Today's usage is not included: OpenRouter's activity data lags by one completed UTC day, and dates here are UTC days.
        {settings?.last_activity_through ? ` Latest day stored: ${settings.last_activity_through}.` : ''}
      </p>
    </div>
  )
}

function ModelTable({
  rows,
  sortField,
  sortDirection,
  onSort,
}: {
  rows: OpenRouterModelRow[]
  sortField: SortField
  sortDirection: SortDirection
  onSort: (field: SortField) => void
}) {
  if (rows.length === 0) return <p className="p-5 text-sm text-ink-muted">No usage recorded yet.</p>
  const head = { sortField, sortDirection, onSort }
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[760px] text-sm">
        <thead>
          <tr>
            <SortHeader label="Model" field="model" {...head} />
            <th className="px-3 py-3 text-left text-xs font-medium uppercase tracking-wide text-ink-muted">Providers</th>
            <SortHeader label="Requests" field="total_requests" align="right" {...head} />
            <SortHeader label="Prompt" field="prompt_tokens" align="right" {...head} />
            <SortHeader label="Completion" field="completion_tokens" align="right" {...head} />
            <SortHeader label="Reasoning" field="reasoning_tokens" align="right" {...head} />
            <SortHeader label="Spend" field="total_cost" align="right" {...head} />
          </tr>
        </thead>
        <tbody className="divide-y divide-border-hairline">
          {rows.map((r) => (
            <tr key={r.model} className="hover:bg-ink-primary/5">
              <td className="px-3 py-3 font-mono text-xs text-ink-primary">{r.model}</td>
              <td className="px-3 py-3 text-ink-secondary">{r.providers || '—'}</td>
              <td className="px-3 py-3 text-right tabular-nums text-ink-primary">{r.total_requests.toLocaleString()}</td>
              <td className="px-3 py-3 text-right tabular-nums text-ink-secondary">{formatTokens(r.prompt_tokens)}</td>
              <td className="px-3 py-3 text-right tabular-nums text-ink-secondary">{formatTokens(r.completion_tokens)}</td>
              <td className="px-3 py-3 text-right tabular-nums text-ink-secondary">{formatTokens(r.reasoning_tokens)}</td>
              <td className="px-3 py-3 text-right tabular-nums text-ink-primary">{formatCost(r.total_cost)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
