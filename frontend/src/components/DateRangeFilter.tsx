import { DATE_RANGE_PRESETS, type DateRangePreset } from '../lib/dateRange'

const selectClass =
  'rounded-lg border border-border-hairline bg-surface-page px-3 py-2 text-sm text-ink-primary outline-none focus:border-accent'

/** Timespan picker: a preset dropdown (Today / Yesterday / Last N Days),
 * plus a pair of native date inputs that only appear for "Custom Range" —
 * replaces the old fixed 7d/30d/90d tab strip. */
export function DateRangeFilter({
  preset,
  customStart,
  customEnd,
  onPresetChange,
  onCustomChange,
}: {
  preset: DateRangePreset
  customStart: string
  customEnd: string
  onPresetChange: (preset: DateRangePreset) => void
  onCustomChange: (start: string, end: string) => void
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <select
        aria-label="Date range"
        value={preset}
        onChange={(e) => onPresetChange(e.target.value as DateRangePreset)}
        className={selectClass}
      >
        {DATE_RANGE_PRESETS.map((p) => (
          <option key={p.value} value={p.value}>
            {p.label}
          </option>
        ))}
      </select>
      {preset === 'custom' && (
        <>
          <input
            type="date"
            aria-label="Start date"
            value={customStart}
            max={customEnd || undefined}
            onChange={(e) => onCustomChange(e.target.value, customEnd)}
            className={selectClass}
          />
          <span className="text-sm text-ink-muted">to</span>
          <input
            type="date"
            aria-label="End date"
            value={customEnd}
            min={customStart || undefined}
            onChange={(e) => onCustomChange(customStart, e.target.value)}
            className={selectClass}
          />
        </>
      )}
    </div>
  )
}
