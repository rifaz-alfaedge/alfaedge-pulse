import { useMemo } from 'react'
import { useFrappeGetCall, useFrappeGetDoc, useFrappeGetDocList } from 'frappe-react-sdk'
import type {
  BifrostSettings,
  FailedJobGroup,
  FrappeActiveJob,
  FrappeFailedJobLog,
  FrappeWorkerHealthLog,
  HostMonitorSettings,
  ImportableMonitor,
  LlmUsageLog,
  MonitoredHost,
  MonitoredHostSite,
  ProxmoxAlertLog,
  ProxmoxBackupLog,
  ProxmoxDatastore,
  ProxmoxGuest,
  ProxmoxMonitorSettings,
  ProxmoxServer,
  ServiceStatusLog,
  UptimeHistoryPoint,
  UptimeKumaInstance,
  UptimeMonitorSettings,
  UptimeSite,
  UptimeSummary,
  UsageBreakdownRow,
  UsageFilterOptions,
  UsageSummary,
  UsageTrendPoint,
} from './types'

/** How often the dashboard itself re-fetches — deliberately faster than the
 * ~20s backend poll cadence so the UI feels responsive the instant new data
 * lands, without hammering the server (SWR dedupes identical in-flight
 * requests, so overlapping polls are cheap). */
const UI_POLL_MS = 7000

const SERVER_FIELDS: (keyof ProxmoxServer)[] = [
  'name', 'server_name', 'hostname', 'port', 'server_type', 'role', 'datacenter_location',
  'cloud_provider', 'local_backup_retention', 'enabled', 'status', 'is_critical', 'backup_critical',
  'cpu_usage', 'memory_usage', 'memory_total', 'swap_usage', 'swap_total', 'storage_usage', 'storage_total',
  'uptime', 'last_synced', 'last_error',
]

const GUEST_FIELDS: (keyof ProxmoxGuest)[] = [
  'name', 'server', 'node', 'vmid', 'guest_name', 'guest_type', 'status', 'is_critical', 'is_warning',
  'cpu_usage', 'memory_usage', 'memory_total', 'disk_usage', 'disk_total', 'uptime',
  'last_synced', 'last_successful_backup', 'ip_address', 'network_mode',
  'public_ip', 'access_url', 'assigned_engineer', 'tags',
]

const DATASTORE_FIELDS: (keyof ProxmoxDatastore)[] = [
  'name', 'server', 'datastore_name', 'datastore_type', 'storage_role', 'is_critical', 'is_warning',
  'used', 'total', 'usage_percent', 'last_synced',
]

const ALERT_LOG_FIELDS: (keyof ProxmoxAlertLog)[] = [
  'name', 'alert_type', 'reference_doctype', 'reference_name', 'message',
  'channels_sent', 'sent_at', 'resolved',
]

export function useServers() {
  return useFrappeGetDocList<ProxmoxServer>(
    'Proxmox Server',
    { fields: SERVER_FIELDS, limit: 0, orderBy: { field: 'server_name', order: 'asc' } },
    undefined,
    { refreshInterval: UI_POLL_MS },
  )
}

export function useGuests() {
  return useFrappeGetDocList<ProxmoxGuest>(
    'Proxmox Guest',
    { fields: GUEST_FIELDS, limit: 0, orderBy: { field: 'guest_name', order: 'asc' } },
    undefined,
    { refreshInterval: UI_POLL_MS },
  )
}

export function useDatastores() {
  return useFrappeGetDocList<ProxmoxDatastore>(
    'Proxmox Datastore',
    { fields: DATASTORE_FIELDS, limit: 0 },
    undefined,
    { refreshInterval: UI_POLL_MS },
  )
}

// A plain "most recent N rows overall" doctype-list query starves whichever
// server (or, within one server, whichever backup source) backs up less
// often than its siblings — one host's high-frequency PBS-remote jobs alone
// can fill a shared window and push a lower-volume host's, or even that
// same host's own less-frequent Local Disk history, out of it entirely.
// The backend fetches per (server, backup_source) bucket and merges
// instead, so every combination that has data is guaranteed its own
// window — see alfaedge_pulse.api.get_recent_backup_logs.
export function useBackupLogs(perBucketLimit = 150) {
  const result = useFrappeGetCall<{ message: ProxmoxBackupLog[] }>(
    'alfaedge_pulse.api.get_recent_backup_logs',
    { per_bucket_limit: perBucketLimit },
    undefined,
    { refreshInterval: UI_POLL_MS },
  )
  return { ...result, data: result.data?.message }
}

export function useAlertLogs(limit = 100) {
  return useFrappeGetDocList<ProxmoxAlertLog>(
    'Proxmox Alert Log',
    { fields: ALERT_LOG_FIELDS, limit, orderBy: { field: 'sent_at', order: 'desc' } },
    undefined,
    { refreshInterval: UI_POLL_MS },
  )
}

/** Settings changes rarely, so this refreshes far less often than the live data hooks.
 * A Single DocType's document name always equals its DocType name in Frappe. */
export function useSettings() {
  return useFrappeGetDoc<ProxmoxMonitorSettings>(
    'Proxmox Monitor Settings',
    'Proxmox Monitor Settings',
    undefined,
    { refreshInterval: 60000 },
  )
}

// ---------------------------------------------------------------------------
// AI Usage (Bifrost) — this data only changes on Bifrost Settings'
// sync_interval_minutes cadence (minutes, not seconds), so every hook here
// uses the same slow ~60s refresh as useSettings rather than the live
// dashboard's 7s UI_POLL_MS, which would just be wasted requests.
// ---------------------------------------------------------------------------
const SLOW_POLL_MS = 60000

export function useBifrostSettings() {
  return useFrappeGetDoc<BifrostSettings>('Bifrost Settings', 'Bifrost Settings', undefined, {
    refreshInterval: SLOW_POLL_MS,
  })
}

/** Shared shape for the AI Usage tab's provider/model/virtual-key filter
 * bar — every hook below accepts the same three optional multi-select
 * filters, on top of its own required params. Empty/absent means "All",
 * same convention as the single-value strings this replaced. */
export interface UsageFilters {
  provider?: string[]
  model?: string[]
  virtualKeyName?: string[]
}

/** Whitelisted GET params are always strings on the wire — a multi-select
 * array is JSON-encoded here and decoded server-side via
 * `frappe.parse_json` (see `llm_usage_monitor/api.py::_parse_list`).
 * `undefined` (not `'[]'`) for an empty selection, so it's indistinguishable
 * from "not passed at all" to the backend's existing None-means-no-filter
 * checks. */
function serializeFilterList(values?: string[]): string | undefined {
  return values && values.length > 0 ? JSON.stringify(values) : undefined
}

export function useAiUsageSummary(startDate?: string, endDate?: string, source?: string, filters: UsageFilters = {}) {
  const result = useFrappeGetCall<{ message: UsageSummary }>(
    'alfaedge_pulse.llm_usage_monitor.api.get_usage_summary',
    {
      start_date: startDate,
      end_date: endDate,
      source,
      provider: serializeFilterList(filters.provider),
      model: serializeFilterList(filters.model),
      virtual_key_name: serializeFilterList(filters.virtualKeyName),
    },
    undefined,
    { refreshInterval: SLOW_POLL_MS },
  )
  return { ...result, data: result.data?.message }
}

export function useAiUsageTrend(
  startDate?: string,
  endDate?: string,
  groupBy: 'day' | 'hour' = 'day',
  source?: string,
  filters: UsageFilters = {},
) {
  const result = useFrappeGetCall<{ message: UsageTrendPoint[] }>(
    'alfaedge_pulse.llm_usage_monitor.api.get_usage_trend',
    {
      start_date: startDate,
      end_date: endDate,
      group_by: groupBy,
      source,
      provider: serializeFilterList(filters.provider),
      model: serializeFilterList(filters.model),
      virtual_key_name: serializeFilterList(filters.virtualKeyName),
    },
    undefined,
    { refreshInterval: SLOW_POLL_MS },
  )
  return { ...result, data: result.data?.message }
}

export function useAiUsageBreakdown(
  dimension: 'provider' | 'model' | 'virtual_key_name',
  startDate?: string,
  endDate?: string,
  source?: string,
  filters: UsageFilters = {},
) {
  const result = useFrappeGetCall<{ message: UsageBreakdownRow[] }>(
    'alfaedge_pulse.llm_usage_monitor.api.get_breakdown',
    {
      dimension,
      start_date: startDate,
      end_date: endDate,
      source,
      provider: serializeFilterList(filters.provider),
      model: serializeFilterList(filters.model),
      virtual_key_name: serializeFilterList(filters.virtualKeyName),
    },
    undefined,
    { refreshInterval: SLOW_POLL_MS },
  )
  return { ...result, data: result.data?.message }
}

export function useAiUsageFilterOptions() {
  const result = useFrappeGetCall<{ message: UsageFilterOptions }>(
    'alfaedge_pulse.llm_usage_monitor.api.get_filter_options',
    {},
    undefined,
    { refreshInterval: SLOW_POLL_MS },
  )
  return { ...result, data: result.data?.message }
}

// ---------------------------------------------------------------------------
// Uptime (Uptime Kuma) — instance/site status changes roughly every minute
// (the poll cadence), so those two use the live UI_POLL_MS like the rest of
// the dashboard; the aggregate summary/history/settings hooks use the slow
// poll like everything else in this file that only changes occasionally.
// ---------------------------------------------------------------------------

export function useUptimeInstances() {
  return useFrappeGetDocList<UptimeKumaInstance>(
    'Uptime Kuma Instance',
    {
      fields: ['name', 'instance_name', 'base_url', 'enabled', 'verify_ssl', 'last_synced', 'last_error'],
      limit: 0,
      orderBy: { field: 'instance_name', order: 'asc' },
    },
    undefined,
    { refreshInterval: UI_POLL_MS },
  )
}

export function useUptimeSites() {
  return useFrappeGetDocList<UptimeSite>(
    'Uptime Site',
    {
      fields: [
        'name', 'instance', 'site_name', 'kuma_monitor_id', 'monitor_type', 'url', 'hostname', 'port',
        'check_interval_seconds', 'is_active', 'current_status', 'is_critical', 'last_checked',
      ],
      limit: 0,
      orderBy: { field: 'site_name', order: 'asc' },
    },
    undefined,
    { refreshInterval: UI_POLL_MS },
  )
}

/** Not polled — only fetched on demand when the "Import Existing Monitors"
 * modal opens for a given instance (`swrKey` left undefined, so passing an
 * empty `instance` disables the call entirely rather than hitting the
 * endpoint with a meaningless value). */
export function useImportableMonitors(instance: string) {
  const result = useFrappeGetCall<{ message: ImportableMonitor[] }>(
    'alfaedge_pulse.uptime_monitor.api.list_importable_monitors',
    { instance },
    instance ? undefined : null,
  )
  return { ...result, data: result.data?.message }
}

export function useUptimeMonitorSettings() {
  return useFrappeGetDoc<UptimeMonitorSettings>('Uptime Monitor Settings', 'Uptime Monitor Settings', undefined, {
    refreshInterval: SLOW_POLL_MS,
  })
}

export function useUptimeSummary(site?: string, days = 7) {
  const result = useFrappeGetCall<{ message: UptimeSummary }>(
    'alfaedge_pulse.uptime_monitor.api.get_uptime_summary',
    { site, days },
    undefined,
    { refreshInterval: SLOW_POLL_MS },
  )
  return { ...result, data: result.data?.message }
}

export function useUptimeHistory(site: string, days = 7) {
  const result = useFrappeGetCall<{ message: UptimeHistoryPoint[] }>(
    'alfaedge_pulse.uptime_monitor.api.get_uptime_history',
    { site, days },
    undefined,
    { refreshInterval: SLOW_POLL_MS },
  )
  return { ...result, data: result.data?.message }
}

// ---------------------------------------------------------------------------
// Host Health — service/worker/failed-job status changes on every agent
// push (~20-30s, see host_health/agent), so the "live" doctypes below use
// UI_POLL_MS like the rest of the dashboard; Host Monitor Settings and the
// site-inventory child table change as rarely as any other settings/
// inventory data, so those two use SLOW_POLL_MS.
// ---------------------------------------------------------------------------

const MONITORED_HOST_FIELDS: (keyof MonitoredHost)[] = [
  'name', 'proxmox_guest', 'hostname', 'enabled', 'last_seen', 'is_online',
  'worker_health_critical', 'failed_job_critical', 'long_running_job_critical',
  'scheduler_last_run', 'scheduler_overdue',
]

export function useMonitoredHosts() {
  return useFrappeGetDocList<MonitoredHost>(
    'Monitored Host',
    {
      fields: MONITORED_HOST_FIELDS,
      filters: [['enabled', '=', 1]],
      limit: 0,
      orderBy: { field: 'hostname', order: 'asc' },
    },
    undefined,
    { refreshInterval: UI_POLL_MS },
  )
}

/** Monitored Host Site is a child table — Frappe's own permission engine
 * always checks a child doctype's permission against its *parent*
 * doctype's context, which the generic list API has no way to supply when
 * listing every row fleet-wide (confirmed live: `Insufficient Permission`
 * for a real non-Administrator account). Goes through a small whitelisted
 * wrapper instead (`host_health/api.py::get_hosted_sites`) that checks
 * read-on-Monitored-Host explicitly — see that function's own docstring. */
export function useMonitoredHostSites() {
  const result = useFrappeGetCall<{ message: MonitoredHostSite[] }>(
    'alfaedge_pulse.host_health.api.get_hosted_sites',
    {},
    undefined,
    { refreshInterval: SLOW_POLL_MS },
  )
  return { ...result, data: result.data?.message }
}

export function useServiceStatusLogs() {
  return useFrappeGetDocList<ServiceStatusLog>(
    'Service Status Log',
    {
      fields: [
        'name', 'monitored_host', 'service_name', 'unit_name', 'current_state',
        'is_down', 'down_streak', 'last_state_change', 'last_checked',
      ],
      limit: 0,
      orderBy: { field: 'service_name', order: 'asc' },
    },
    undefined,
    { refreshInterval: UI_POLL_MS },
  )
}

/** Append-only — only the most recent row per (monitored_host, bench_name)
 * is what the dashboard needs "live," so this pulls a bounded recent
 * window (newest first) and the panel groups client-side down to one row
 * per bench, rather than querying per-host/bench individually. */
export function useFrappeWorkerHealthLogs(limit = 300) {
  return useFrappeGetDocList<FrappeWorkerHealthLog>(
    'Frappe Worker Health Log',
    {
      fields: [
        'name', 'monitored_host', 'bench_name', 'timestamp', 'registered_workers', 'live_worker_count',
        'orphan_worker_count', 'orphan_critical_streak', 'queue_depths', 'failed_job_count', 'failed_job_critical_streak',
        'active_job_count', 'long_running_job_count', 'long_running_job_critical_streak',
      ],
      limit,
      orderBy: { field: 'timestamp', order: 'desc' },
    },
    undefined,
    { refreshInterval: UI_POLL_MS },
  )
}

/** Full history for exactly one (monitored_host, bench_name) within a
 * chosen window — unlike useFrappeWorkerHealthLogs' fleet-wide recent-300
 * cap (built for "what's the latest row per bench," not "show me a real
 * range"), this is what the trend chart's own date-range picker uses, so
 * picking a longer window doesn't just starve other hosts/benches out of
 * a shared window. `sinceMinutes` a fixed lookback (e.g. 360 = 6h); pass
 * an explicit `until` only for a custom/non-"now" range. */
export function useWorkerHealthHistory(monitoredHost: string, benchName: string, sinceMinutes: number, until?: string) {
  // Bucketed to the minute, and memoized, rather than computed fresh from
  // Date.now() on every render: a raw Date.now() value changes by however
  // many milliseconds elapsed between renders, which — since it's baked
  // into the filters below — makes useFrappeGetDocList (SWR under the
  // hood) see a "new" query on every single render, not just every poll
  // tick, discarding cached data and flashing the whole stats row back to
  // its loading state. Confirmed live: this is exactly what was causing
  // Bench Health's numbers to flicker every few seconds. Recomputing once
  // per minute keeps the filter value stable across the vastly more
  // frequent re-renders this component goes through (its own poll tick,
  // sibling state changes, etc.) while still sliding forward over time.
  const minuteBucket = Math.floor(Date.now() / 60_000)
  const sinceStr = useMemo(() => {
    const since = new Date(minuteBucket * 60_000 - sinceMinutes * 60_000)
    const pad = (n: number) => String(n).padStart(2, '0')
    // Local-time string, NOT toISOString() (which is UTC) — this app's
    // datetimes are naive-local throughout (see lib/dateRange.ts's own
    // note on the same convention), and `timestamp` here is stored via
    // Frappe's now_datetime(), also naive-local.
    return `${since.getFullYear()}-${pad(since.getMonth() + 1)}-${pad(since.getDate())} ${pad(since.getHours())}:${pad(since.getMinutes())}:${pad(since.getSeconds())}`
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [minuteBucket, sinceMinutes])
  return useFrappeGetDocList<FrappeWorkerHealthLog>(
    'Frappe Worker Health Log',
    {
      fields: [
        'name', 'monitored_host', 'bench_name', 'timestamp', 'registered_workers', 'live_worker_count',
        'orphan_worker_count', 'orphan_critical_streak', 'queue_depths', 'failed_job_count', 'failed_job_critical_streak',
        'active_job_count', 'long_running_job_count', 'long_running_job_critical_streak',
      ],
      filters: until
        ? [
            ['monitored_host', '=', monitoredHost],
            ['bench_name', '=', benchName],
            ['timestamp', '>=', sinceStr],
            ['timestamp', '<=', until],
          ]
        : [
            ['monitored_host', '=', monitoredHost],
            ['bench_name', '=', benchName],
            ['timestamp', '>=', sinceStr],
          ],
      limit: 0,
      orderBy: { field: 'timestamp', order: 'asc' },
    },
    undefined,
    { refreshInterval: UI_POLL_MS },
  )
}

/** Only open (unresolved) rows — the actionable ones for a live dashboard.
 * Resolved history is drill-down-only territory, not needed fleet-wide. */
export function useFrappeFailedJobLogs() {
  return useFrappeGetDocList<FrappeFailedJobLog>(
    'Frappe Failed Job Log',
    {
      fields: [
        'name',
        'monitored_host',
        'bench_name',
        'queue_name',
        'rq_job_id',
        'job_name',
        'exc_type',
        'failure_signature',
        'failed_at',
        'first_seen',
        'last_seen',
        'resolved',
      ],
      filters: [['resolved', '=', 0]],
      limit: 0,
      orderBy: { field: 'last_seen', order: 'desc' },
    },
    undefined,
    { refreshInterval: UI_POLL_MS },
  )
}

/** Every currently-executing RQ job fleet-wide — a live snapshot (see
 * FrappeActiveJob's own docstring), so unlike failed jobs there's no
 * resolved/unresolved filter here: a finished job's row is simply gone by
 * the next push, nothing to filter out. Sorted longest-running first so
 * the worst offender fleet-wide is always the first row, matching the
 * doctype's own default sort. */
export function useFrappeActiveJobs() {
  return useFrappeGetDocList<FrappeActiveJob>(
    'Frappe Active Job',
    {
      fields: [
        'name', 'monitored_host', 'bench_name', 'queue_name', 'rq_job_id',
        'job_name', 'worker_pid', 'started_at', 'last_seen', 'elapsed_seconds', 'is_long_running',
      ],
      limit: 0,
      orderBy: { field: 'elapsed_seconds', order: 'desc' },
    },
    undefined,
    { refreshInterval: UI_POLL_MS },
  )
}

/** Human-readable root-cause summary for one host's failed jobs — the
 * inline counterpart to `host_health.api.get_failed_job_log_text`'s
 * downloadable file, for a reader who wants the gist without downloading
 * anything. Only ever called with a real host name (the dialog that uses
 * this only mounts once a host is selected), so no undefined-skip guard
 * needed here. */
export function useFailedJobGroups(monitoredHost: string) {
  const result = useFrappeGetCall<{ message: FailedJobGroup[] }>(
    'alfaedge_pulse.host_health.api.get_failed_job_groups',
    { monitored_host: monitoredHost },
    undefined,
    { refreshInterval: UI_POLL_MS },
  )
  return { ...result, data: result.data?.message }
}

export function useHostMonitorSettings() {
  return useFrappeGetDoc<HostMonitorSettings>('Host Monitor Settings', 'Host Monitor Settings', undefined, {
    refreshInterval: SLOW_POLL_MS,
  })
}

export function useAiRecentRequests(
  startDate?: string,
  endDate?: string,
  source?: string,
  filters: UsageFilters = {},
  limit = 50,
  offset = 0,
  sortBy: 'request_timestamp' | 'latency_ms' | 'total_tokens' | 'total_cost' = 'request_timestamp',
  order: 'asc' | 'desc' = 'desc',
) {
  const result = useFrappeGetCall<{ message: { rows: LlmUsageLog[]; total_count: number } }>(
    'alfaedge_pulse.llm_usage_monitor.api.get_recent_requests',
    {
      start_date: startDate,
      end_date: endDate,
      source,
      provider: serializeFilterList(filters.provider),
      model: serializeFilterList(filters.model),
      virtual_key_name: serializeFilterList(filters.virtualKeyName),
      limit,
      offset,
      sort_by: sortBy,
      order,
    },
    undefined,
    { refreshInterval: SLOW_POLL_MS },
  )
  return { ...result, data: result.data?.message }
}
