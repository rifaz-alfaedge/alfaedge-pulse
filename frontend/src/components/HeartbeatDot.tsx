import { isFresh } from '../lib/format'

/** A small pulsing dot showing whether a node's last reading is still fresh.
 * Green+pulsing when recently synced, red+pulsing when critical, grey/still
 * when stale (host unreachable, or the poller hasn't caught up yet) — this
 * is the "realtime heartbeat" cue requested for the dashboard. */
export function HeartbeatDot({
  lastSynced,
  pollIntervalSeconds,
  critical,
}: {
  lastSynced?: string
  pollIntervalSeconds: number
  critical?: boolean
}) {
  const fresh = isFresh(lastSynced, pollIntervalSeconds)

  let colorClass = 'bg-ink-muted/50'
  let animationClass = ''
  if (fresh) {
    if (critical) {
      colorClass = 'bg-status-critical'
      animationClass = 'heartbeat-critical'
    } else {
      colorClass = 'bg-status-good'
      animationClass = 'heartbeat-live'
    }
  }

  return (
    <span
      className={`inline-block h-3 w-3 rounded-full ${colorClass} ${animationClass}`}
      title={fresh ? 'Live' : 'Stale — last update was a while ago'}
    />
  )
}
