import type { ReactNode } from 'react'
import { Dialog } from '@rtcamp/frappe-ui-react'
import type { ProxmoxAlertLog, ProxmoxBackupLog, ProxmoxDatastore, ProxmoxGuest, ProxmoxServer } from '../lib/types'
import { formatGB, formatPercent, formatUptime, timeAgo } from '../lib/format'
import { StatusBadge } from './StatusBadge'
import { UsageBar } from './UsageBar'

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
