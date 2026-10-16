import { useMemo, useState } from 'react'
import { Dialog } from '@rtcamp/frappe-ui-react'
import { useFrappeGetCall, useFrappePostCall } from 'frappe-react-sdk'
import type { SiteOverview, SiteOverviewHost } from '../lib/types'
import { getErrorMessage } from '../lib/errors'
import { timeAgo } from '../lib/format'

const inputClass =
  'w-full rounded-lg border border-border-hairline bg-surface-card px-3 py-1.5 text-sm text-ink-primary placeholder:text-ink-muted focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/20'

/** Every host seen in the logs, and what it's shown as. http/https
 * variants of one host are already merged automatically (shown here as
 * its list of URLs); this is for the rest — a site logged under a bare
 * name like "lulu" mapped onto its real site, or onto any display name.
 * Saving writes a Log Site Alias, applied inside every ClickHouse query,
 * so it regroups history too. */
export function ManageSitesDialog({
  canEdit,
  onClose,
  onChanged,
}: {
  canEdit: boolean
  onClose: () => void
  onChanged: () => void
}) {
  const { data, error, isLoading, mutate } = useFrappeGetCall<{ message: SiteOverview }>(
    'alfaedge_pulse.log_monitor.api.get_site_overview',
  )
  const overview = data?.message
  const [filter, setFilter] = useState('')

  const hosts = useMemo(() => {
    const all = overview?.hosts ?? []
    const q = filter.trim().toLowerCase()
    if (!q) return all
    return all.filter((h) => h.host.includes(q) || (h.alias ?? '').toLowerCase().includes(q))
  }, [overview, filter])

  // Bare single-word hosts are the likely candidates for an alias — list them first.
  const sorted = useMemo(
    () => [...hosts].sort((a, b) => Number(a.host.includes('.')) - Number(b.host.includes('.')) || b.logs - a.logs),
    [hosts],
  )

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()} options={{ title: 'Sites', size: '4xl' }}>
      <div className="flex max-h-[70vh] flex-col gap-4 py-2">
        <p className="text-sm text-ink-secondary">
          http:// and https:// addresses of the same host are combined automatically. To merge a host such as{' '}
          <span className="font-mono">lulu</span> into its real site, pick that site under <em>Show as</em>, or type any
          name. This also regroups past logs.
        </p>
        <input className={inputClass} value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter hosts…" />
        {error && <p className="text-sm text-status-critical">{getErrorMessage(error)}</p>}
        {isLoading && !overview && <p className="text-sm text-ink-muted">Loading…</p>}
        <datalist id="log-site-suggestions">
          {(overview?.suggestions ?? []).map((s) => (
            <option key={s} value={s} />
          ))}
        </datalist>
        <div className="overflow-auto rounded-lg border border-border-hairline">
          <table className="w-full min-w-[680px] text-left text-sm">
            <thead className="sticky top-0 border-b border-border-hairline bg-surface-card text-xs uppercase tracking-wide text-ink-muted">
              <tr>
                <th className="px-4 py-2 font-medium">Host in logs</th>
                <th className="px-3 py-2 text-right font-medium">Logs (30d)</th>
                <th className="px-4 py-2 font-medium">Show as</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((h) => (
                <SiteRow
                  key={h.host}
                  host={h}
                  canEdit={canEdit}
                  onSaved={() => {
                    mutate()
                    onChanged()
                  }}
                />
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </Dialog>
  )
}

function SiteRow({ host, canEdit, onSaved }: { host: SiteOverviewHost; canEdit: boolean; onSaved: () => void }) {
  const [value, setValue] = useState(host.alias ?? '')
  const [err, setErr] = useState<string | null>(null)
  const { call, loading } = useFrappePostCall<{ message: { source_host: string; site: string | null } }>(
    'alfaedge_pulse.log_monitor.api.save_site_alias',
  )
  const dirty = value.trim() !== (host.alias ?? '')

  const save = async (site: string) => {
    setErr(null)
    try {
      const r = await call({ source_host: host.host, site })
      setValue(r.message.site ?? '')
      onSaved()
    } catch (e) {
      setErr(getErrorMessage(e))
    }
  }

  return (
    <tr className="border-b border-border-hairline align-top last:border-0">
      <td className="px-4 py-2.5">
        <div className="font-medium text-ink-primary">{host.host}</div>
        <div className="mt-0.5 flex flex-wrap gap-1">
          {host.urls.map((u) => (
            <span key={u} className="rounded bg-surface-page px-1.5 py-0.5 font-mono text-[11px] text-ink-muted">
              {u}
            </span>
          ))}
          {host.urls.length > 1 && <span className="text-[11px] text-status-good">combined</span>}
        </div>
        <div className="mt-0.5 text-[11px] text-ink-muted">last log {timeAgo(host.last_seen)}</div>
      </td>
      <td className="px-3 py-2.5 text-right tabular-nums text-ink-secondary">{host.logs.toLocaleString()}</td>
      <td className="px-4 py-2.5">
        {canEdit ? (
          <div className="flex flex-col gap-1">
            <div className="flex gap-2">
              <input
                className={inputClass}
                list="log-site-suggestions"
                value={value}
                placeholder="(itself)"
                onChange={(e) => setValue(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && dirty && save(value)}
              />
              <button
                type="button"
                disabled={!dirty || loading}
                onClick={() => save(value)}
                className="shrink-0 rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white enabled:hover:opacity-90 disabled:opacity-40"
              >
                Save
              </button>
              {host.alias && (
                <button
                  type="button"
                  disabled={loading}
                  onClick={() => save('')}
                  className="shrink-0 rounded-lg border border-border-hairline px-3 py-1.5 text-xs font-medium text-ink-primary enabled:hover:border-accent"
                >
                  Reset
                </button>
              )}
            </div>
            {err && <span className="text-xs text-status-critical">{err}</span>}
          </div>
        ) : (
          <span className="text-ink-secondary">{host.alias ?? '(itself)'}</span>
        )}
      </td>
    </tr>
  )
}
