import { useEffect, useMemo, useState } from 'react'
import { Dialog } from '@rtcamp/frappe-ui-react'
import { useFrappePostCall } from 'frappe-react-sdk'
import {
  type LogFilters,
  useLogFilterOptions,
  useLogSettings,
  useLogSummary,
  useLogs,
  useRecurringErrors,
} from '../lib/hooks'
import type { LogDetail, LogRow, LogSettings, RecurringErrorRow } from '../lib/types'
import { timeAgo } from '../lib/format'
import { getErrorMessage } from '../lib/errors'
import { type DateRangePreset, resolveDateRange } from '../lib/dateRange'
import { DateRangeFilter } from './DateRangeFilter'
import { MultiSelectFilter } from './MultiSelectFilter'
import { SearchInput } from './SearchInput'
import { Tabs } from './Tabs'
import { TrendChart } from './TrendChart'
import { LogSettingsDialog } from './LogSettingsDialog'
import { ManageSitesDialog } from './ManageSitesDialog'
import { useSWRConfig } from 'frappe-react-sdk'
import { Card, Empty, StatTile } from './ui'
import { CreateTaskButton, IgnoreCheckbox } from './ErrorActions'

const SUB_TABS = ['All Logs', 'Recurring Errors'] as const
const LOG_TYPE_OPTIONS = ['Error', 'General']
const PAGE_LENGTH = 50

/** Same "naive string is site-local" convention as the rest of the app —
 * the backend formats every ClickHouse timestamp in the site's timezone. */
function toUnixSeconds(value: string): number {
  return Math.floor(new Date(value.replace(' ', 'T')).getTime() / 1000)
}

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return debounced
}

/** Frappe error logs from every production site, read live from ClickHouse
 * (see alfaedge_pulse/log_monitor). Nothing is copied locally except
 * Recurring Error alert state, so every filter here is a ClickHouse query. */
export function LogsPanel() {
  const [subTab, setSubTab] = useState<(typeof SUB_TABS)[number]>('All Logs')
  const [preset, setPreset] = useState<DateRangePreset>('today')
  const [customStart, setCustomStart] = useState('')
  const [customEnd, setCustomEnd] = useState('')
  const [sites, setSites] = useState<string[]>([])
  const [logTypes, setLogTypes] = useState<string[]>([])
  const [searchInput, setSearchInput] = useState('')
  const [showIgnored, setShowIgnored] = useState(false)
  const [page, setPage] = useState(1)
  const [selectedLog, setSelectedLog] = useState<Pick<LogRow, 'log_id' | 'ts' | 'ignored'> | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [sitesOpen, setSitesOpen] = useState(false)
  const { mutate: globalMutate } = useSWRConfig()
  const search = useDebounced(searchInput.trim(), 400)

  const { startDate, endDate } = useMemo(
    () => resolveDateRange(preset, customStart, customEnd),
    [preset, customStart, customEnd],
  )
  const filters: LogFilters = useMemo(
    () => ({ startDate, endDate, sites, logTypes, search, showIgnored }),
    [startDate, endDate, sites, logTypes, search, showIgnored],
  )
  // Any filter change sends the table back to page 1.
  useEffect(() => setPage(1), [filters])

  const { data: settings, mutate: refreshSettings } = useLogSettings()
  const ready = !!settings?.configured
  const canEdit = !!settings?.can_edit
  const canIgnore = !!settings?.can_ignore
  // Ignoring changes every count and list on the tab, so refetch them all.
  const refreshAll = () => globalMutate(() => true)
  const { data: options } = useLogFilterOptions(ready)
  const { data: summary, error: summaryError } = useLogSummary(filters, ready)
  const { data: logs, error: logsError, isLoading: logsLoading } = useLogs(filters, page, PAGE_LENGTH, ready && subTab === 'All Logs')
  const { data: recurring, error: recurringError, isLoading: recurringLoading, mutate: refreshRecurring } = useRecurringErrors(
    filters,
    2,
    ready && subTab === 'Recurring Errors',
  )

  const queryError = summaryError || logsError || recurringError
  const siteOptions = useMemo(() => {
    const fromServer = (options?.sites ?? []).map((s) => s.site_url)
    // Keep selected sites visible even if they've aged out of the options window.
    return Array.from(new Set([...sites, ...fromServer]))
  }, [options, sites])

  const trend = useMemo(() => summary?.trend ?? [], [summary])
  const trendX = useMemo(() => trend.map((p) => toUnixSeconds(p.bucket)), [trend])

  // A recurring error opens its latest occurrence; the filters stay as they are.
  const openLatest = (row: RecurringErrorRow) =>
    setSelectedLog({ log_id: row.latest_log_id, ts: row.latest_ts, ignored: row.ignored })

  // The number cards double as quick filters for the list below them.
  const showLogs = (types: string[]) => {
    setSubTab('All Logs')
    setLogTypes(types)
  }
  const onlyType = logTypes.length === 1 ? logTypes[0] : null
  const filtersChanged =
    preset !== 'today' || !!customStart || !!customEnd || sites.length > 0 || logTypes.length > 0 || !!searchInput || showIgnored
  const clearFilters = () => {
    setPreset('today')
    setCustomStart('')
    setCustomEnd('')
    setSites([])
    setLogTypes([])
    setSearchInput('')
    setShowIgnored(false)
    setPage(1)
  }

  return (
    <div>
      <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <div className="flex min-w-0 items-center gap-3 text-sm text-ink-secondary">
          <span
            className={`h-2.5 w-2.5 shrink-0 rounded-full ${
              !ready ? 'bg-ink-muted' : queryError ? 'bg-status-critical' : 'bg-status-good'
            }`}
          />
          <span className="truncate">
            {!settings
              ? 'Loading…'
              : !ready
                ? canEdit
                  ? 'Log Monitor is not configured — open Settings to connect ClickHouse.'
                  : 'Log Monitor is not configured yet — ask a Proxmox Monitor Manager to connect ClickHouse.'
                : queryError
                  ? 'ClickHouse query failed'
                  : `${canEdit && settings.effective_url ? `ClickHouse ${settings.effective_url}` : 'Live from ClickHouse'}${
                      settings.last_checked ? ` · alerts checked ${timeAgo(settings.last_checked)}` : ''
                    }`}
          </span>
          {canEdit && ready && (
            <button
              type="button"
              onClick={() => setSitesOpen(true)}
              className="shrink-0 rounded-lg border border-border-hairline bg-surface-card px-3 py-1.5 text-xs font-medium text-ink-primary hover:border-accent"
            >
              Sites
            </button>
          )}
          {canEdit && (
            <button
              type="button"
              onClick={() => setSettingsOpen(true)}
              className="shrink-0 rounded-lg border border-border-hairline bg-surface-card px-3 py-1.5 text-xs font-medium text-ink-primary hover:border-accent"
            >
              Settings
            </button>
          )}
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
          {subTab === 'All Logs' && (
            <MultiSelectFilter label="Log Type" options={LOG_TYPE_OPTIONS} selected={logTypes} onChange={setLogTypes} />
          )}
          <MultiSelectFilter label="Sites" options={siteOptions} selected={sites} onChange={setSites} />
          <SearchInput value={searchInput} onChange={setSearchInput} placeholder="Search log text…" />
          {canIgnore && (
            <label className="flex items-center gap-2 rounded-xl border border-border-hairline bg-surface-card px-3 py-2 text-sm text-ink-secondary">
              <input type="checkbox" checked={showIgnored} onChange={(e) => setShowIgnored(e.target.checked)} />
              Show ignored
            </label>
          )}
          <button
            type="button"
            onClick={clearFilters}
            disabled={!filtersChanged}
            className="rounded-xl border border-border-hairline bg-surface-card px-3 py-2 text-sm font-medium text-ink-primary hover:border-accent disabled:cursor-not-allowed disabled:opacity-40"
          >
            Clear filters
          </button>
        </div>
      </div>

      {queryError && (
        <div className="mb-6 break-words rounded-xl border border-status-critical/30 bg-status-critical/10 px-4 py-3 text-sm text-status-critical">
          {getErrorMessage(queryError)}
        </div>
      )}
      {canEdit && settings?.last_error && (
        <div className="mb-6 rounded-xl border border-status-warning/40 bg-status-warning/10 px-4 py-3 text-sm text-ink-primary">
          <span className="font-semibold">Last recurring-error check failed.</span>{' '}
          <span className="break-words font-mono text-xs">{settings.last_error.trim().split('\n').pop()}</span>
        </div>
      )}

      <div className="mb-6 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
        <StatTile
          label="Total Logs"
          value={summary?.total}
          onClick={() => showLogs([])}
          active={subTab === 'All Logs' && logTypes.length === 0}
        />
        <StatTile
          label="Errors"
          value={summary?.errors}
          tone={summary && summary.errors > 0 ? 'critical' : 'ok'}
          onClick={() => showLogs(['Error'])}
          active={subTab === 'All Logs' && onlyType === 'Error'}
        />
        <StatTile
          label="General"
          value={summary?.general}
          onClick={() => showLogs(['General'])}
          active={subTab === 'All Logs' && onlyType === 'General'}
        />
        <StatTile label="Sites" value={summary?.sites} />
        <StatTile
          label="Distinct Errors"
          value={summary?.distinct_errors}
          onClick={() => setSubTab('Recurring Errors')}
          active={subTab === 'Recurring Errors'}
        />
      </div>

      <Card title="Logs over time" className="mb-6">
        {trendX.length === 0 ? (
          <Empty text={ready ? 'No logs in this range.' : 'Connect ClickHouse to see logs.'} />
        ) : (
          <TrendChart
            x={trendX}
            series={[
              { label: 'Errors', data: trend.map((p) => p.errors), colorVar: '--color-status-critical', fill: true },
              { label: 'General', data: trend.map((p) => p.general), colorVar: '--color-accent' },
            ]}
            valueFormatter={(v) => Math.round(v).toLocaleString()}
          />
        )}
      </Card>

      <div className="mb-4">
        <Tabs options={[...SUB_TABS]} value={subTab} onChange={(v) => setSubTab(v as (typeof SUB_TABS)[number])} />
      </div>

      {subTab === 'All Logs' ? (
        <>
          <Card title={logs ? `${logs.total.toLocaleString()} log${logs.total === 1 ? '' : 's'}` : 'Logs'} noPadding>
            <LogTable
              rows={logs?.rows ?? []}
              loading={logsLoading && !logs}
              onOpen={setSelectedLog}
              canIgnore={canIgnore}
              onIgnoredChanged={refreshAll}
            />
            {logs && logs.total > PAGE_LENGTH && (
              <Pager page={page} pageLength={PAGE_LENGTH} total={logs.total} onChange={setPage} />
            )}
          </Card>
        </>
      ) : (
        <Card title="Recurring errors (2+ occurrences, grouped by error and site; resolved ones hidden until they recur)" noPadding>
          <RecurringTable
            rows={recurring ?? []}
            loading={recurringLoading && !recurring}
            onOpen={openLatest}
            onResolved={() => refreshRecurring()}
            canIgnore={canIgnore}
            showIgnored={showIgnored}
            onIgnoredChanged={refreshAll}
          />
        </Card>
      )}

      {selectedLog && (
        <LogDetailDialog log={selectedLog} canIgnore={canIgnore} onIgnoredChanged={refreshAll} onClose={() => setSelectedLog(null)} />
      )}
      {canEdit && sitesOpen && (
        <ManageSitesDialog
          canEdit
          onClose={() => setSitesOpen(false)}
          onChanged={() => {
            // An alias regroups every query on this tab — drop the site
            // from any active filter (it may no longer exist) and refetch.
            setSites([])
            globalMutate(() => true)
          }}
        />
      )}
      {canEdit && settingsOpen && settings && (
        <LogSettingsDialog
          settings={settings as LogSettings}
          onClose={() => setSettingsOpen(false)}
          onSaved={() => refreshSettings()}
        />
      )}
    </div>
  )
}

function LogTypeBadge({ type }: { type: string }) {
  const isError = type === 'Error'
  return (
    <span
      className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${
        isError ? 'bg-status-critical/10 text-status-critical' : 'bg-accent/10 text-accent'
      }`}
    >
      {type}
    </span>
  )
}

/** Ignore + Create task, the first column of both lists. */
function ActionsCell({
  signature,
  ignored,
  canIgnore,
  onIgnoredChanged,
}: {
  signature: string
  ignored: boolean
  canIgnore: boolean
  onIgnoredChanged: (ignored: boolean) => void
}) {
  return (
    <td className="whitespace-nowrap px-5 py-3 align-top" onClick={(e) => e.stopPropagation()}>
      <span className="inline-flex items-start gap-3">
        <IgnoreCheckbox signature={signature} ignored={ignored} canIgnore={canIgnore} onChanged={onIgnoredChanged} />
        <CreateTaskButton signature={signature} compact />
      </span>
    </td>
  )
}

const ACTIONS_HEADER = (
  <th className="px-5 py-3 font-medium">
    Ignore · Task
  </th>
)

function LogTable({
  rows,
  loading,
  onOpen,
  canIgnore,
  onIgnoredChanged,
}: {
  rows: LogRow[]
  loading: boolean
  onOpen: (row: LogRow) => void
  canIgnore: boolean
  onIgnoredChanged: () => void
}) {
  if (loading) return <Empty text="Loading…" />
  if (rows.length === 0) return <Empty text="No logs match these filters." />
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[860px] text-left text-sm">
        <thead className="border-b border-border-hairline text-xs uppercase tracking-wide text-ink-muted">
          <tr>
            {ACTIONS_HEADER}
            <th className="px-3 py-3 font-medium">Time</th>
            <th className="px-3 py-3 font-medium">Type</th>
            <th className="px-3 py-3 font-medium">Site</th>
            <th className="px-5 py-3 font-medium">Message</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={`${row.log_id}-${row.ts}`}
              onClick={() => onOpen(row)}
              className={`cursor-pointer border-b border-border-hairline last:border-0 hover:bg-surface-page ${row.ignored ? 'opacity-60' : ''}`}
            >
              {row.log_type === 'Error' ? (
                <ActionsCell signature={row.signature} ignored={!!row.ignored} canIgnore={canIgnore} onIgnoredChanged={onIgnoredChanged} />
              ) : (
                <td className="px-5 py-3" />
              )}
              <td className="whitespace-nowrap px-3 py-3 align-top text-ink-secondary" title={row.ts}>
                {row.ts}
              </td>
              <td className="px-3 py-3 align-top">
                <LogTypeBadge type={row.log_type} />
              </td>
              <td className="max-w-[14rem] truncate px-3 py-3 align-top text-ink-secondary" title={row.site_url}>
                {row.site_url}
              </td>
              <td className="px-5 py-3 align-top">
                <div className="line-clamp-1 font-medium text-ink-primary" title={row.title}>
                  {row.title || '(empty)'}
                </div>
                {row.preview && row.preview.trim() !== row.title && (
                  <div className="mt-0.5 line-clamp-2 font-mono text-xs text-ink-muted">{row.preview}</div>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** Same columns and look as LogTable — one row per distinct error, with its
 * latest time, occurrences and alert state. Clicking a row opens the latest
 * occurrence; it never changes the filters. */
function RecurringTable({
  rows,
  loading,
  onOpen,
  onResolved,
  canIgnore,
  showIgnored,
  onIgnoredChanged,
}: {
  rows: RecurringErrorRow[]
  loading: boolean
  onOpen: (row: RecurringErrorRow) => void
  onResolved: () => void
  canIgnore: boolean
  showIgnored: boolean
  onIgnoredChanged: () => void
}) {
  const { call: resolve } = useFrappePostCall('alfaedge_pulse.log_monitor.api.resolve_error')
  // Hidden optimistically the moment Resolve is clicked, rather than
  // waiting for the refetch.
  const [resolving, setResolving] = useState<Set<string>>(new Set())
  const [resolveError, setResolveError] = useState<string | null>(null)
  // Ignored rows also vanish at once (unless Show ignored is on).
  const [ignoring, setIgnoring] = useState<Set<string>>(new Set())
  const visible = rows.filter((r) => !resolving.has(r.signature) && (showIgnored || !ignoring.has(r.signature)))

  const onResolve = async (row: RecurringErrorRow) => {
    setResolveError(null)
    setResolving((s) => new Set(s).add(row.signature))
    try {
      await resolve({ signature: row.signature })
      onResolved()
    } catch (e) {
      setResolveError(getErrorMessage(e))
      setResolving((s) => {
        const next = new Set(s)
        next.delete(row.signature)
        return next
      })
    }
  }

  if (loading) return <Empty text="Loading…" />
  if (visible.length === 0) return <Empty text="No unresolved error has repeated in this range." />
  return (
    <div className="overflow-x-auto">
      {resolveError && (
        <p className="border-b border-border-hairline px-5 py-2 text-sm text-status-critical">{resolveError}</p>
      )}
      <table className="w-full min-w-[1080px] text-left text-sm">
        <thead className="border-b border-border-hairline text-xs uppercase tracking-wide text-ink-muted">
          <tr>
            {ACTIONS_HEADER}
            <th className="px-3 py-3 font-medium">Last seen</th>
            <th className="px-3 py-3 font-medium">Type</th>
            <th className="px-3 py-3 font-medium">Site</th>
            <th className="px-5 py-3 font-medium">Message</th>
            <th className="px-3 py-3 text-right font-medium">Occurrences</th>
            <th className="px-3 py-3 font-medium">First seen</th>
            <th className="px-3 py-3 font-medium">Alert</th>
            <th className="px-5 py-3">
              <span className="sr-only">Resolve</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {visible.map((row) => (
            <tr
              key={row.signature}
              onClick={() => onOpen(row)}
              className={`cursor-pointer border-b border-border-hairline last:border-0 hover:bg-surface-page ${row.ignored ? 'opacity-60' : ''}`}
            >
              <ActionsCell
                signature={row.signature}
                ignored={row.ignored}
                canIgnore={canIgnore}
                onIgnoredChanged={(next) => {
                  setIgnoring((s) => {
                    const copy = new Set(s)
                    if (next) copy.add(row.signature)
                    else copy.delete(row.signature)
                    return copy
                  })
                  onIgnoredChanged()
                }}
              />
              <td className="whitespace-nowrap px-3 py-3 align-top text-ink-secondary" title={row.latest_ts}>
                {row.latest_ts}
              </td>
              <td className="px-3 py-3 align-top">
                <LogTypeBadge type="Error" />
              </td>
              <td className="max-w-[14rem] truncate px-3 py-3 align-top text-ink-secondary" title={row.site_url}>
                {row.site_url}
              </td>
              <td className="px-5 py-3 align-top">
                <div className="line-clamp-2 font-medium text-ink-primary" title={row.title}>
                  {row.reopened && (
                    <span
                      className="mr-1.5 inline-flex rounded-full bg-status-warning/15 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-status-warning"
                      title={`Resolved ${row.resolved_at ?? ''} — happened again since`}
                    >
                      Reopened
                    </span>
                  )}
                  {row.title || '(empty)'}
                </div>
                {row.ignored && row.ignored_by && (
                  <div className="mt-0.5 text-xs text-ink-muted" title={row.ignored_at ?? ''}>
                    Ignored by {row.ignored_by}
                  </div>
                )}
              </td>
              <td className="px-3 py-3 text-right align-top text-base font-semibold tabular-nums text-ink-primary">
                {row.occurrences.toLocaleString()}
              </td>
              <td className="whitespace-nowrap px-3 py-3 align-top text-ink-secondary" title={row.first_seen}>
                {row.first_seen}
              </td>
              <td className="px-3 py-3 align-top">
                {row.is_critical ? (
                  <span className="inline-flex rounded-full bg-status-critical/10 px-2 py-0.5 text-xs font-medium text-status-critical">
                    Alerting
                  </span>
                ) : row.alert_count > 0 ? (
                  <span className="text-xs text-ink-muted">Alerted {row.alert_count}×</span>
                ) : (
                  <span className="text-xs text-ink-muted">—</span>
                )}
              </td>
              <td className="px-5 py-3 text-right align-top">
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation()
                    onResolve(row)
                  }}
                  className="rounded-lg border border-border-hairline px-3 py-1 text-xs font-medium text-ink-primary hover:border-status-good hover:text-status-good"
                  title="Hide this error until it happens again"
                >
                  Resolve
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Pager({
  page,
  pageLength,
  total,
  onChange,
}: {
  page: number
  pageLength: number
  total: number
  onChange: (page: number) => void
}) {
  const lastPage = Math.max(1, Math.ceil(total / pageLength))
  const from = (page - 1) * pageLength + 1
  const to = Math.min(page * pageLength, total)
  const btn =
    'rounded-lg border border-border-hairline px-3 py-1.5 text-xs font-medium text-ink-primary enabled:hover:border-accent disabled:opacity-40'
  return (
    <div className="flex items-center justify-between gap-3 border-t border-border-hairline px-5 py-3 text-xs text-ink-muted">
      <span>
        {from.toLocaleString()}–{to.toLocaleString()} of {total.toLocaleString()}
      </span>
      <div className="flex gap-2">
        <button type="button" className={btn} disabled={page <= 1} onClick={() => onChange(page - 1)}>
          Previous
        </button>
        <button type="button" className={btn} disabled={page >= lastPage} onClick={() => onChange(page + 1)}>
          Next
        </button>
      </div>
    </div>
  )
}

function LogDetailDialog({
  log,
  canIgnore,
  onIgnoredChanged,
  onClose,
}: {
  log: Pick<LogRow, 'log_id' | 'ts'> & { ignored?: boolean }
  canIgnore: boolean
  onIgnoredChanged: () => void
  onClose: () => void
}) {
  const [ignored, setIgnored] = useState(!!log.ignored)
  const { call } = useFrappePostCall<{ message: LogDetail }>('alfaedge_pulse.log_monitor.api.get_log_detail')
  const [detail, setDetail] = useState<LogDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    let cancelled = false
    call({ log_id: log.log_id, ts: log.ts })
      .then((r) => !cancelled && setDetail(r.message))
      .catch((e) => !cancelled && setError(getErrorMessage(e)))
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [log.log_id, log.ts])

  const copy = () => {
    if (!detail) return
    navigator.clipboard?.writeText(detail.content).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()} options={{ title: 'Log entry', size: '4xl' }}>
      <div className="flex flex-col gap-4 py-2">
        {error && <p className="text-sm text-status-critical">{error}</p>}
        {!detail && !error && <p className="text-sm text-ink-muted">Loading…</p>}
        {detail && (
          <>
            <div className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-3">
              <Field label="Time" value={detail.ts} />
              <Field label="Site" value={detail.site_url} />
              <div>
                <div className="text-xs uppercase tracking-wide text-ink-muted">Type</div>
                <div className="mt-1">
                  <LogTypeBadge type={detail.log_type} />
                </div>
              </div>
            </div>
            <Field label="Message" value={detail.title} mono />
            {detail.log_type === 'Error' && (
              <div className="flex flex-wrap items-center gap-4">
                <CreateTaskButton signature={detail.signature} />
                <IgnoreCheckbox
                  signature={detail.signature}
                  ignored={ignored}
                  canIgnore={canIgnore}
                  onChanged={(next) => {
                    setIgnored(next)
                    onIgnoredChanged()
                  }}
                />
              </div>
            )}
            <div>
              <div className="mb-1 flex items-center justify-between">
                <span className="text-xs uppercase tracking-wide text-ink-muted">Full content</span>
                <button type="button" onClick={copy} className="text-xs font-medium text-accent hover:underline">
                  {copied ? 'Copied' : 'Copy'}
                </button>
              </div>
              <pre className="max-h-[50vh] overflow-auto whitespace-pre-wrap break-words rounded-lg border border-border-hairline bg-surface-page p-3 font-mono text-xs text-ink-primary">
                {detail.content}
              </pre>
            </div>
          </>
        )}
      </div>
    </Dialog>
  )
}

function Field({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <div className="text-xs uppercase tracking-wide text-ink-muted">{label}</div>
      <div className={`mt-1 break-words text-ink-primary ${mono ? 'font-mono text-xs' : ''}`}>{value || '—'}</div>
    </div>
  )
}
