import { useEffect, useMemo, useRef, useState } from 'react'
import { Switch } from '@rtcamp/frappe-ui-react'
import { useAlertLogs, useBackupLogs, useDatastores, useGuests, useServers, useSettings } from './lib/hooks'
import { ResourceCard, type DiskMeter, type Severity } from './components/ResourceCard'
import { InstanceSeverityLists } from './components/InstanceSeverityLists'
import { UptimeSeverityLists } from './components/UptimeSeverityLists'
import { HostHealthSeverityLists } from './components/HostHealthSeverityLists'
import { NodeDetailDialog, type SelectedNode } from './components/NodeDetailDialog'
import { BackupsPanel } from './components/BackupsPanel'
import { AlertsPanel } from './components/AlertsPanel'
import { AiUsagePanel } from './components/AiUsagePanel'
import { UptimePanel } from './components/UptimePanel'
import { HostHealthPanel } from './components/HostHealthPanel'
import { HeartbeatDot } from './components/HeartbeatDot'
import { SearchInput } from './components/SearchInput'
import { Tabs } from './components/Tabs'
import type { ProxmoxDatastore, ProxmoxGuest, ProxmoxServer } from './lib/types'

/** Short, card-friendly label for a drive/datastore — the two-drive PVE
 * layout gets fixed short names; anything else (PBS datastores, unusual
 * storage types) falls back to its own name since "Other" alone isn't
 * meaningful on a card. */
function driveLabel(d: ProxmoxDatastore): string {
  if (d.storage_role === 'OS + Local Backup Drive') return 'OS+Backup'
  if (d.storage_role === 'Guest Storage (LVM-Thin)') return 'Guest LVM'
  return d.datastore_name
}

const MAIN_TABS = ['Overview', 'Backups', 'Alerts', 'AI Usage', 'Uptime', 'Host Health'] as const
const DEFAULT_WARNING_THRESHOLD = 85
const DEFAULT_CRITICAL_THRESHOLD = 95
const DEFAULT_POLL_SECONDS = 20

// How many guest cards render initially, and how many more get appended each
// time the scroll sentinel comes into view — keeps the DOM light as the
// fleet grows into the hundreds of VMs/CTs instead of rendering everything
// that matches the current filter/search all at once.
const GUEST_PAGE_SIZE = 12

type GuestSortField = 'cpu' | 'ram' | 'storage'
type SortDirection = 'asc' | 'desc'

const GUEST_SORT_OPTIONS: { field: GuestSortField; label: string }[] = [
  { field: 'cpu', label: 'CPU' },
  { field: 'ram', label: 'RAM' },
  { field: 'storage', label: 'Storage' },
]

// Whichever metric a card grid is currently sorted by leads that card's own
// meter rings too, so the value driving the order is always the first thing
// you see on it.
const GUEST_METER_ORDER: Record<GuestSortField, Array<'cpu' | 'ram' | 'disk'>> = {
  cpu: ['cpu', 'ram', 'disk'],
  ram: ['ram', 'cpu', 'disk'],
  storage: ['disk', 'cpu', 'ram'],
}

/** Guests missing a reading for the selected metric (e.g. disk usage with
 * no QEMU agent) always sort to the end, regardless of direction — an
 * unknown value is neither "highest" nor "lowest," just absent. */
function guestMetricValue(g: ProxmoxGuest, field: GuestSortField): number | null {
  if (field === 'cpu') return g.cpu_usage ?? null
  if (field === 'ram') return g.memory_usage ?? null
  return g.disk_usage ?? null
}

function sortGuestsByMetric(guests: ProxmoxGuest[], field: GuestSortField, direction: SortDirection) {
  const sign = direction === 'asc' ? 1 : -1
  return [...guests].sort((a, b) => {
    const av = guestMetricValue(a, field)
    const bv = guestMetricValue(b, field)
    if (av === null && bv === null) return 0
    if (av === null) return 1
    if (bv === null) return -1
    return (av - bv) * sign
  })
}

function App() {
  const { data: servers } = useServers()
  const { data: guests } = useGuests()
  const { data: datastores } = useDatastores()
  const { data: backupLogs } = useBackupLogs()
  const { data: alertLogs } = useAlertLogs()
  const { data: settings } = useSettings()

  const warningThreshold = settings?.warning_threshold_percent ?? DEFAULT_WARNING_THRESHOLD
  const criticalThreshold = settings?.critical_threshold_percent ?? DEFAULT_CRITICAL_THRESHOLD
  const pollIntervalSeconds = settings?.poll_interval_seconds ?? DEFAULT_POLL_SECONDS

  const [mainTab, setMainTab] = useState<(typeof MAIN_TABS)[number]>('Overview')
  const [serverFilter, setServerFilter] = useState<string>('All')
  const [criticalOnly, setCriticalOnly] = useState(false)
  const [offlineOnly, setOfflineOnly] = useState(false)
  const [guestSearch, setGuestSearch] = useState('')
  const [guestSortField, setGuestSortField] = useState<GuestSortField>('cpu')
  const [guestSortDirection, setGuestSortDirection] = useState<SortDirection>('desc')
  const [visibleGuestCount, setVisibleGuestCount] = useState(GUEST_PAGE_SIZE)
  const [selected, setSelected] = useState<SelectedNode | null>(null)

  const allServers = servers ?? []
  const allGuests = guests ?? []
  const allDatastores = datastores ?? []
  const allBackupLogs = backupLogs ?? []
  const allAlertLogs = alertLogs ?? []

  const pveHosts = allServers.filter((s) => s.server_type === 'PVE')
  const pbsInstances = allServers.filter((s) => s.server_type === 'PBS')

  const serverFilterOptions = useMemo(
    () => ['All', ...allServers.map((s) => s.server_name)],
    [allServers],
  )

  // A host's own is_critical only reflects CPU/RAM (see poller.py) — disk
  // criticality lives on its individual drives (Proxmox Datastore), so the
  // card/summary/filter all need "is this host critical" to mean "itself
  // or any of its drives," not just the server doctype's own flag.
  const isHostCritical = (server: ProxmoxServer) =>
    !!server.is_critical || allDatastores.some((d) => d.server === server.name && d.is_critical)
  // Same idea one tier down — a host's own Warning status comes through
  // `status` (poller.py reuses that field's existing Warning option rather
  // than a separate flag), while a drive's Warning still needs its own
  // Proxmox Datastore row.
  const isHostWarning = (server: ProxmoxServer) =>
    server.status === 'Warning' || allDatastores.some((d) => d.server === server.name && d.is_warning)
  const hostSeverity = (server: ProxmoxServer): Severity =>
    isHostCritical(server) ? 'critical' : isHostWarning(server) ? 'warning' : 'ok'
  const guestSeverity = (guest: ProxmoxGuest): Severity =>
    guest.is_critical ? 'critical' : guest.is_warning ? 'warning' : 'ok'

  const visibleHosts = filterAndSortServers(
    [...pveHosts, ...pbsInstances], serverFilter, criticalOnly, offlineOnly, isHostCritical,
  )
  const matchingGuests = sortGuestsByMetric(
    filterAndSortGuests(allGuests, allServers, serverFilter, criticalOnly, guestSearch),
    guestSortField,
    guestSortDirection,
  )
  const visibleGuests = matchingGuests.slice(0, visibleGuestCount)
  const hasMoreGuests = visibleGuestCount < matchingGuests.length

  // Backup log rows are kept forever, including for guests that have since
  // been stopped or removed (see Proxmox Backup Log's own doctype notes) —
  // useful for the per-guest history in NodeDetailDialog, but it means the
  // fleet-wide Backups tab would otherwise fill up with old entries for
  // instances nobody is running anymore. That tab only makes sense scoped
  // to guests that are actually active right now.
  const runningGuestNames = new Set(allGuests.filter((g) => g.status === 'running').map((g) => g.name))
  const activeBackupLogs = allBackupLogs.filter((b) => b.guest && runningGuestNames.has(b.guest))

  // Any change to what's being shown restarts pagination from the top,
  // matching the scroll position resetting back to the section itself.
  useEffect(() => {
    setVisibleGuestCount(GUEST_PAGE_SIZE)
  }, [serverFilter, criticalOnly, guestSearch, guestSortField, guestSortDirection])

  const sentinelRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    const el = sentinelRef.current
    if (!el || !hasMoreGuests) return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting) {
          setVisibleGuestCount((c) => c + GUEST_PAGE_SIZE)
        }
      },
      { rootMargin: '600px' },
    )
    observer.observe(el)
    return () => observer.disconnect()
  }, [hasMoreGuests])

  const criticalCount =
    allServers.filter(isHostCritical).length + allGuests.filter((g) => g.is_critical).length
  const offlineCount = allServers.filter((s) => s.status === 'Offline').length
  const openAlertCount = allAlertLogs.filter((a) => !a.resolved).length
  const mostRecentSync = [...allServers, ...allGuests]
    .map((n) => n.last_synced)
    .filter(Boolean)
    .sort()
    .at(-1)

  return (
    <div className="mx-auto max-w-[96rem] px-6 py-10 sm:px-10 lg:px-16">
      <header className="mb-10 flex flex-wrap items-center justify-between gap-6">
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
        <div className="flex gap-4">
          <SummaryStat
            label="Critical"
            value={criticalCount}
            tone={criticalCount > 0 ? 'critical' : 'ok'}
            active={mainTab === 'Overview' && criticalOnly}
            onClick={() => {
              setMainTab('Overview')
              setServerFilter('All')
              setOfflineOnly(false)
              setCriticalOnly((v) => !v)
            }}
          />
          <SummaryStat
            label="Offline"
            value={offlineCount}
            tone={offlineCount > 0 ? 'warning' : 'ok'}
            active={mainTab === 'Overview' && offlineOnly}
            onClick={() => {
              setMainTab('Overview')
              setServerFilter('All')
              setCriticalOnly(false)
              setOfflineOnly((v) => !v)
            }}
          />
          <SummaryStat
            label="Open Alerts"
            value={openAlertCount}
            tone={openAlertCount > 0 ? 'warning' : 'ok'}
            active={mainTab === 'Alerts'}
            onClick={() => setMainTab('Alerts')}
          />
        </div>
      </header>

      <div className="mb-8">
        <Tabs options={[...MAIN_TABS]} value={mainTab} onChange={(v) => setMainTab(v as (typeof MAIN_TABS)[number])} />
      </div>

      <InstanceSeverityLists
        servers={allServers}
        guests={allGuests}
        datastores={allDatastores}
        isHostCritical={isHostCritical}
        isHostWarning={isHostWarning}
      />
      <UptimeSeverityLists />
      <HostHealthSeverityLists />

      {mainTab === 'Overview' && (
        <>
          <div className="mb-8 flex flex-wrap items-center justify-between gap-4">
            <Tabs options={serverFilterOptions} value={serverFilter} onChange={setServerFilter} />
            <div className="flex items-center gap-5">
              {offlineOnly && (
                <span className="text-xs font-medium text-status-warning">Showing offline hosts only</span>
              )}
              <Switch
                value={criticalOnly}
                onChange={(v) => {
                  setCriticalOnly(v)
                  if (v) setOfflineOnly(false)
                }}
                label="Critical only"
                size="sm"
              />
            </div>
          </div>

          <section className="mb-14">
            <h2 className="mb-5 text-sm font-semibold uppercase tracking-widest text-ink-muted">
              Hosts &amp; Backup Server
            </h2>
            {visibleHosts.length === 0 ? (
              <EmptyState message="No Proxmox Server connected yet — add one to get started." />
            ) : (
              <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 xl:grid-cols-3">
                {visibleHosts.map((s) => {
                  // Swap sits right after RAM, ahead of the real physical
                  // drives — informational only (see poller.py), and only
                  // ever shown on a host card; individual VM/CT cards don't
                  // get one (Proxmox doesn't expose a guest's own swap use).
                  const disks: DiskMeter[] = [
                    { label: 'Swap', value: s.swap_usage },
                    ...allDatastores
                      .filter((d) => d.server === s.name)
                      .map((d) => ({ label: driveLabel(d), value: d.usage_percent })),
                  ]
                  return (
                    <ResourceCard
                      key={s.name}
                      kind={s.server_type === 'PBS' ? 'pbs' : 'host'}
                      title={s.server_name}
                      subtitle={`${s.hostname}${s.datacenter_location ? ` · ${s.datacenter_location}` : ''}`}
                      status={s.status}
                      severity={hostSeverity(s)}
                      cpu={s.cpu_usage}
                      memory={s.memory_usage}
                      disks={disks}
                      lastSynced={s.last_synced}
                      pollIntervalSeconds={pollIntervalSeconds}
                      warningThreshold={warningThreshold}
                      criticalThreshold={criticalThreshold}
                      onClick={() => setSelected({ kind: s.server_type === 'PBS' ? 'pbs' : 'host', doc: s })}
                    />
                  )
                })}
              </div>
            )}
          </section>

          <section>
            <div className="mb-5 flex flex-wrap items-center justify-between gap-4">
              <h2 className="text-sm font-semibold uppercase tracking-widest text-ink-muted">
                Virtual Machines &amp; Containers
              </h2>
              <SearchInput value={guestSearch} onChange={setGuestSearch} placeholder="Search by hostname…" />
            </div>

            {matchingGuests.length === 0 ? (
              <EmptyState
                message={
                  guestSearch
                    ? `No VMs or CTs match "${guestSearch}".`
                    : "No VMs or CTs discovered yet — they'll appear automatically once a server is connected."
                }
              />
            ) : (
              <>
                <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                  <p className="text-sm text-ink-muted">
                    Showing {visibleGuests.length} of {matchingGuests.length}
                  </p>
                  <div className="flex items-center gap-1 text-sm">
                    <span className="mr-1 text-ink-muted">Sort by</span>
                    {GUEST_SORT_OPTIONS.map(({ field, label }) => (
                      <button
                        key={field}
                        type="button"
                        onClick={() => setGuestSortField(field)}
                        className={`rounded-full px-3 py-1 transition-colors ${
                          guestSortField === field
                            ? 'bg-accent text-white'
                            : 'text-ink-secondary hover:bg-surface-card'
                        }`}
                      >
                        {label}
                      </button>
                    ))}
                    <button
                      type="button"
                      onClick={() => setGuestSortDirection((d) => (d === 'asc' ? 'desc' : 'asc'))}
                      title={guestSortDirection === 'desc' ? 'Descending' : 'Ascending'}
                      className="ml-1 rounded-full px-2 py-1 text-ink-secondary hover:bg-surface-card"
                    >
                      {guestSortDirection === 'desc' ? '▼' : '▲'}
                    </button>
                  </div>
                </div>
                <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 xl:grid-cols-3">
                  {visibleGuests.map((g) => (
                    <ResourceCard
                      key={g.name}
                      kind="guest"
                      title={g.guest_name}
                      subtitle={`${g.server} · VMID ${g.vmid} · ${g.guest_type}`}
                      status={g.status}
                      severity={guestSeverity(g)}
                      cpu={g.cpu_usage}
                      memory={g.memory_usage}
                      disks={[{ label: 'Disk', value: g.disk_usage }]}
                      meterOrder={GUEST_METER_ORDER[guestSortField]}
                      lastSynced={g.last_synced}
                      pollIntervalSeconds={pollIntervalSeconds}
                      warningThreshold={warningThreshold}
                      criticalThreshold={criticalThreshold}
                      tags={g.tags ? g.tags.split(',').map((t) => t.trim()).filter(Boolean) : undefined}
                      onClick={() => setSelected({ kind: 'guest', doc: g })}
                    />
                  ))}
                </div>
                {hasMoreGuests && (
                  <div ref={sentinelRef} className="flex justify-center py-10 text-sm text-ink-muted">
                    Loading more…
                  </div>
                )}
              </>
            )}
          </section>
        </>
      )}

      {mainTab === 'Backups' && <BackupsPanel backupLogs={activeBackupLogs} guests={allGuests} servers={allServers} />}
      {mainTab === 'Alerts' && <AlertsPanel alertLogs={allAlertLogs} />}
      {mainTab === 'AI Usage' && <AiUsagePanel />}
      {mainTab === 'Uptime' && <UptimePanel />}
      {mainTab === 'Host Health' && <HostHealthPanel />}

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

function filterAndSortServers(
  servers: ProxmoxServer[],
  serverFilter: string,
  criticalOnly: boolean,
  offlineOnly: boolean,
  isCritical: (s: ProxmoxServer) => boolean,
) {
  return servers
    .filter((s) => serverFilter === 'All' || s.server_name === serverFilter)
    .filter((s) => !criticalOnly || isCritical(s))
    .filter((s) => !offlineOnly || s.status === 'Offline')
    .sort((a, b) =>
      roleRank(a.role) - roleRank(b.role)
      || Number(isCritical(b)) - Number(isCritical(a))
      || a.server_name.localeCompare(b.server_name),
    )
}

function filterAndSortGuests(
  guests: ProxmoxGuest[],
  servers: ProxmoxServer[],
  serverFilter: string,
  criticalOnly: boolean,
  search: string,
) {
  const serverNameByDocName = new Map(servers.map((s) => [s.name, s.server_name]))
  const serverRoleByDocName = new Map(servers.map((s) => [s.name, s.role]))
  const needle = search.trim().toLowerCase()
  return guests
    .filter((g) => serverFilter === 'All' || serverNameByDocName.get(g.server) === serverFilter)
    .filter((g) => !criticalOnly || g.is_critical)
    .filter((g) => !needle || g.guest_name.toLowerCase().includes(needle) || String(g.vmid).includes(needle))
    .sort((a, b) =>
      roleRank(serverRoleByDocName.get(a.server)) - roleRank(serverRoleByDocName.get(b.server))
      || (b.is_critical - a.is_critical)
      || a.guest_name.localeCompare(b.guest_name),
    )
}

function SummaryStat({
  label,
  value,
  tone,
  active,
  onClick,
}: {
  label: string
  value: number
  tone: 'critical' | 'warning' | 'ok'
  active: boolean
  onClick: () => void
}) {
  const toneClass =
    tone === 'critical' ? 'text-status-critical' : tone === 'warning' ? 'text-status-warning' : 'text-ink-primary'
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      title={`Show ${label.toLowerCase()}`}
      className={`min-w-[7rem] rounded-2xl border bg-surface-card px-5 py-3.5 text-center shadow-sm transition-shadow hover:shadow-md ${
        active ? 'border-accent ring-2 ring-accent/40' : 'border-border-hairline'
      }`}
    >
      <div className={`text-2xl font-semibold ${toneClass}`}>{value}</div>
      <div className="text-xs font-medium uppercase tracking-wide text-ink-muted">{label}</div>
    </button>
  )
}

function EmptyState({ message }: { message: string }) {
  return (
    <div className="rounded-2xl border border-dashed border-border-hairline p-10 text-center text-sm text-ink-muted">
      {message}
    </div>
  )
}

export default App
