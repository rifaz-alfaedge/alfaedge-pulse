import { useFrappeGetCall, useFrappeGetDoc, useFrappeGetDocList } from 'frappe-react-sdk'
import type {
  ProxmoxAlertLog,
  ProxmoxBackupLog,
  ProxmoxDatastore,
  ProxmoxGuest,
  ProxmoxMonitorSettings,
  ProxmoxServer,
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
