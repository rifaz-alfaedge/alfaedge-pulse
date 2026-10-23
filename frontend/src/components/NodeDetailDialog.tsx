import { type ReactNode, useState } from 'react'
import { Dialog } from '@rtcamp/frappe-ui-react'
import { useGuestMetricHistory, useHostMetricHistory, useInstanceSites } from '../lib/hooks'
import type { InstanceSite, ProxmoxAlertLog, ProxmoxBackupLog, ProxmoxGuest, ProxmoxServer } from '../lib/types'
import { formatGB, formatPercent, timeAgo } from '../lib/format'
import { StatusBadge } from './StatusBadge'
import { TrendChart } from './TrendChart'

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
  backupLogs,
  alertLogs,
}: {
  selected: SelectedNode | null
  onClose: () => void
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
        {/* Only what the Usage Metrics table doesn't already show. */}
        {isServer ? <HostDetail server={selected.doc as ProxmoxServer} /> : <GuestDetail guest={selected.doc as ProxmoxGuest} />}

        <SitesSection kind={isServer ? 'server' : 'guest'} name={selected.doc.name} />

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

function HostDetail({ server }: { server: ProxmoxServer }) {
  return (
    <div className="grid grid-cols-2 gap-x-10 gap-y-5 text-base">
      <Field label="Datacenter">{server.datacenter_location ? `${server.datacenter_location} (${server.cloud_provider})` : '—'}</Field>
      <Field label="Root Filesystem (/)">{formatPercent(server.storage_usage)} of {formatGB(server.storage_total)}</Field>
      <Field label="Backups">
        <span className={server.backup_critical ? 'font-semibold text-status-critical' : undefined}>
          {server.backup_critical ? 'No successful backup in 24h' : 'OK'}
        </span>
      </Field>
      <Field label="Local Backup Retention">{server.local_backup_retention || '—'}</Field>
      {server.last_error && (
        <div className="col-span-2 rounded-lg bg-status-critical/10 p-3 text-sm text-status-critical">{server.last_error}</div>
      )}
    </div>
  )
}

function GuestDetail({ guest }: { guest: ProxmoxGuest }) {
  const fields: [string, ReactNode][] = [
    ['Network Mode', guest.network_mode],
    ['Access URL', guest.access_url],
    ['Assigned Engineer', guest.assigned_engineer],
    ['Last Synced', guest.last_synced ? timeAgo(guest.last_synced) : null],
  ]
  const shown = fields.filter(([, v]) => v)
  if (!shown.length) return null
  return (
    <div className="grid grid-cols-2 gap-x-10 gap-y-5 text-base">
      {shown.map(([label, value]) => (
        <Field key={label} label={label}>{value}</Field>
      ))}
    </div>
  )
}

const SOURCE_LABEL: Record<InstanceSite['source'], string> = { NPM: 'NPM', 'Direct DNS': 'Direct DNS', 'Pulse Agent': 'Agent' }

/** Site URLs on this VM/CT (or served through this host's NPM), from the
 * daily NPM + Cloudflare + Pulse agent site sync (site_discovery/sync.py). */
function SitesSection({ kind, name }: { kind: 'guest' | 'server'; name: string }) {
  const { data, isLoading } = useInstanceSites(kind, name)
  const sites = data ?? []
  return (
    <section>
      <h4 className="mb-2.5 text-xs font-semibold uppercase tracking-widest text-ink-muted">
        {kind === 'server' ? "Sites via this host's NPM" : 'Sites'} {sites.length > 0 && `(${sites.length})`}
      </h4>
      {isLoading && !data ? (
        <p className="text-sm text-ink-muted">Loading…</p>
      ) : sites.length === 0 ? (
        <p className="text-sm text-ink-muted">No sites found yet.</p>
      ) : (
        <div className="max-h-72 space-y-2 overflow-y-auto pr-1">
          {sites.map((s) => (
            <div key={s.name} className="rounded-lg border border-border-hairline px-3 py-2">
              <div className="flex flex-wrap items-center gap-2">
                <a
                  href={`https://${s.domain}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-medium text-accent hover:underline"
                >
                  {s.domain} ↗
                </a>
                <span className="rounded-full bg-surface-page px-2 py-0.5 text-[11px] text-ink-secondary">{SOURCE_LABEL[s.source]}</span>
                {s.status !== 'Active' && (
                  <span
                    className="rounded-full bg-status-warning/15 px-2 py-0.5 text-[11px] font-medium text-status-warning"
                    title={s.note ?? undefined}
                  >
                    {s.status}
                  </span>
                )}
              </div>
              <div className="mt-1 text-xs text-ink-muted">
                {[
                  s.npm_instance && `via ${s.npm_instance}${s.forward_target ? ` → ${s.forward_target}` : ''}`,
                  s.cloudflare_target && `Cloudflare → ${s.cloudflare_target}${s.cloudflare_proxied ? ' (proxied)' : ''}`,
                  s.also_seen_in && `also: ${s.also_seen_in}`,
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </div>
              {s.status !== 'Active' && s.note && <div className="mt-1 text-xs text-status-warning">{s.note}</div>}
            </div>
          ))}
        </div>
      )}
    </section>
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
