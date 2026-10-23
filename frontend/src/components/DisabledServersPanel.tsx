import { useState } from 'react'
import { useFrappeGetCall, useFrappePostCall } from 'frappe-react-sdk'
import { getErrorMessage } from '../lib/errors'
import { timeAgo } from '../lib/format'

const API = 'alfaedge_pulse.proxmox_fleet_monitor.server_deletion'

interface DisabledServer {
  name: string
  server_name: string
  hostname: string
  role: string | null
  server_type: string
  last_synced: string | null
}

interface ServerArchive {
  name: string
  server_name: string
  status: 'Queued' | 'Running' | 'Completed' | 'Failed'
  requested_by: string
  requested_at: string
  completed_at: string | null
  archive_file: string | null
  error: string | null
}

const STATUS_CLASS: Record<ServerArchive['status'], string> = {
  Queued: 'bg-status-warning/15 text-status-warning',
  Running: 'bg-status-warning/15 text-status-warning',
  Completed: 'bg-status-good/10 text-status-good',
  Failed: 'bg-status-critical/10 text-status-critical',
}

/** Managers-only: disabled Proxmox Servers (hidden everywhere else on the
 * dashboard) with a Delete action, and recent deletions with their
 * archives. Deletion runs in the background; see server_deletion.py. */
export function DisabledServersPanel() {
  const { data, mutate } = useFrappeGetCall<{ message: { disabled: DisabledServer[]; archives: ServerArchive[] } }>(
    `${API}.get_server_deletions`,
    undefined,
    undefined,
    { refreshInterval: 10000 },
  )
  const disabled = data?.message.disabled ?? []
  const archives = data?.message.archives ?? []
  if (!data || (disabled.length === 0 && archives.length === 0)) return null

  return (
    <section className="mt-12">
      <h2 className="mb-4 text-sm font-semibold uppercase tracking-widest text-ink-muted">Disabled servers</h2>
      <div className="rounded-2xl border border-border-hairline bg-surface-card">
        {disabled.length === 0 ? (
          <p className="px-5 py-4 text-sm text-ink-muted">No disabled servers.</p>
        ) : (
          disabled.map((s) => <DisabledRow key={s.name} server={s} onDone={() => mutate()} />)
        )}
        {archives.length > 0 && (
          <div className="border-t border-border-hairline px-5 py-4">
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-widest text-ink-muted">Recent deletions</h3>
            <div className="flex flex-col gap-2">
              {archives.map((a) => (
                <div key={a.name} className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
                  <span className="font-medium text-ink-primary">{a.server_name}</span>
                  <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${STATUS_CLASS[a.status]}`}>{a.status}</span>
                  <span className="text-xs text-ink-muted">
                    by {a.requested_by} · {timeAgo(a.completed_at || a.requested_at)}
                  </span>
                  {a.archive_file && a.status === 'Completed' && (
                    <a href={a.archive_file} className="text-xs font-medium text-accent hover:underline">
                      Download archive
                    </a>
                  )}
                  <a href={`/app/proxmox-server-archive/${encodeURIComponent(a.name)}`} className="text-xs text-ink-muted hover:underline">
                    Details
                  </a>
                  {a.status === 'Failed' && a.error && (
                    <span className="w-full truncate text-xs text-status-critical" title={a.error}>
                      {a.error.trim().split('\n').pop()}
                    </span>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </section>
  )
}

function DisabledRow({ server, onDone }: { server: DisabledServer; onDone: () => void }) {
  const [confirming, setConfirming] = useState(false)
  const [typed, setTyped] = useState('')
  const [error, setError] = useState<string | null>(null)
  const { call, loading } = useFrappePostCall(`${API}.request_server_deletion`)

  return (
    <div className="border-b border-border-hairline px-5 py-3 last:border-0">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="font-medium text-ink-primary">{server.server_name}</div>
          <div className="text-xs text-ink-muted">
            {[server.server_type === 'PBS' ? 'Backup server' : server.role, server.hostname, server.last_synced && `last synced ${timeAgo(server.last_synced)}`]
              .filter(Boolean)
              .join(' · ')}
          </div>
        </div>
        {!confirming && (
          <button
            type="button"
            onClick={() => setConfirming(true)}
            className="rounded-lg border border-status-critical/40 px-3 py-1.5 text-xs font-medium text-status-critical hover:bg-status-critical/10"
          >
            Delete…
          </button>
        )}
      </div>
      {confirming && (
        <div className="mt-3 flex flex-col gap-2 rounded-lg bg-status-critical/5 p-3">
          <p className="text-xs text-ink-secondary">
            Deletes <b>{server.server_name}</b> and everything linked to it: VMs/CTs, datastores, backups, alerts, metric history,
            Host Health agents, sites and its NPM. An archive is saved first. Type <b>{server.server_name}</b> to confirm.
          </p>
          <div className="flex flex-wrap gap-2">
            <input
              className="w-64 rounded-lg border border-border-hairline bg-surface-card px-3 py-1.5 text-sm text-ink-primary focus:border-status-critical focus:outline-none"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              placeholder={server.server_name}
              autoFocus
            />
            <button
              type="button"
              disabled={loading || typed.trim() !== server.server_name}
              onClick={async () => {
                setError(null)
                try {
                  await call({ server: server.name, confirm_name: typed.trim() })
                  setConfirming(false)
                  setTyped('')
                  onDone()
                } catch (e) {
                  setError(getErrorMessage(e))
                }
              }}
              className="rounded-lg bg-status-critical px-3 py-1.5 text-xs font-medium text-white enabled:hover:opacity-90 disabled:opacity-40"
            >
              Delete permanently
            </button>
            <button
              type="button"
              onClick={() => {
                setConfirming(false)
                setTyped('')
                setError(null)
              }}
              className="rounded-lg border border-border-hairline px-3 py-1.5 text-xs font-medium text-ink-primary"
            >
              Cancel
            </button>
          </div>
          {error && <p className="text-xs text-status-critical">{error}</p>}
        </div>
      )}
    </div>
  )
}
