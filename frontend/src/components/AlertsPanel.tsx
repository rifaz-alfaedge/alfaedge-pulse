import { useMemo, useState } from 'react'
import type { ProxmoxAlertLog } from '../lib/types'
import { timeAgo } from '../lib/format'
import { Tabs } from './Tabs'

const FILTERS = ['Open', 'All']

export function AlertsPanel({ alertLogs }: { alertLogs: ProxmoxAlertLog[] }) {
  const [filter, setFilter] = useState<string>('Open')

  const filtered = useMemo(
    () => (filter === 'Open' ? alertLogs.filter((a) => !a.resolved) : alertLogs),
    [alertLogs, filter],
  )

  return (
    <div>
      <div className="mb-6">
        <Tabs options={FILTERS} value={filter} onChange={setFilter} />
      </div>

      {filtered.length === 0 ? (
        <p className="text-sm text-ink-muted">
          {filter === 'Open' ? 'No open incidents. 🎉' : 'No alerts recorded yet.'}
        </p>
      ) : (
        <div className="space-y-3">
          {filtered.map((a) => (
            <div
              key={a.name}
              className={`rounded-2xl border border-l-4 border-border-hairline bg-surface-card p-5 text-sm ${
                a.resolved ? 'border-l-transparent' : 'border-l-status-critical'
              }`}
            >
              <div className="mb-2 flex items-center justify-between text-xs">
                <span className="font-semibold uppercase tracking-widest text-ink-muted">{a.alert_type}</span>
                <span className="text-ink-muted">{timeAgo(a.sent_at)}</span>
              </div>
              <p className="text-base text-ink-primary">{a.message}</p>
              <div className="mt-2 flex items-center justify-between text-xs text-ink-secondary">
                <span>{a.channels_sent ? `Sent via ${a.channels_sent}` : 'No channels configured'}</span>
                <span className={a.resolved ? 'text-ink-muted' : 'font-medium text-status-critical'}>
                  {a.resolved ? 'Resolved' : 'Open'}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
