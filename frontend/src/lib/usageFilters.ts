/** Filter state for the Usage Metrics tables (components/UsageTables.tsx),
 * kept in App so the status strip's Critical/Offline pills can drive them. */
export type Severity = 'critical' | 'warning' | 'ok'

export interface HostFilters {
  role: string[]
  status: string[]
  severity: string[]
}

export interface GuestFilters {
  search: string
  host: string[]
  status: string[]
  type: string[]
  hostHealth: string[]
  severity: string[]
}

export const EMPTY_HOST_FILTERS: HostFilters = { role: [], status: [], severity: [] }
export const EMPTY_GUEST_FILTERS: GuestFilters = {
  search: '', host: [], status: [], type: [], hostHealth: [], severity: [],
}
