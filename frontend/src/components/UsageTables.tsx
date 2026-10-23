import { useMemo, useState, type ReactNode } from 'react'
import type {
  GuestLoadAverages,
  MonitoredHost,
  ProxmoxDatastore,
  ProxmoxGuest,
  ProxmoxServer,
} from '../lib/types'
import { timeAgo } from '../lib/format'
import { hostHealthStatus } from '../lib/severity'
import { MultiSelectFilter } from './MultiSelectFilter'
import { SearchInput } from './SearchInput'
import { EMPTY_GUEST_FILTERS, EMPTY_HOST_FILTERS, type GuestFilters, type HostFilters, type Severity } from '../lib/usageFilters'

const SEVERITY_LABEL: Record<Severity, string> = { critical: 'Critical', warning: 'Warning', ok: 'OK' }
const SEVERITY_OPTIONS = ['Critical', 'Warning', 'OK']
/** A resource-agent reading older than this is shown greyed, with its age. */
const LOAD_STALE_MS = 15 * 60 * 1000

interface Thresholds {
  warning: number
  critical: number
}

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------

function num(v: number): string {
  return v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1).replace(/\.0$/, '') : v.toFixed(1)
}

/** "3.9 / 8 GB", "1.7 / 1.8 TB" — one unit for the pair, picked by the total. */
function sizePair(used: number | null, totalGb?: number | null): string {
  if (!totalGb) return used === null ? '—' : `${num(used)} GB`
  const tb = totalGb >= 1000
  const scale = (v: number) => (tb ? v / 1000 : v)
  const unit = tb ? 'TB' : 'GB'
  return `${used === null ? '—' : num(scale(used))} / ${num(scale(totalGb))} ${unit}`
}

function pct(v?: number | null): string {
  return v === undefined || v === null ? '' : `${Math.round(v)}%`
}

function toneClass(value: number | null | undefined, t: Thresholds): string {
  if (value === undefined || value === null) return ''
  if (value >= t.critical) return 'text-status-critical'
  if (value >= t.warning) return 'text-status-warning'
  return ''
}

/** Sortable form of an IPv4 address ("10.10.10.9" sorts before "10.10.10.10"). */
function ipSortKey(ip?: string): string | null {
  if (!ip) return null
  const parts = ip.split('.')
  return parts.length === 4 && parts.every((p) => /^\d+$/.test(p)) ? parts.map((p) => p.padStart(3, '0')).join('.') : ip
}

function ts(value?: string | null): number | null {
  if (!value) return null
  const t = new Date(value.replace(' ', 'T')).getTime()
  return Number.isNaN(t) ? null : t
}

// ---------------------------------------------------------------------------
// Generic sortable table
// ---------------------------------------------------------------------------

type SortValue = number | string | null

interface Column<T> {
  key: string
  label: string
  render: (row: T) => ReactNode
  sortValue: (row: T) => SortValue
  align?: 'right'
}

interface SortState {
  key: string
  dir: 'asc' | 'desc'
}

function readSort(storageKey: string, fallback: SortState): SortState {
  try {
    const raw = localStorage.getItem(storageKey)
    if (raw) {
      const parsed = JSON.parse(raw) as SortState
      if (parsed && typeof parsed.key === 'string' && (parsed.dir === 'asc' || parsed.dir === 'desc')) return parsed
    }
  } catch {
    // storage unavailable — fall back to the default sort
  }
  return fallback
}

function SortableTable<T>({
  rows,
  columns,
  rowKey,
  rowSeverity,
  onRowClick,
  storageKey,
  defaultSort,
  emptyText,
}: {
  rows: T[]
  columns: Column<T>[]
  rowKey: (row: T) => string
  rowSeverity: (row: T) => Severity
  onRowClick: (row: T) => void
  storageKey: string
  defaultSort: SortState
  emptyText: string
}) {
  const [sort, setSort] = useState<SortState>(() => readSort(storageKey, defaultSort))

  const onSort = (key: string) => {
    const next: SortState = sort.key === key ? { key, dir: sort.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: 'desc' }
    setSort(next)
    try {
      localStorage.setItem(storageKey, JSON.stringify(next))
    } catch {
      // ignore — sort just won't persist
    }
  }

  const sorted = useMemo(() => {
    const col = columns.find((c) => c.key === sort.key) ?? columns[0]
    const sign = sort.dir === 'asc' ? 1 : -1
    return [...rows].sort((a, b) => {
      const av = col.sortValue(a)
      const bv = col.sortValue(b)
      // Missing values always last, whichever direction.
      if (av === null && bv === null) return 0
      if (av === null) return 1
      if (bv === null) return -1
      if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * sign
      return String(av).localeCompare(String(bv), undefined, { numeric: true }) * sign
    })
  }, [rows, columns, sort])

  return (
    <div className="overflow-x-auto rounded-2xl border border-border-hairline bg-surface-card">
      <table className="w-full min-w-[960px] text-left text-sm">
        <thead className="border-b border-border-hairline text-xs uppercase tracking-wide text-ink-muted">
          <tr>
            {columns.map((c) => (
              <th key={c.key} className={`px-4 py-3 font-medium ${c.align === 'right' ? 'text-right' : ''}`}>
                <button
                  type="button"
                  onClick={() => onSort(c.key)}
                  className={`inline-flex items-center gap-1 uppercase tracking-wide hover:text-ink-primary ${
                    sort.key === c.key ? 'text-ink-primary' : ''
                  }`}
                >
                  {c.label}
                  <span aria-hidden className="w-2 text-[10px]">
                    {sort.key === c.key ? (sort.dir === 'asc' ? '▲' : '▼') : ''}
                  </span>
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.length === 0 && (
            <tr>
              <td colSpan={columns.length} className="px-4 py-10 text-center text-sm text-ink-muted">
                {emptyText}
              </td>
            </tr>
          )}
          {sorted.map((row) => {
            const sev = rowSeverity(row)
            const edge =
              sev === 'critical'
                ? 'shadow-[inset_3px_0_0_var(--color-status-critical)]'
                : sev === 'warning'
                  ? 'shadow-[inset_3px_0_0_var(--color-status-warning)]'
                  : ''
            return (
              <tr
                key={rowKey(row)}
                onClick={() => onRowClick(row)}
                className="cursor-pointer border-b border-border-hairline align-top last:border-0 hover:bg-surface-page"
              >
                {columns.map((c, i) => (
                  <td
                    key={c.key}
                    className={`whitespace-nowrap px-4 py-2.5 ${c.align === 'right' ? 'text-right' : ''} ${i === 0 ? edge : ''}`}
                  >
                    {c.render(row)}
                  </td>
                ))}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function Sub({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`text-xs text-ink-muted ${className}`}>{children}</div>
}

function Badge({ tone, children }: { tone: 'good' | 'warning' | 'critical' | 'muted'; children: ReactNode }) {
  const cls = {
    good: 'bg-status-good/10 text-status-good',
    warning: 'bg-status-warning/15 text-status-warning',
    critical: 'bg-status-critical/10 text-status-critical',
    muted: 'bg-surface-page text-ink-muted',
  }[tone]
  return <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${cls}`}>{children}</span>
}

function FilterBar({
  children,
  shown,
  total,
  active,
  onClear,
}: {
  children: ReactNode
  shown: number
  total: number
  active: boolean
  onClear: () => void
}) {
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2">
      {children}
      <span className="ml-auto flex items-center gap-3 text-xs text-ink-muted">
        {active && (
          <button type="button" onClick={onClear} className="font-medium text-accent hover:underline">
            Clear filters
          </button>
        )}
        <span>
          Showing {shown} of {total}
        </span>
      </span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Hosts & backup server
// ---------------------------------------------------------------------------

function driveLabel(d: ProxmoxDatastore): string {
  if (d.storage_role === 'OS + Local Backup Drive') return 'OS+Backup'
  if (d.storage_role === 'Guest Storage (LVM-Thin)') return 'Guest LVM'
  return d.datastore_name
}

export function HostUsageTable({
  servers,
  datastores,
  severityOf,
  thresholds,
  loadThresholds,
  filters,
  onFiltersChange,
  onOpen,
}: {
  servers: ProxmoxServer[]
  datastores: ProxmoxDatastore[]
  severityOf: (s: ProxmoxServer) => Severity
  thresholds: Thresholds
  loadThresholds: Thresholds
  filters: HostFilters
  onFiltersChange: (f: HostFilters) => void
  onOpen: (s: ProxmoxServer) => void
}) {
  const drivesBy = useMemo(() => {
    const m = new Map<string, ProxmoxDatastore[]>()
    for (const d of datastores) m.set(d.server, [...(m.get(d.server) ?? []), d])
    return m
  }, [datastores])

  const roleOptions = useMemo(
    () => Array.from(new Set(servers.map((s) => s.role).filter((r): r is NonNullable<typeof r> => !!r))).sort(),
    [servers],
  )
  const statusOf = (s: ProxmoxServer) => (s.status === 'Offline' ? 'Offline' : 'Online')

  const rows = servers.filter(
    (s) =>
      (filters.role.length === 0 || (s.role !== undefined && filters.role.includes(s.role)))
      && (filters.status.length === 0 || filters.status.includes(statusOf(s)))
      && (filters.severity.length === 0 || filters.severity.includes(SEVERITY_LABEL[severityOf(s)])),
  )
  const active = filters.role.length + filters.status.length + filters.severity.length > 0

  const loadPct = (s: ProxmoxServer) =>
    s.load_avg_1min !== undefined && s.load_avg_1min !== null && s.cores ? (s.load_avg_1min / s.cores) * 100 : null

  const columns: Column<ProxmoxServer>[] = [
    {
      key: 'name',
      label: 'Host',
      sortValue: (s) => s.server_name.toLowerCase(),
      render: (s) => (
        <>
          <div className="font-medium text-ink-primary">{s.server_name}</div>
          <Sub>{[s.server_type === 'PBS' ? 'Backup server' : s.role, s.hostname].filter(Boolean).join(' · ')}</Sub>
        </>
      ),
    },
    {
      key: 'status',
      label: 'Status',
      sortValue: (s) => statusOf(s),
      render: (s) =>
        s.status === 'Offline' ? <Badge tone="warning">Offline</Badge> : <Badge tone="good">Online</Badge>,
    },
    {
      key: 'cpu',
      label: 'CPU',
      sortValue: (s) => s.cpu_usage ?? null,
      render: (s) => (
        <span className={toneClass(s.cpu_usage, thresholds)}>
          {s.cores && s.cpu_usage !== undefined ? `${num((s.cpu_usage / 100) * s.cores)} / ${s.cores} cores` : '—'}
          <Sub className={toneClass(s.cpu_usage, thresholds)}>{pct(s.cpu_usage)}</Sub>
        </span>
      ),
    },
    {
      key: 'ram',
      label: 'RAM',
      sortValue: (s) => s.memory_usage ?? null,
      render: (s) => (
        <span className={toneClass(s.memory_usage, thresholds)}>
          {sizePair(s.memory_usage !== undefined && s.memory_total ? (s.memory_usage / 100) * s.memory_total : null, s.memory_total)}
          <Sub className={toneClass(s.memory_usage, thresholds)}>{pct(s.memory_usage)}</Sub>
        </span>
      ),
    },
    {
      key: 'swap',
      label: 'Swap',
      sortValue: (s) => s.swap_usage ?? null,
      render: (s) =>
        s.swap_total ? (
          <>
            {sizePair(s.swap_usage !== undefined && s.swap_usage !== null ? (s.swap_usage / 100) * s.swap_total : null, s.swap_total)}
            <Sub>{pct(s.swap_usage)}</Sub>
          </>
        ) : (
          <span className="text-ink-muted">none</span>
        ),
    },
    {
      key: 'disks',
      label: 'Disks',
      sortValue: (s) => {
        const vals = (drivesBy.get(s.name) ?? []).map((d) => d.usage_percent).filter((v): v is number => v !== undefined && v !== null)
        return vals.length ? Math.max(...vals) : null
      },
      render: (s) => {
        const drives = drivesBy.get(s.name) ?? []
        if (!drives.length) return <span className="text-ink-muted">—</span>
        return (
          <div className="flex flex-col gap-0.5">
            {drives.map((d) => (
              <div key={d.name} className={toneClass(d.usage_percent, thresholds)}>
                <span className="text-ink-secondary">{driveLabel(d)}</span> {sizePair(d.used ?? null, d.total)}
                {d.usage_percent !== undefined && <span className="text-xs"> · {pct(d.usage_percent)}</span>}
              </div>
            ))}
          </div>
        )
      },
    },
    {
      key: 'load',
      label: 'Load avg',
      sortValue: (s) => loadPct(s),
      render: (s) =>
        s.load_avg_1min === undefined || s.load_avg_1min === null ? (
          <span className="text-ink-muted">—</span>
        ) : (
          <span className={toneClass(loadPct(s), loadThresholds)}>
            {[s.load_avg_1min, s.load_avg_5min, s.load_avg_15min].map((v) => (v ?? 0).toFixed(2)).join(' · ')}
            {s.cores ? <Sub>{pct(loadPct(s))} of {s.cores} cores</Sub> : null}
          </span>
        ),
    },
    {
      key: 'synced',
      label: 'Last synced',
      sortValue: (s) => ts(s.last_synced),
      render: (s) => <span className="text-ink-secondary">{timeAgo(s.last_synced)}</span>,
    },
  ]

  return (
    <>
      <FilterBar
        shown={rows.length}
        total={servers.length}
        active={active}
        onClear={() => onFiltersChange(EMPTY_HOST_FILTERS)}
      >
        <MultiSelectFilter label="Role" options={roleOptions} selected={filters.role} onChange={(role) => onFiltersChange({ ...filters, role })} />
        <MultiSelectFilter label="Status" options={['Online', 'Offline']} selected={filters.status} onChange={(status) => onFiltersChange({ ...filters, status })} />
        <MultiSelectFilter label="Severity" options={SEVERITY_OPTIONS} selected={filters.severity} onChange={(severity) => onFiltersChange({ ...filters, severity })} />
      </FilterBar>
      <SortableTable
        rows={rows}
        columns={columns}
        rowKey={(s) => s.name}
        rowSeverity={severityOf}
        onRowClick={onOpen}
        storageKey="pulse.usage.hosts.sort"
        defaultSort={{ key: 'name', dir: 'asc' }}
        emptyText={servers.length ? 'No hosts match these filters.' : 'No Proxmox Server connected yet — add one to get started.'}
      />
    </>
  )
}

// ---------------------------------------------------------------------------
// VMs & containers
// ---------------------------------------------------------------------------

type HealthLabel = 'Online' | 'Issue' | 'No agent'

export function GuestUsageTable({
  guests,
  servers,
  monitoredHosts,
  loads,
  siteIndex,
  severityOf,
  thresholds,
  backupOverdueHours,
  filters,
  onFiltersChange,
  onOpen,
}: {
  guests: ProxmoxGuest[]
  servers: ProxmoxServer[]
  monitoredHosts: MonitoredHost[]
  loads?: GuestLoadAverages
  /** guest → site domains (site discovery); counted here, listed in the popup. */
  siteIndex?: Record<string, string[]>
  severityOf: (g: ProxmoxGuest) => Severity
  thresholds: Thresholds
  /** Proxmox Monitor Settings' backup_overdue_hours — same window the
   * Backup Overdue alert uses, so the colour and the alert agree. */
  backupOverdueHours: number
  filters: GuestFilters
  onFiltersChange: (f: GuestFilters) => void
  onOpen: (g: ProxmoxGuest) => void
}) {
  const serverName = useMemo(() => new Map(servers.map((s) => [s.name, s.server_name])), [servers])
  const healthByGuest = useMemo(() => new Map(monitoredHosts.map((h) => [h.proxmox_guest, h])), [monitoredHosts])
  const loadThresholds: Thresholds = {
    warning: loads?.warning_percent ?? 100,
    critical: loads?.critical_percent ?? 200,
  }

  const healthOf = (g: ProxmoxGuest): HealthLabel => {
    const h = healthByGuest.get(g.name)
    if (!h) return 'No agent'
    return hostHealthStatus(h) === 'Online' ? 'Online' : 'Issue'
  }
  const typeOf = (g: ProxmoxGuest) => (g.guest_type === 'QEMU (VM)' ? 'VM' : 'CT')
  const loadOf = (g: ProxmoxGuest) => loads?.guests[g.name]

  const hostOptions = useMemo(
    () => Array.from(new Set(guests.map((g) => serverName.get(g.server) ?? g.server))).sort(),
    [guests, serverName],
  )
  const statusOptions = useMemo(() => Array.from(new Set(guests.map((g) => g.status))).sort(), [guests])

  const needle = filters.search.trim().toLowerCase()
  const rows = guests.filter((g) => {
    if (needle) {
      const hay = [g.guest_name, String(g.vmid), g.ip_address ?? '', g.public_ip ?? '', g.tags ?? '', ...(siteIndex?.[g.name] ?? [])]
        .join(' ')
        .toLowerCase()
      if (!hay.includes(needle)) return false
    }
    return (
      (filters.host.length === 0 || filters.host.includes(serverName.get(g.server) ?? g.server))
      && (filters.status.length === 0 || filters.status.includes(g.status))
      && (filters.type.length === 0 || filters.type.includes(typeOf(g)))
      && (filters.hostHealth.length === 0 || filters.hostHealth.includes(healthOf(g)))
      && (filters.severity.length === 0 || filters.severity.includes(SEVERITY_LABEL[severityOf(g)]))
    )
  })
  const active =
    !!needle
    || filters.host.length + filters.status.length + filters.type.length + filters.hostHealth.length + filters.severity.length > 0

  const running = (g: ProxmoxGuest) => g.status === 'running'

  const columns: Column<ProxmoxGuest>[] = [
    {
      key: 'name',
      label: 'Instance',
      sortValue: (g) => g.guest_name.toLowerCase(),
      render: (g) => {
        const tags = g.tags ? g.tags.split(',').map((t) => t.trim()).filter(Boolean) : []
        return (
          <>
            <div className="font-medium text-ink-primary">{g.guest_name}</div>
            <Sub>
              {typeOf(g)} {g.vmid}
              {tags.length > 0 && <span className="ml-1.5">{tags.map((t) => `#${t}`).join(' ')}</span>}
            </Sub>
          </>
        )
      },
    },
    {
      key: 'host',
      label: 'Host',
      sortValue: (g) => (serverName.get(g.server) ?? g.server).toLowerCase(),
      render: (g) => <span className="text-ink-secondary">{serverName.get(g.server) ?? g.server}</span>,
    },
    {
      key: 'status',
      label: 'Status',
      sortValue: (g) => g.status,
      render: (g) => <span className={running(g) ? 'text-ink-primary' : 'text-ink-muted'}>{g.status}</span>,
    },
    {
      key: 'cpu',
      label: 'CPU',
      sortValue: (g) => (running(g) ? g.cpu_usage ?? null : null),
      render: (g) => {
        const on = running(g) && g.cpu_usage !== undefined
        return (
          <span className={on ? toneClass(g.cpu_usage, thresholds) : 'text-ink-muted'}>
            {on && g.cpus ? num((g.cpu_usage! / 100) * g.cpus) : '—'} / {g.cpus ?? '—'} vCPU
            {on && <Sub className={toneClass(g.cpu_usage, thresholds)}>{pct(g.cpu_usage)}</Sub>}
          </span>
        )
      },
    },
    {
      key: 'ram',
      label: 'RAM',
      sortValue: (g) => (running(g) ? g.memory_usage ?? null : null),
      render: (g) => {
        const on = running(g) && g.memory_usage !== undefined
        return (
          <span className={on ? toneClass(g.memory_usage, thresholds) : 'text-ink-muted'}>
            {sizePair(on && g.memory_total ? (g.memory_usage! / 100) * g.memory_total : null, g.memory_total)}
            {on && <Sub className={toneClass(g.memory_usage, thresholds)}>{pct(g.memory_usage)}</Sub>}
          </span>
        )
      },
    },
    {
      key: 'disk',
      label: 'Disk',
      sortValue: (g) => g.disk_usage ?? null,
      render: (g) => {
        const known = g.disk_usage !== undefined && g.disk_usage !== null
        return (
          <span className={known ? toneClass(g.disk_usage, thresholds) : ''}>
            {sizePair(known && g.disk_total ? (g.disk_usage! / 100) * g.disk_total : null, g.disk_total)}
            {known ? (
              <Sub className={toneClass(g.disk_usage, thresholds)}>{pct(g.disk_usage)}</Sub>
            ) : g.guest_type === 'QEMU (VM)' && running(g) ? (
              <Sub>no guest agent</Sub>
            ) : null}
          </span>
        )
      },
    },
    {
      key: 'load',
      label: 'Load avg',
      sortValue: (g) => loadOf(g)?.load_normalized_percent ?? null,
      render: (g) => {
        const l = loadOf(g)
        if (!l) return <span className="text-ink-muted">—</span>
        const collected = ts(l.collected_at)
        const stale = collected !== null && Date.now() - collected > LOAD_STALE_MS
        return (
          <span className={stale ? 'text-ink-muted' : toneClass(l.load_normalized_percent, loadThresholds)}>
            {[l.load_avg_1min, l.load_avg_5min, l.load_avg_15min].map((v) => v.toFixed(2)).join(' · ')}
            <Sub>
              {stale
                ? `as of ${timeAgo(l.collected_at)}`
                : `${pct(l.load_normalized_percent)} of ${l.cpu_core_count} core${l.cpu_core_count === 1 ? '' : 's'}`}
            </Sub>
          </span>
        )
      },
    },
    {
      key: 'ip',
      label: 'IP',
      sortValue: (g) => ipSortKey(g.ip_address),
      render: (g) => (
        <>
          <span className="font-mono text-xs text-ink-secondary">{g.ip_address || '—'}</span>
          {g.public_ip && <Sub className="font-mono">{g.public_ip}</Sub>}
        </>
      ),
    },
    {
      key: 'sites',
      label: 'Sites',
      align: 'right',
      sortValue: (g) => siteIndex?.[g.name]?.length ?? null,
      render: (g) => {
        const sites = siteIndex?.[g.name] ?? []
        return sites.length ? (
          <span className="tabular-nums text-ink-primary">{sites.length}</span>
        ) : (
          <span className="text-ink-muted">—</span>
        )
      },
    },
    {
      key: 'backup',
      label: 'Last backup',
      sortValue: (g) => ts(g.last_successful_backup),
      render: (g) => {
        const t = ts(g.last_successful_backup)
        const ageH = t === null ? null : (Date.now() - t) / 3_600_000
        const overdue = backupOverdueHours > 0 && ageH !== null && ageH > backupOverdueHours
        const cls = ageH === null ? 'text-status-critical' : overdue ? 'text-status-warning' : 'text-ink-secondary'
        return <span className={cls} title={g.last_successful_backup ?? 'No successful backup recorded'}>{timeAgo(g.last_successful_backup)}</span>
      },
    },
    {
      key: 'health',
      label: 'Host Health',
      sortValue: (g) => ({ Issue: 0, Online: 1, 'No agent': 2 })[healthOf(g)],
      render: (g) => {
        const h = healthOf(g)
        return h === 'No agent' ? <span className="text-ink-muted">—</span> : <Badge tone={h === 'Online' ? 'good' : 'critical'}>{h}</Badge>
      },
    },
  ]

  return (
    <>
      <FilterBar
        shown={rows.length}
        total={guests.length}
        active={active}
        onClear={() => onFiltersChange(EMPTY_GUEST_FILTERS)}
      >
        <SearchInput
          value={filters.search}
          onChange={(search) => onFiltersChange({ ...filters, search })}
          placeholder="Search name, VMID, IP, tag, site URL…"
        />
        <MultiSelectFilter label="Host" options={hostOptions} selected={filters.host} onChange={(host) => onFiltersChange({ ...filters, host })} />
        <MultiSelectFilter label="Status" options={statusOptions} selected={filters.status} onChange={(status) => onFiltersChange({ ...filters, status })} />
        <MultiSelectFilter label="Type" options={['VM', 'CT']} selected={filters.type} onChange={(type) => onFiltersChange({ ...filters, type })} />
        <MultiSelectFilter
          label="Host Health"
          options={['Online', 'Issue', 'No agent']}
          selected={filters.hostHealth}
          onChange={(hostHealth) => onFiltersChange({ ...filters, hostHealth })}
        />
        <MultiSelectFilter label="Severity" options={SEVERITY_OPTIONS} selected={filters.severity} onChange={(severity) => onFiltersChange({ ...filters, severity })} />
      </FilterBar>
      <SortableTable
        rows={rows}
        columns={columns}
        rowKey={(g) => g.name}
        rowSeverity={severityOf}
        onRowClick={onOpen}
        storageKey="pulse.usage.guests.sort"
        defaultSort={{ key: 'cpu', dir: 'desc' }}
        emptyText={
          guests.length
            ? 'No VMs or CTs match these filters.'
            : "No VMs or CTs discovered yet — they'll appear automatically once a server is connected."
        }
      />
    </>
  )
}
