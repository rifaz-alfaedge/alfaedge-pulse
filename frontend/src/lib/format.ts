/** Human-friendly "Xs/Xm/Xh ago" for a Frappe datetime string (site-local, treated as UTC-naive). */
export function timeAgo(datetime?: string | null): string {
  if (!datetime) return 'never'
  const then = new Date(datetime.replace(' ', 'T') + 'Z').getTime()
  const seconds = Math.max(0, Math.round((Date.now() - then) / 1000))
  if (seconds < 5) return 'just now'
  if (seconds < 60) return `${seconds}s ago`
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.round(hours / 24)}d ago`
}

/** Proxmox `uptime` is seconds since boot; render as the largest one or two sensible units. */
export function formatUptime(seconds?: number | null): string {
  if (!seconds || seconds <= 0) return '—'
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  if (days > 0) return `${days}d ${hours}h`
  if (hours > 0) return `${hours}h ${minutes}m`
  return `${minutes}m`
}

export function formatGB(value?: number | null): string {
  if (value === undefined || value === null) return '—'
  if (value < 1) return `${Math.round(value * 1024)} MB`
  return `${value.toFixed(1)} GB`
}

export function formatPercent(value?: number | null): string {
  if (value === undefined || value === null) return '—'
  return `${Math.round(value)}%`
}

/** Is this reading recent enough to trust as "live"? Used to grey out stale cards
 * when a host is unreachable rather than showing a frozen number as if it were current. */
export function isFresh(lastSynced: string | undefined, pollIntervalSeconds: number): boolean {
  if (!lastSynced) return false
  const then = new Date(lastSynced.replace(' ', 'T') + 'Z').getTime()
  return Date.now() - then < pollIntervalSeconds * 4000
}
