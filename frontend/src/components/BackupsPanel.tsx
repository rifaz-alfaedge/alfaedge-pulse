import { Fragment, useMemo, useState } from 'react'
import type { ProxmoxBackupLog, ProxmoxGuest, ProxmoxServer } from '../lib/types'
import { formatGB, timeAgo } from '../lib/format'
import { StatusBadge } from './StatusBadge'
import { Tabs } from './Tabs'

const STATUS_FILTERS = ['All', 'Failed', 'Success', 'Running']

const ROW_ACCENT: Record<string, string> = {
  Failed: 'border-l-status-critical',
  Running: 'border-l-status-warning',
  Success: 'border-l-status-good',
}

type SortField = 'vmid' | 'size' | 'backup_time'
type SortDirection = 'asc' | 'desc'

interface BackupGroup {
  key: string
  vmid: number
  server: string
  guestName: string
  entries: ProxmoxBackupLog[]
  latest: ProxmoxBackupLog
  hasFailed: boolean
}

/** Dedicated Backups tab. Every recorded run for a guest used to be its own
 * flat row, which made the table grow without bound (a guest with 6
 * backups on file took 6 rows) — this groups by VMID instead, one row per
 * guest showing its latest result, expandable to the full history. Sortable
 * by VMID/Size/Last Backup; a failed backup anywhere in a guest's history
 * keeps the whole group's left-edge accent red even if its *latest* run
 * succeeded, so a past failure is never one click away from invisible.
 *
 * Split into one table per *backup destination* rather than one combined
 * table with a Server column: a "PBS Remote" backup is a vzdump job that
 * runs on a PVE host but actually lands on the PBS instance, so grouping
 * it by the triggering PVE host (the only `server` a backup log row ever
 * carries) put it in the wrong bucket entirely — Alpha's PBS-targeted
 * backups showed up under "ALPHA" instead of the PBS server. Local Disk
 * entries still group by their PVE host; PBS Remote entries group by
 * whichever server is actually of type PBS. Different servers/PBS
 * instances can (and do) reuse the same VMID for unrelated guests, so this
 * also keeps those from being visually conflated. The VMID
 * grouping/sorting/expand behaviour inside each table is unchanged. */
export function BackupsPanel({
  backupLogs,
  guests,
  servers,
}: {
  backupLogs: ProxmoxBackupLog[]
  guests: ProxmoxGuest[]
  servers: ProxmoxServer[]
}) {
  const [statusFilter, setStatusFilter] = useState<string>('All')
  const [sortField, setSortField] = useState<SortField>('backup_time')
  const [sortDirection, setSortDirection] = useState<SortDirection>('desc')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  // Per-server tables start collapsed — with 4+ servers each carrying their
  // own long history, showing every table open by default was more scrolling
  // than signal. Tracked as a "which ones are open" set rather than "closed"
  // so newly-added servers default to collapsed too, with no extra wiring.
  const [openServers, setOpenServers] = useState<Set<string>>(new Set())

  const guestNameByDocName = useMemo(() => new Map(guests.map((g) => [g.name, g.guest_name])), [guests])
  // Only used as a fallback destination for "PBS Remote" entries — there's
  // normally exactly one PBS instance in a fleet like this. If none is
  // connected yet, those entries just fall back to their triggering PVE host.
  const pbsServerName = useMemo(() => servers.find((s) => s.server_type === 'PBS')?.name, [servers])

  const groupsByServer = useMemo(() => {
    const tableFor = (b: ProxmoxBackupLog) => (b.backup_source === 'PBS Remote' && pbsServerName) || b.server

    const byKey = new Map<string, ProxmoxBackupLog[]>()
    for (const b of backupLogs) {
      const key = `${tableFor(b)}::${b.server}::${b.vmid ?? 'unknown'}`
      const bucket = byKey.get(key)
      if (bucket) bucket.push(b)
      else byKey.set(key, [b])
    }

    const bySeverName = new Map<string, BackupGroup[]>()
    for (const entries of byKey.values()) {
      const sorted = [...entries].sort((a, b) => (b.backup_time ?? '').localeCompare(a.backup_time ?? ''))
      const latest = sorted[0]
      const table = tableFor(latest)
      const group: BackupGroup = {
        key: `${table}::${latest.server}::${latest.vmid ?? 'unknown'}`,
        vmid: latest.vmid ?? 0,
        server: latest.server,
        guestName: (latest.guest && guestNameByDocName.get(latest.guest)) || `VMID ${latest.vmid ?? '—'}`,
        entries: sorted,
        latest,
        hasFailed: entries.some((e) => e.status === 'Failed'),
      }
      const bucket = bySeverName.get(table)
      if (bucket) bucket.push(group)
      else bySeverName.set(table, [group])
    }

    return [...bySeverName.entries()].sort((a, b) => a[0].localeCompare(b[0]))
  }, [backupLogs, guestNameByDocName])

  const sortAndFilter = (groups: BackupGroup[]) => {
    const filtered =
      statusFilter === 'All' ? groups : groups.filter((g) => g.entries.some((e) => e.status === statusFilter))
    const dir = sortDirection === 'asc' ? 1 : -1
    return [...filtered].sort((a, b) => {
      switch (sortField) {
        case 'vmid':
          return dir * (a.vmid - b.vmid)
        case 'size':
          return dir * ((a.latest.size ?? 0) - (b.latest.size ?? 0))
        case 'backup_time':
        default:
          return dir * (a.latest.backup_time ?? '').localeCompare(b.latest.backup_time ?? '')
      }
    })
  }

  const toggleSort = (field: SortField) => {
    if (field === sortField) {
      setSortDirection((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortField(field)
      setSortDirection('desc')
    }
  }

  const toggleExpanded = (key: string) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const toggleServer = (serverName: string) => {
    setOpenServers((prev) => {
      const next = new Set(prev)
      if (next.has(serverName)) next.delete(serverName)
      else next.add(serverName)
      return next
    })
  }

  return (
    <div>
      <div className="mb-6">
        <Tabs options={STATUS_FILTERS} value={statusFilter} onChange={setStatusFilter} />
      </div>

      {groupsByServer.length === 0 ? (
        <p className="text-sm text-ink-muted">No backups recorded yet.</p>
      ) : (
        <div className="space-y-8">
          {groupsByServer.map(([serverName, groups]) => (
            <ServerBackupTable
              key={serverName}
              serverName={serverName}
              groups={sortAndFilter(groups)}
              sortField={sortField}
              sortDirection={sortDirection}
              onSort={toggleSort}
              expanded={expanded}
              onToggleExpand={toggleExpanded}
              isOpen={openServers.has(serverName)}
              onToggleOpen={() => toggleServer(serverName)}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function ServerBackupTable({
  serverName,
  groups,
  sortField,
  sortDirection,
  onSort,
  expanded,
  onToggleExpand,
  isOpen,
  onToggleOpen,
}: {
  serverName: string
  groups: BackupGroup[]
  sortField: SortField
  sortDirection: SortDirection
  onSort: (field: SortField) => void
  expanded: Set<string>
  onToggleExpand: (key: string) => void
  isOpen: boolean
  onToggleOpen: () => void
}) {
  const hasFailed = groups.some((g) => g.hasFailed)

  return (
    <div>
      <button
        type="button"
        onClick={onToggleOpen}
        className="mb-3 flex w-full items-center gap-2 text-left text-sm font-semibold uppercase tracking-wide text-ink-secondary hover:text-ink-primary"
      >
        <span className="text-ink-muted">{isOpen ? '▾' : '▸'}</span>
        <span>{serverName}</span>
        <span className="font-normal normal-case text-ink-muted">({groups.length})</span>
        {/* A failure inside a collapsed table would otherwise be invisible
         * until someone thinks to expand it — this dot means a red flag is
         * never more than one glance away, even collapsed. */}
        {hasFailed && <span className="h-1.5 w-1.5 rounded-full bg-status-critical" aria-label="has failed backups" />}
      </button>
      {!isOpen ? null : groups.length === 0 ? (
        <p className="text-sm text-ink-muted">No backups match this filter.</p>
      ) : (
        <div className="overflow-x-auto rounded-2xl border border-border-hairline bg-surface-card">
          <table className="w-full min-w-[760px] text-sm">
            <thead className="text-left text-xs uppercase tracking-widest text-ink-muted">
              <tr>
                <th className="w-8 px-3 py-3" />
                <SortableHeader label="VMID" field="vmid" sortField={sortField} sortDirection={sortDirection} onSort={onSort} />
                <th className="px-3 py-3">Guest</th>
                <th className="px-3 py-3">Source</th>
                <th className="px-3 py-3">Status</th>
                <SortableHeader label="Size" field="size" sortField={sortField} sortDirection={sortDirection} onSort={onSort} />
                <SortableHeader label="Last Backup" field="backup_time" sortField={sortField} sortDirection={sortDirection} onSort={onSort} />
                <th className="px-3 py-3">Notes</th>
                <th className="px-3 py-3">Backups</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-hairline">
              {groups.map((group) => {
                const isRowOpen = expanded.has(group.key)
                return (
                  <Fragment key={group.key}>
                    <tr
                      onClick={() => onToggleExpand(group.key)}
                      className={`cursor-pointer border-l-4 hover:bg-ink-primary/5 ${
                        group.hasFailed ? ROW_ACCENT.Failed : ROW_ACCENT[group.latest.status] ?? 'border-l-transparent'
                      }`}
                    >
                      <td className="px-3 py-3 text-ink-muted">{isRowOpen ? '▾' : '▸'}</td>
                      <td className="px-3 py-3 font-mono text-ink-primary">{group.vmid || '—'}</td>
                      <td className="px-3 py-3 text-ink-primary">
                        {group.guestName}
                        {/* The PBS table can mix guests from several different PVE hosts
                         * under the same VMID — call out the origin host there so two
                         * unrelated guests that happen to share a VMID aren't confused
                         * for one instance. Redundant (and hidden) on a PVE host's own
                         * table, since every row there already shares that origin. */}
                        {group.server !== serverName && (
                          <span className="ml-1.5 text-xs text-ink-muted">via {group.server}</span>
                        )}
                      </td>
                      <td className="px-3 py-3 text-ink-primary">{group.latest.backup_source}</td>
                      <td className="px-3 py-3"><StatusBadge status={group.latest.status} /></td>
                      <td className="px-3 py-3 text-ink-primary">{formatGB(group.latest.size)}</td>
                      <td className="px-3 py-3 text-ink-secondary">{timeAgo(group.latest.backup_time)}</td>
                      <td className="px-3 py-3 text-ink-secondary">{group.latest.notes || '—'}</td>
                      <td className="px-3 py-3 text-ink-secondary">{group.entries.length}</td>
                    </tr>
                    {isRowOpen &&
                      group.entries.map((entry) => (
                        <tr key={entry.name} className="bg-ink-primary/[0.03] text-xs">
                          <td className="px-3 py-2" />
                          <td className="px-3 py-2" />
                          <td className="px-3 py-2" />
                          <td className="px-3 py-2 text-ink-secondary">{entry.backup_source}</td>
                          <td className="px-3 py-2"><StatusBadge status={entry.status} /></td>
                          <td className="px-3 py-2 text-ink-secondary">{formatGB(entry.size)}</td>
                          <td className="px-3 py-2 text-ink-secondary">{timeAgo(entry.backup_time)}</td>
                          <td className="px-3 py-2 text-ink-secondary">{entry.notes || '—'}</td>
                          <td className="px-3 py-2 max-w-[16rem] truncate text-status-critical">{entry.error_message}</td>
                        </tr>
                      ))}
                  </Fragment>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
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
      className={`cursor-pointer select-none px-3 py-3 hover:text-ink-primary ${active ? 'text-ink-primary' : ''}`}
    >
      <span className="inline-flex items-center gap-1">
        {label}
        <span className={active ? 'opacity-100' : 'opacity-30'}>{active && sortDirection === 'asc' ? '▲' : '▼'}</span>
      </span>
    </th>
  )
}
