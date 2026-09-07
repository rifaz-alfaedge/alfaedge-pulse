import { type ReactNode, useState } from 'react'
import { Dialog } from '@rtcamp/frappe-ui-react'
import { useGuestMetricHistory, useHostMetricHistory } from '../lib/hooks'
import type { ProxmoxAlertLog, ProxmoxBackupLog, ProxmoxDatastore, ProxmoxGuest, ProxmoxServer } from '../lib/types'
import { formatGB, formatPercent, formatUptime, timeAgo } from '../lib/format'
import { StatusBadge } from './StatusBadge'
import { TrendChart } from './TrendChart'
import { UsageBar } from './UsageBar'

/** Resource History trend-chart window presets, in minutes — same shape as
 * Host Health's own TREND_RANGE_OPTIONS (HostHealthPanel.tsx), duplicated
 * rather than shared since the two panels' natural windows differ slightly
 * (this data is sampled once a minute by default, not pushed every ~20-30s
 * by an agent, so there's no reason to keep them coupled). */
const RESOURCE_HISTORY_RANGE_OPTIONS: { label: string; minutes: number }[] = [
  { label: '1h', minutes: 60 },
  { label: '6h', minutes: 360 },
  { label: '24h', minutes: 1440 },
  { label: '7d', minutes: 10080 },
]

const formatChartPercent = (v: number) => `${Math.round(v)}%`

export type SelectedNode =
  | { kind: 'host' | 'pbs'; doc: ProxmoxServer }
  | { kind: 'guest'; doc: ProxmoxGuest }

/** Full drill-down for a single node — everything we know about it, plus its
 * own backup and alert history filtered from the already-loaded lists (no
 * extra network round-trip). Covers the "click any card for in-depth
 * details" requirement for hosts, PBS, VMs, and CTs alike. */
export function NodeDetailDialog({
  selected,
  onClose,
  onOpenConsole,
  datastores,
  backupLogs,
  alertLogs,
}: {
  selected: SelectedNode | null
  onClose: () => void
  // Handed up to whoever renders this dialog (App.tsx), which deep-links
  // to Proxmox's own console UI in a new tab — see lib/consoleUrl.ts.
  onOpenConsole: (guest: ProxmoxGuest) => void
  datastores: ProxmoxDatastore[]
  backupLogs: ProxmoxBackupLog[]
  alertLogs: ProxmoxAlertLog[]
}) {
  if (!selected) return null

  const isServer = selected.kind !== 'guest'
  const title = isServer ? (selected.doc as ProxmoxServer).server_name : (selected.doc as ProxmoxGuest).guest_name

  const relatedBackups = isServer
    ? backupLogs.filter((b) => b.server === selected.doc.name)
    : backupLogs.filter((b) => b.guest === selected.doc.name)

  const relatedAlerts = alertLogs.filter((a) => a.reference_name === selected.doc.name)

  return (
    <Dialog
      open={!!selected}
      onOpenChange={(open) => !open && onClose()}
      options={{ title, size: '2xl' }}
    >
      <div className="space-y-8 py-2">
        {isServer ? (
          <HostDetail server={selected.doc as ProxmoxServer} datastores={datastores.filter((d) => d.server === selected.doc.name)} />
        ) : (
          <GuestDetail guest={selected.doc as ProxmoxGuest} />
        )}

        {isServer ? (
          <HostResourceHistory serverName={selected.doc.name} />
        ) : (
          <GuestResourceHistory guestName={selected.doc.name} guestType={(selected.doc as ProxmoxGuest).guest_type} />
        )}

        <section>
          <h4 className="mb-2.5 text-xs font-semibold uppercase tracking-widest text-ink-muted">
            Backup History
          </h4>
          {relatedBackups.length === 0 ? (
            <p className="text-sm text-ink-muted">No backups recorded yet.</p>
          ) : (
            <div className="max-h-52 space-y-2 overflow-y-auto pr-1">
              {relatedBackups.slice(0, 15).map((b) => (
                <div key={b.name} className="flex items-center justify-between text-sm">
                  <span className="text-ink-secondary">
                    {b.backup_source} · VMID {b.vmid} · {timeAgo(b.backup_time)}
                  </span>
                  <StatusBadge status={b.status} />
                </div>
              ))}
            </div>
          )}
        </section>

        <section>
          <h4 className="mb-2.5 text-xs font-semibold uppercase tracking-widest text-ink-muted">
            Alert History
          </h4>
          {relatedAlerts.length === 0 ? (
            <p className="text-sm text-ink-muted">No alerts raised.</p>
          ) : (
            <div className="max-h-52 space-y-2 overflow-y-auto pr-1">
              {relatedAlerts.slice(0, 15).map((a) => (
                <div key={a.name} className="flex items-center justify-between gap-3 text-sm">
                  <span className="truncate text-ink-secondary">{a.message}</span>
                  <span className={a.resolved ? 'text-ink-muted' : 'font-medium text-status-critical'}>
                    {a.resolved ? 'resolved' : 'open'}
                  </span>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* Console is guest-only, by design — a Proxmox host/PBS server
         * (isServer) never renders this trigger; onOpenConsole only ever
         * receives a real Proxmox Guest. */}
        {!isServer && (
          <div className="flex justify-end border-t border-border-hairline pt-4">
            <button
              type="button"
              onClick={() => onOpenConsole(selected.doc as ProxmoxGuest)}
              className="rounded-lg border border-border-hairline px-2.5 py-1 text-xs text-ink-secondary hover:bg-ink-primary/5"
            >
              Open Console ↗
            </button>
          </div>
        )}
      </div>
    </Dialog>
  )
}

/** Range-preset strip + trend chart, shared shell for both the host and
 * guest variants below — only the data hook and series list differ. */
function ResourceHistoryChart({
  x,
  series,
  emptyLabel,
}: {
  x: number[]
  series: { label: string; data: (number | null)[]; colorVar: string }[]
  emptyLabel: string
}) {
  return (
    <div className="rounded-xl border border-gridline p-3">
      {x.length === 0 ? (
        <p className="py-6 text-center text-sm text-ink-muted">{emptyLabel}</p>
      ) : (
        <TrendChart x={x} series={series} valueFormatter={formatChartPercent} />
      )}
    </div>
  )
}

function RangePicker({ rangeMinutes, onChange }: { rangeMinutes: number; onChange: (minutes: number) => void }) {
  return (
    <div className="flex items-center rounded-lg border border-border-hairline p-0.5 text-xs">
      {RESOURCE_HISTORY_RANGE_OPTIONS.map(({ label, minutes }) => (
        <button
          key={minutes}
          type="button"
          onClick={() => onChange(minutes)}
          className={`rounded-md px-2 py-1 transition-colors ${
            rangeMinutes === minutes ? 'bg-accent text-white' : 'text-ink-secondary hover:bg-ink-primary/5'
          }`}
        >
          {label}
        </button>
      ))}
    </div>
  )
}

function HostResourceHistory({ serverName }: { serverName: string }) {
  const [rangeMinutes, setRangeMinutes] = useState(360)
  const { data } = useHostMetricHistory(serverName, rangeMinutes)
  const history = data ?? []
  const x = history.map((h) => Math.floor(new Date(h.collected_at.replace(' ', 'T')).getTime() / 1000))

  return (
    <section>
      <div className="mb-2.5 flex flex-wrap items-center justify-between gap-3">
        <h4 className="text-xs font-semibold uppercase tracking-widest text-ink-muted">Resource History</h4>
        <RangePicker rangeMinutes={rangeMinutes} onChange={setRangeMinutes} />
      </div>
      <ResourceHistoryChart
        x={x}
        emptyLabel="No history yet for this window."
        series={[
          { label: 'CPU', data: history.map((h) => h.cpu_usage), colorVar: '--color-accent' },
          { label: 'Memory', data: history.map((h) => h.memory_usage), colorVar: '--color-status-warning' },
          { label: 'Swap', data: history.map((h) => h.swap_usage ?? null), colorVar: '--color-status-critical' },
        ]}
      />
    </section>
  )
}

function GuestResourceHistory({ guestName, guestType }: { guestName: string; guestType: ProxmoxGuest['guest_type'] }) {
  const [rangeMinutes, setRangeMinutes] = useState(360)
  const { data } = useGuestMetricHistory(guestName, rangeMinutes)
  const history = data ?? []
  const x = history.map((h) => Math.floor(new Date(h.collected_at.replace(' ', 'T')).getTime() / 1000))
  // Swap is only ever meaningful for LXC containers with a per-container
  // swap cap configured — always null for QEMU, and often null for LXC
  // too if no cap is set (see ProxmoxGuestMetricLog's own field
  // description). Only add the series at all for LXC, so a QEMU guest's
  // chart isn't cluttered with a line that can never be anything but "—".
  const series = [
    { label: 'CPU', data: history.map((h) => h.cpu_usage), colorVar: '--color-accent' },
    { label: 'Memory', data: history.map((h) => h.memory_usage), colorVar: '--color-status-warning' },
    { label: 'Disk', data: history.map((h) => h.disk_usage ?? null), colorVar: '--color-status-serious' },
    ...(guestType === 'LXC (CT)'
      ? [{ label: 'Swap', data: history.map((h) => h.swap_usage_percent ?? null), colorVar: '--color-status-critical' }]
      : []),
  ]

  return (
    <section>
      <div className="mb-2.5 flex flex-wrap items-center justify-between gap-3">
        <h4 className="text-xs font-semibold uppercase tracking-widest text-ink-muted">Resource History</h4>
        <RangePicker rangeMinutes={rangeMinutes} onChange={setRangeMinutes} />
      </div>
      <ResourceHistoryChart x={x} series={series} emptyLabel="No history yet for this window." />
    </section>
  )
}

function HostDetail({ server, datastores }: { server: ProxmoxServer; datastores: ProxmoxDatastore[] }) {
  return (
    <div className="grid grid-cols-2 gap-x-10 gap-y-5 text-base">
      <Field label="Status"><StatusBadge status={server.status} /></Field>
      <Field label="Role">{server.role || '—'}</Field>
      <Field label="Hostname">{server.hostname}</Field>
      <Field label="Datacenter">{server.datacenter_location ? `${server.datacenter_location} (${server.cloud_provider})` : '—'}</Field>
      <Field label="CPU">{formatPercent(server.cpu_usage)}</Field>
      <Field label="Memory">{formatPercent(server.memory_usage)} of {formatGB(server.memory_total)}</Field>
      <Field label="Root Filesystem (/)">{formatPercent(server.storage_usage)} of {formatGB(server.storage_total)}</Field>
      <Field label="Uptime">{formatUptime(server.uptime)}</Field>
      <Field label="Backup Task Status">
        <span className={server.backup_critical ? 'font-semibold text-status-critical' : undefined}>
          {server.backup_critical ? 'Last task failed' : 'OK'}
        </span>
      </Field>
      <Field label="Local Backup Retention">{server.local_backup_retention || '—'}</Field>
      <Field label="Last Synced">{timeAgo(server.last_synced)}</Field>
      {server.last_error && (
        <div className="col-span-2 rounded-lg bg-status-critical/10 p-3 text-sm text-status-critical">{server.last_error}</div>
      )}
      {datastores.length > 0 && (
        <div className="col-span-2 mt-3">
          <h5 className="mb-3 text-xs font-semibold uppercase tracking-widest text-ink-muted">Storage</h5>
          <div className="space-y-3">
            {datastores.map((d) => (
              <UsageBar
                key={d.name}
                label={`${d.datastore_name} (${formatGB(d.used)}/${formatGB(d.total)})`}
                percent={d.usage_percent ?? 0}
                critical={!!d.is_critical}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function GuestDetail({ guest }: { guest: ProxmoxGuest }) {
  return (
    <div className="grid grid-cols-2 gap-x-10 gap-y-5 text-base">
      <Field label="Status"><StatusBadge status={guest.status} /></Field>
      <Field label="Type">{guest.guest_type}</Field>
      <Field label="VMID">{guest.vmid}</Field>
      <Field label="CPU">{formatPercent(guest.cpu_usage)}</Field>
      <Field label="Memory">{formatPercent(guest.memory_usage)} of {formatGB(guest.memory_total)}</Field>
      <Field label="Disk">{formatPercent(guest.disk_usage)} of {formatGB(guest.disk_total)}</Field>
      <Field label="Uptime">{formatUptime(guest.uptime)}</Field>
      <Field label="Last Successful Backup">
        {guest.last_successful_backup ? timeAgo(guest.last_successful_backup) : 'Never'}
      </Field>
      <Field label="IP Address">{guest.ip_address || guest.public_ip || '—'}</Field>
      <Field label="Network Mode">{guest.network_mode || '—'}</Field>
      <Field label="Access URL">{guest.access_url || '—'}</Field>
      <Field label="Assigned Engineer">{guest.assigned_engineer || '—'}</Field>
      <Field label="Tags">{guest.tags || '—'}</Field>
      <Field label="Last Synced">{timeAgo(guest.last_synced)}</Field>
    </div>
  )
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <div className="mb-1 text-xs uppercase tracking-wide text-ink-muted">{label}</div>
      <div className="text-ink-primary">{children}</div>
    </div>
  )
}
