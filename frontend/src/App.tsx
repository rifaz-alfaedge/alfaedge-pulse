import { useMemo, useState } from 'react'
import {
  useAlertLogs,
  useBackupLogs,
  useDatastores,
  useGuestLoadAverages,
  useGuests,
  useMonitoredHosts,
  useServers,
  useSettings,
} from './lib/hooks'
import { hostHealthStatus, isHostCritical, isHostWarning } from './lib/severity'
import { GuestUsageTable, HostUsageTable } from './components/UsageTables'
import { EMPTY_GUEST_FILTERS, EMPTY_HOST_FILTERS, type GuestFilters, type HostFilters, type Severity } from './lib/usageFilters'
import { AlertsPopup } from './components/AlertsPopup'
import { MultiSelectFilter } from './components/MultiSelectFilter'
import { ServerStatusStrip } from './components/ServerStatusStrip'
import { ThemeToggle } from './components/ThemeToggle'
import { NodeDetailDialog, type SelectedNode } from './components/NodeDetailDialog'
import { BackupsPanel } from './components/BackupsPanel'
import { AlertsPanel } from './components/AlertsPanel'
import { AiUsagePanel } from './components/AiUsagePanel'
import { AiInsightsPanel } from './components/AiInsightsPanel'
import { UptimePanel } from './components/UptimePanel'
import { LogsPanel } from './components/LogsPanel'
import { HostHealthPanel } from './components/HostHealthPanel'
import { HeartbeatDot } from './components/HeartbeatDot'
import { Tabs } from './components/Tabs'
import type { ProxmoxGuest, ProxmoxServer } from './lib/types'

// Host Health and Uptime always lead — the two "is anything actively
// wrong right now" tabs — followed by AI Usage, the renamed Overview
// ("Usage Metrics"), Backups, Alerts, and AI Insights last (a weekly-
// cadence tab, the lowest-traffic one here).
const MAIN_TABS = ['Host Health', 'Uptime', 'Logs', 'AI Usage', 'Usage Metrics', 'Backups', 'Alerts', 'AI Insights'] as const
const DEFAULT_WARNING_THRESHOLD = 85
const DEFAULT_CRITICAL_THRESHOLD = 95
const DEFAULT_POLL_SECONDS = 20

function App() {
  const { data: servers } = useServers()
  const { data: guests } = useGuests()
  const { data: datastores } = useDatastores()
  const { data: backupLogs } = useBackupLogs()
  const { data: alertLogs } = useAlertLogs()
  const { data: settings } = useSettings()
  const { data: monitoredHosts } = useMonitoredHosts()
  const { data: guestLoads } = useGuestLoadAverages()

  const warningThreshold = settings?.warning_threshold_percent ?? DEFAULT_WARNING_THRESHOLD
  const criticalThreshold = settings?.critical_threshold_percent ?? DEFAULT_CRITICAL_THRESHOLD
  const pollIntervalSeconds = settings?.poll_interval_seconds ?? DEFAULT_POLL_SECONDS

  const [mainTab, setMainTab] = useState<(typeof MAIN_TABS)[number]>('Host Health')
  const [serverFilter, setServerFilter] = useState<string[]>([])
  const [hostFilters, setHostFilters] = useState<HostFilters>(EMPTY_HOST_FILTERS)
  const [guestFilters, setGuestFilters] = useState<GuestFilters>(EMPTY_GUEST_FILTERS)
  const [selected, setSelected] = useState<SelectedNode | null>(null)

  const allServers = servers ?? []
  const allGuests = guests ?? []
  const allDatastores = datastores ?? []
  const allBackupLogs = backupLogs ?? []
  const allAlertLogs = alertLogs ?? []
  const allMonitoredHosts = monitoredHosts ?? []
  const hostHealthByGuest = new Map(allMonitoredHosts.map((h) => [h.proxmox_guest, h]))

  const pveHosts = allServers.filter((s) => s.server_type === 'PVE')
  const pbsInstances = allServers.filter((s) => s.server_type === 'PBS')
  // Production, then Staging, then Development — same ordering convention
  // as the card grid below, so the always-visible strip and the cards it
  // links into never disagree about which host comes first.
  const statusStripHosts = [...pveHosts].sort((a, b) => roleRank(a.role) - roleRank(b.role) || a.server_name.localeCompare(b.server_name))

  const serverFilterOptions = useMemo(
    () => [...allServers.map((s) => s.server_name)].sort((a, b) => a.localeCompare(b)),
    [allServers],
  )

  const hostIsCritical = (server: ProxmoxServer) => isHostCritical(server, allDatastores)
  const hostIsWarning = (server: ProxmoxServer) => isHostWarning(server, allDatastores)
  const hostSeverity = (server: ProxmoxServer): Severity =>
    hostIsCritical(server) ? 'critical' : hostIsWarning(server) ? 'warning' : 'ok'
  // Combines Proxmox resource state AND Host Health — a guest whose Monitored
  // Host is Down/Critical reads as critical here even if its CPU/RAM/disk
  // are all fine, so Host Health actually drives the card's border/pulse
  // rather than being purely informational (see lib/severity.ts).
  const guestSeverity = (guest: ProxmoxGuest): Severity => {
    const hostHealth = hostHealthByGuest.get(guest.name)
    const hostHealthCritical = hostHealth ? hostHealthStatus(hostHealth) !== 'Online' : false
    return guest.is_critical || hostHealthCritical ? 'critical' : guest.is_warning ? 'warning' : 'ok'
  }

  // The header's Servers filter narrows both tables before their own
  // per-table filters apply.
  const serverDocNames = new Set(allServers.filter((s) => serverFilter.includes(s.server_name)).map((s) => s.name))
  const tableHosts = [...pveHosts, ...pbsInstances]
    .filter((s) => serverFilter.length === 0 || serverDocNames.has(s.name))
    .sort((a, b) => roleRank(a.role) - roleRank(b.role) || a.server_name.localeCompare(b.server_name))
  const tableGuests = allGuests.filter((g) => serverFilter.length === 0 || serverDocNames.has(g.server))

  // The status strip's Critical/Offline pills drive the tables' own
  // Severity/Status filters, so the pill and the filter bar never disagree.
  const onlyCritical = (f: string[]) => f.length === 1 && f[0] === 'Critical'
  const criticalActive = onlyCritical(hostFilters.severity) && onlyCritical(guestFilters.severity)
  const offlineActive = hostFilters.status.length === 1 && hostFilters.status[0] === 'Offline'

  // Backup log rows are kept forever, including for guests that have since
  // been stopped or removed (see Proxmox Backup Log's own doctype notes) —
  // useful for the per-guest history in NodeDetailDialog, but it means the
  // fleet-wide Backups tab would otherwise fill up with old entries for
  // instances nobody is running anymore. That tab only makes sense scoped
  // to guests that are actually active right now.
  const runningGuestNames = new Set(allGuests.filter((g) => g.status === 'running').map((g) => g.name))
  const activeBackupLogs = allBackupLogs.filter((b) => b.guest && runningGuestNames.has(b.guest))

  const criticalCount =
    allServers.filter(hostIsCritical).length + allGuests.filter((g) => g.is_critical).length
  const offlineCount = allServers.filter((s) => s.status === 'Offline').length
  const openAlertCount = allAlertLogs.filter((a) => !a.resolved).length
  const mostRecentSync = [...allServers, ...allGuests]
    .map((n) => n.last_synced)
    .filter(Boolean)
    .sort()
    .at(-1)

  return (
    <div className="mx-auto max-w-[96rem] px-6 pb-10 sm:px-10 lg:px-16">
      <ServerStatusStrip
        servers={statusStripHosts}
        datastores={allDatastores}
        warningThreshold={warningThreshold}
        criticalThreshold={criticalThreshold}
        criticalCount={criticalCount}
        offlineCount={offlineCount}
        openAlertCount={openAlertCount}
        criticalActive={mainTab === 'Usage Metrics' && criticalActive}
        offlineActive={mainTab === 'Usage Metrics' && offlineActive}
        openAlertsActive={mainTab === 'Alerts'}
        onCriticalClick={() => {
          setMainTab('Usage Metrics')
          setServerFilter([])
          const on = !criticalActive
          setHostFilters({ ...EMPTY_HOST_FILTERS, severity: on ? ['Critical'] : [] })
          setGuestFilters({ ...EMPTY_GUEST_FILTERS, severity: on ? ['Critical'] : [] })
        }}
        onOfflineClick={() => {
          setMainTab('Usage Metrics')
          setServerFilter([])
          setHostFilters({ ...EMPTY_HOST_FILTERS, status: offlineActive ? [] : ['Offline'] })
          setGuestFilters(EMPTY_GUEST_FILTERS)
        }}
        onOpenAlertsClick={() => setMainTab('Alerts')}
      />

      <header className="mb-10 flex flex-wrap items-center justify-between gap-6 pt-10">
        <div>
          {/* Relative path, not the site's full domain — this dashboard rides on
           * whatever Frappe site it's installed on, and "/app" (the Desk's default
           * authenticated landing route) resolves correctly against any of them,
           * including this deployment's own https://frappe.example.com. */}
          <a
            href="/app"
            className="mb-2 flex w-fit items-center gap-1 text-xs font-medium text-ink-muted hover:text-ink-primary"
          >
            <span aria-hidden>←</span> Back to Frappe
          </a>
          {/* A real link (full navigation) rather than an onClick state-reset —
           * "load the dashboard homepage" means a clean fresh load of this same
           * route, the same way clicking a site's logo conventionally does. */}
          <a href="/alfaedge-pulse" className="inline-block transition-opacity hover:opacity-80">
            <h1 className="text-3xl font-semibold tracking-tight text-ink-primary">alfaEdge Pulse</h1>
          </a>
          <div className="mt-2 flex items-center gap-2 text-sm text-ink-secondary">
            <HeartbeatDot lastSynced={mostRecentSync} pollIntervalSeconds={pollIntervalSeconds} />
            <span>Live · refreshes every few seconds</span>
          </div>
        </div>
        <div className="flex items-center gap-4">
          <MultiSelectFilter
            label="Servers"
            options={serverFilterOptions}
            selected={serverFilter}
            onChange={(next) => {
              setMainTab('Usage Metrics')
              setServerFilter(next)
            }}
          />
          <ThemeToggle />
        </div>
      </header>

      <div className="mb-8">
        <Tabs options={[...MAIN_TABS]} value={mainTab} onChange={(v) => setMainTab(v as (typeof MAIN_TABS)[number])} size="lg" />
      </div>

      {mainTab === 'Usage Metrics' && (
        <>
          <section className="mb-12">
            <h2 className="mb-4 text-sm font-semibold uppercase tracking-widest text-ink-muted">
              Hosts &amp; Backup Server
            </h2>
            <HostUsageTable
              servers={tableHosts}
              datastores={allDatastores}
              severityOf={hostSeverity}
              thresholds={{ warning: warningThreshold, critical: criticalThreshold }}
              loadThresholds={{ warning: guestLoads?.warning_percent ?? 100, critical: guestLoads?.critical_percent ?? 200 }}
              filters={hostFilters}
              onFiltersChange={setHostFilters}
              onOpen={(s) => setSelected({ kind: s.server_type === 'PBS' ? 'pbs' : 'host', doc: s })}
            />
          </section>

          <section>
            <h2 className="mb-4 text-sm font-semibold uppercase tracking-widest text-ink-muted">
              Virtual Machines &amp; Containers
            </h2>
            <GuestUsageTable
              guests={tableGuests}
              servers={allServers}
              monitoredHosts={allMonitoredHosts}
              loads={guestLoads}
              severityOf={guestSeverity}
              thresholds={{ warning: warningThreshold, critical: criticalThreshold }}
              backupOverdueHours={settings?.backup_overdue_hours ?? 26}
              filters={guestFilters}
              onFiltersChange={setGuestFilters}
              onOpen={(g) => setSelected({ kind: 'guest', doc: g })}
            />
          </section>
        </>
      )}

      {mainTab === 'Backups' && <BackupsPanel backupLogs={activeBackupLogs} guests={allGuests} servers={allServers} />}
      {mainTab === 'Alerts' && <AlertsPanel alertLogs={allAlertLogs} />}
      {mainTab === 'AI Usage' && <AiUsagePanel />}
      {mainTab === 'AI Insights' && <AiInsightsPanel />}
      {mainTab === 'Uptime' && <UptimePanel />}
      {mainTab === 'Host Health' && <HostHealthPanel />}
      {mainTab === 'Logs' && <LogsPanel />}

      <AlertsPopup />

      <NodeDetailDialog
        selected={selected}
        onClose={() => setSelected(null)}
        datastores={allDatastores}
        backupLogs={allBackupLogs}
        alertLogs={allAlertLogs}
      />
    </div>
  )
}

// Dashboard-wide ordering: every Production server (and its guests) is
// shown as a block before Staging, then Development, then PBS/Backup —
// a role not in this map (unset, or a future addition) sorts last, after
// Backup, rather than crashing or floating unpredictably to the front.
const ROLE_ORDER: Record<string, number> = { Production: 0, Staging: 1, Development: 2, Backup: 3 }
const roleRank = (role?: string) => (role !== undefined && role in ROLE_ORDER ? ROLE_ORDER[role] : 4)

export default App
