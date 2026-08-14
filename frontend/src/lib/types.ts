// Mirrors the Frappe DocTypes in proxmox_monitor/proxmox_fleet_monitor/doctype.
// Kept intentionally in sync by hand (no codegen) since the field set is
// small and stable — see the corresponding .json files for the source of truth.

export type ServerType = 'PVE' | 'PBS'
export type ServerStatus = 'Online' | 'Offline' | 'Warning' | 'Critical'
export type DatacenterLocation = 'Mumbai' | 'Singapore'

export interface ProxmoxServer {
  name: string
  server_name: string
  hostname: string
  server_type: ServerType
  role?: 'Production' | 'Development' | 'Staging' | 'Backup'
  datacenter_location?: DatacenterLocation
  cloud_provider?: string
  local_backup_retention?: string
  enabled: 0 | 1
  status: ServerStatus
  is_critical: 0 | 1
  backup_critical?: 0 | 1
  cpu_usage?: number
  memory_usage?: number
  memory_total?: number
  swap_usage?: number | null
  swap_total?: number
  storage_usage?: number
  storage_total?: number
  uptime?: number
  last_synced?: string
  last_error?: string
}

export type GuestType = 'QEMU (VM)' | 'LXC (CT)'
export type GuestStatus = 'running' | 'stopped' | 'paused' | 'unknown'
export type NetworkMode = 'Direct Public IP' | 'Internal Bridge + Reverse Proxy'

export interface ProxmoxGuest {
  name: string
  server: string
  vmid: number
  guest_name: string
  guest_type: GuestType
  status: GuestStatus
  is_critical: 0 | 1
  is_warning?: 0 | 1
  cpu_usage?: number
  memory_usage?: number
  memory_total?: number
  disk_usage?: number | null
  disk_total?: number
  uptime?: number
  last_synced?: string
  last_successful_backup?: string
  ip_address?: string
  network_mode?: NetworkMode
  public_ip?: string
  access_url?: string
  assigned_engineer?: string
  tags?: string
}

export type StorageRole = 'OS + Local Backup Drive' | 'Guest Storage (LVM-Thin)' | 'Other'

export interface ProxmoxDatastore {
  name: string
  server: string
  datastore_name: string
  datastore_type: 'PVE Storage' | 'PBS Datastore'
  storage_role: StorageRole
  is_critical: 0 | 1
  is_warning?: 0 | 1
  used?: number
  total?: number
  usage_percent?: number
  last_synced?: string
}

export type BackupSource = 'Local Disk' | 'PBS Remote'
export type BackupStatus = 'Success' | 'Failed' | 'Running'

export interface ProxmoxBackupLog {
  name: string
  server: string
  guest?: string
  vmid?: number
  backup_source: BackupSource
  status: BackupStatus
  backup_time?: string
  size?: number
  storage_target?: string
  notes?: string
  error_message?: string
}

export type AlertType = 'Critical Resource' | 'Resource Warning' | 'Backup Failure' | 'Backup Overdue' | 'Server Offline' | 'Site Down'

export interface ProxmoxAlertLog {
  name: string
  alert_type: AlertType
  reference_doctype?: string
  reference_name?: string
  message: string
  channels_sent?: string
  sent_at?: string
  resolved: 0 | 1
}

export interface ProxmoxMonitorSettings {
  poll_interval_seconds: number
  warning_threshold_percent: number
  critical_threshold_percent: number
  confirmation_checks: number
}

// Mirrors proxmox_monitor/llm_usage_monitor/doctype — see those .json files
// for the source of truth.

export type LlmUsageSource = 'Bifrost' | 'OpenAI Direct' | 'Google Direct' | 'Other'
export type LlmUsageStatus = 'processing' | 'success' | 'error'

export interface LlmUsageLog {
  name: string
  source: LlmUsageSource
  external_id: string
  provider?: string
  model?: string
  virtual_key_name?: string
  status: LlmUsageStatus
  request_timestamp?: string
  latency_ms?: number
  total_tokens?: number
  total_cost?: number
}

export interface BifrostSettings {
  enabled: 0 | 1
  base_url: string
  sync_interval_minutes: number
  initial_backfill_days: number
  last_synced?: string
  last_synced_through?: string
  last_sync_row_count?: number
  last_error?: string
}

export interface UsageSummary {
  total_requests: number
  total_tokens: number
  total_cost: number
  average_latency: number
  success_rate: number
}

export interface UsageTrendPoint {
  bucket: string
  total_requests: number
  total_tokens: number
  total_cost: number
}

export interface UsageBreakdownRow {
  label: string
  total_requests: number
  total_tokens: number
  total_cost: number
}

export interface UsageFilterOptions {
  providers: string[]
  models: string[]
  virtual_keys: string[]
}

// Mirrors proxmox_monitor/uptime_monitor/doctype — see those .json files
// for the source of truth.

export type UptimeMonitorType = 'HTTP(s)' | 'TCP' | 'Ping'
export type UptimeStatus = 'Up' | 'Down' | 'Pending' | 'Maintenance'

export interface UptimeKumaInstance {
  name: string
  instance_name: string
  base_url: string
  enabled: 0 | 1
  verify_ssl: 0 | 1
  last_synced?: string
  last_error?: string
}

export interface UptimeSite {
  name: string
  instance: string
  site_name: string
  kuma_monitor_id?: number
  monitor_type: UptimeMonitorType
  url?: string
  hostname?: string
  port?: number
  check_interval_seconds: number
  is_active: 0 | 1
  current_status: UptimeStatus
  is_critical: 0 | 1
  last_checked?: string
}

/** A Kuma monitor that already exists on an instance but has no matching
 * local Uptime Site yet — e.g. it was created directly in Kuma before that
 * instance was ever connected to alfaEdge Pulse. See `list_importable_monitors`. */
export interface ImportableMonitor {
  kuma_monitor_id: number
  site_name: string
  monitor_type: UptimeMonitorType
  url?: string
  hostname?: string
  port?: number
  check_interval_seconds: number
  is_active: 0 | 1
}

export interface UptimeMonitorSettings {
  poll_interval_seconds: number
  heartbeat_interval_seconds: number
  alert_window_checks: number
}

export interface UptimeSummary {
  total_checks: number
  uptime_percent: number | null
  avg_response_time_ms: number
}

export interface UptimeHistoryPoint {
  bucket: string
  uptime_percent: number | null
  avg_response_time_ms: number
}

// Mirrors proxmox_monitor/host_health/doctype — see those .json files
// for the source of truth.

export type ServiceState = 'active' | 'inactive' | 'failed' | 'activating' | 'deactivating' | 'unknown'

export interface MonitoredHost {
  name: string
  proxmox_guest: string
  hostname?: string
  enabled: 0 | 1
  last_seen?: string
  is_online: 0 | 1
  worker_health_critical: 0 | 1
  failed_job_critical: 0 | 1
  scheduler_last_run?: string
  scheduler_overdue: 0 | 1
}

/** Row of the `hosted_sites` child table — inventory only (no status field)
 * for this release, see Monitored Host Site's own doctype notes. `parent`
 * is the owning Monitored Host's `name`, queried directly rather than via
 * a per-host `useFrappeGetDoc` call since this child doctype's own rows
 * are ordinary DB rows like any other. */
export interface MonitoredHostSite {
  name: string
  parent: string
  bench_name?: string
  site_name: string
  site_url?: string
}

export interface ServiceStatusLog {
  name: string
  monitored_host: string
  service_name: string
  unit_name: string
  current_state: ServiceState
  is_down: 0 | 1
  down_streak: number
  last_state_change?: string
  last_checked?: string
}

/** Append-only — one row per agent push per (monitored_host, bench_name).
 * `queue_depths` is a Frappe JSON field, serialized as a raw JSON string
 * over the API (not auto-parsed), e.g. `'{"default": 2, "long": 0}'`. */
export interface FrappeWorkerHealthLog {
  name: string
  monitored_host: string
  bench_name: string
  timestamp: string
  registered_workers: number
  live_worker_count: number
  orphan_worker_count: number
  orphan_critical_streak: number
  queue_depths?: string
  failed_job_count: number
  failed_job_critical_streak: number
}

export interface FrappeFailedJobLog {
  name: string
  monitored_host: string
  bench_name: string
  queue_name?: string
  rq_job_id: string
  job_name?: string
  exc_type?: string
  failure_signature?: string
  failed_at?: string
  first_seen?: string
  last_seen?: string
  resolved: 0 | 1
}

export interface HostMonitorSettings {
  heartbeat_timeout_seconds: number
  confirmation_checks: number
  scheduler_heartbeat_timeout_minutes: number
  orphan_worker_warning_threshold: number
  orphan_worker_critical_threshold: number
  failed_job_warning_threshold: number
  failed_job_critical_threshold: number
}
