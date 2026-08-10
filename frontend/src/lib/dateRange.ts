export type DateRangePreset = 'today' | 'yesterday' | '7d' | '30d' | '90d' | 'custom'

export const DATE_RANGE_PRESETS: { value: DateRangePreset; label: string }[] = [
  { value: 'today', label: 'Today' },
  { value: 'yesterday', label: 'Yesterday' },
  { value: '7d', label: 'Last 7 Days' },
  { value: '30d', label: 'Last 30 Days' },
  { value: '90d', label: 'Last 90 Days' },
  { value: 'custom', label: 'Custom Range' },
]

const PRESET_DAYS: Record<'7d' | '30d' | '90d', number> = { '7d': 7, '30d': 30, '90d': 90 }

function pad(n: number): string {
  return String(n).padStart(2, '0')
}

/** Site-local calendar date, e.g. "2026-08-10" — matches the `<input
 * type="date">` value format and this app's "naive string = site-local"
 * datetime convention (see lib/format.ts). */
export function toDateKey(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

function dayBounds(d: Date): { startDate: string; endDate: string } {
  const key = toDateKey(d)
  return { startDate: `${key} 00:00:00`, endDate: `${key} 23:59:59` }
}

/** Resolves a preset (or a custom start/end pair of `<input type="date">`
 * values) into full "YYYY-MM-DD HH:MM:SS" datetime strings. A plain
 * date-only `end_date` would make the backend's `get_datetime()` treat it
 * as midnight and silently drop that whole day's data, so every branch
 * here always returns an explicit end-of-day time. */
export function resolveDateRange(
  preset: DateRangePreset,
  customStart: string,
  customEnd: string,
): { startDate: string; endDate: string } {
  const now = new Date()
  if (preset === 'today') return dayBounds(now)
  if (preset === 'yesterday') {
    const y = new Date(now)
    y.setDate(y.getDate() - 1)
    return dayBounds(y)
  }
  if (preset === 'custom') {
    const start = customStart || toDateKey(now)
    const end = customEnd || toDateKey(now)
    return { startDate: `${start} 00:00:00`, endDate: `${end} 23:59:59` }
  }
  const start = new Date(now)
  start.setDate(start.getDate() - (PRESET_DAYS[preset] - 1))
  return { startDate: dayBounds(start).startDate, endDate: dayBounds(now).endDate }
}
