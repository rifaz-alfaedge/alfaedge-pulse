import { useFrappeGetCall, useFrappeGetDoc, useFrappeGetDocList } from 'frappe-react-sdk'
import type {
  BifrostSettings,
  ImportableMonitor,
  LlmUsageLog,
  ProxmoxAlertLog,
  ProxmoxBackupLog,
  ProxmoxDatastore,
  ProxmoxGuest,
  ProxmoxMonitorSettings,
  ProxmoxServer,
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
  'name', 'server_name', 'hostname', 'server_type', 'role', 'datacenter_location',
  'cloud_provider', 'local_backup_retention', 'enabled', 'status', 'is_critical', 'backup_critical',
  'cpu_usage', 'memory_usage', 'memory_total', 'swap_usage', 'swap_total', 'storage_usage', 'storage_total',
  'uptime', 'last_synced', 'last_error',
]

const GUEST_FIELDS: (keyof ProxmoxGuest)[] = [
  'name', 'server', 'vmid', 'guest_name', 'guest_type', 'status', 'is_critical', 'is_warning',
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
// window — see proxmox_monitor.api.get_recent_backup_logs.
export function useBackupLogs(perBucketLimit = 150) {
  const result = useFrappeGetCall<{ message: ProxmoxBackupLog[] }>(
    'proxmox_monitor.api.get_recent_backup_logs',
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
 * bar — every hook below accepts the same three optional exact-match
 * filters, on top of its own required params. */
export interface UsageFilters {
  provider?: string
  model?: string
  virtualKeyName?: string
}

export function useAiUsageSummary(startDate?: string, endDate?: string, source?: string, filters: UsageFilters = {}) {
  const result = useFrappeGetCall<{ message: UsageSummary }>(
    'proxmox_monitor.llm_usage_monitor.api.get_usage_summary',
    {
      start_date: startDate,
      end_date: endDate,
      source,
      provider: filters.provider,
      model: filters.model,
      virtual_key_name: filters.virtualKeyName,
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
    'proxmox_monitor.llm_usage_monitor.api.get_usage_trend',
    {
      start_date: startDate,
      end_date: endDate,
      group_by: groupBy,
      source,
      provider: filters.provider,
      model: filters.model,
      virtual_key_name: filters.virtualKeyName,
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
    'proxmox_monitor.llm_usage_monitor.api.get_breakdown',
    {
      dimension,
      start_date: startDate,
      end_date: endDate,
      source,
      provider: filters.provider,
      model: filters.model,
      virtual_key_name: filters.virtualKeyName,
    },
    undefined,
    { refreshInterval: SLOW_POLL_MS },
  )
  return { ...result, data: result.data?.message }
}

export function useAiUsageFilterOptions() {
  const result = useFrappeGetCall<{ message: UsageFilterOptions }>(
    'proxmox_monitor.llm_usage_monitor.api.get_filter_options',
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
    'proxmox_monitor.uptime_monitor.api.list_importable_monitors',
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
    'proxmox_monitor.uptime_monitor.api.get_uptime_summary',
    { site, days },
    undefined,
    { refreshInterval: SLOW_POLL_MS },
  )
  return { ...result, data: result.data?.message }
}

export function useUptimeHistory(site: string, days = 7) {
  const result = useFrappeGetCall<{ message: UptimeHistoryPoint[] }>(
    'proxmox_monitor.uptime_monitor.api.get_uptime_history',
    { site, days },
    undefined,
    { refreshInterval: SLOW_POLL_MS },
  )
  return { ...result, data: result.data?.message }
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
    'proxmox_monitor.llm_usage_monitor.api.get_recent_requests',
    {
      start_date: startDate,
      end_date: endDate,
      source,
      provider: filters.provider,
      model: filters.model,
      virtual_key_name: filters.virtualKeyName,
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
