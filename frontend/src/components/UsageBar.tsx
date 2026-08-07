/** A single labeled horizontal usage bar — e.g. one drive's capacity.
 * Follows the dataviz-skill meter spec: fill carries severity, the
 * unfilled track is a lighter step of the same ramp, rounded data-ends. */
export function UsageBar({
  label,
  percent,
  critical,
}: {
  label: string
  percent: number
  critical: boolean
}) {
  return (
    <div className="flex items-center gap-3">
      <span className="w-32 shrink-0 truncate text-sm text-ink-secondary">{label}</span>
      <div className="h-2 flex-1 overflow-hidden rounded-full bg-accent-track/30">
        <div
          className={`h-full rounded-full ${critical ? 'bg-status-critical' : 'bg-accent'}`}
          style={{ width: `${Math.min(Math.max(percent, 0), 100)}%` }}
        />
      </div>
      <span className={`w-12 shrink-0 text-right text-sm font-medium ${critical ? 'text-status-critical' : 'text-ink-secondary'}`}>
        {Math.round(percent)}%
      </span>
    </div>
  )
}
