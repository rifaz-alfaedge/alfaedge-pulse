export type SortDirection = 'asc' | 'desc'

/** Sort state with "click again to flip direction". */
export function nextSort<F extends string>(current: { field: F; dir: SortDirection }, field: F): { field: F; dir: SortDirection } {
  return current.field === field ? { field, dir: current.dir === 'asc' ? 'desc' : 'asc' } : { field, dir: 'desc' }
}

export function compareValues(a: unknown, b: unknown, dir: SortDirection): number {
  // Missing values always sort last, whichever the direction.
  const aMissing = a === null || a === undefined || a === ''
  const bMissing = b === null || b === undefined || b === ''
  if (aMissing || bMissing) return aMissing === bMissing ? 0 : aMissing ? 1 : -1
  const r = typeof a === 'number' && typeof b === 'number' ? a - b : String(a).localeCompare(String(b))
  return dir === 'asc' ? r : -r
}
