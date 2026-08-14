import { CircularProgressBar } from '@rtcamp/frappe-ui-react'
import { formatPercent } from '../lib/format'
import { MeterIcon } from './MeterIcon'

/** Which glyph identifies a meter at a glance, keyed off its label rather
 * than a separate explicit prop — storage meters carry all sorts of labels
 * (a guest's plain "Disk", a host's "OS+Backup"/"Guest LVM", or a raw PBS
 * datastore name), so "none of the other named kinds" reliably means
 * "storage" without the caller having to say so. */
/** Shared with `ResourceCard`, which only ever hands this component a meter
 * worth rendering in the first place — a card no longer shows every
 * CPU/RAM/disk ring all the time, only the ones currently at/above the
 * warning threshold, so both files need to agree on exactly what counts
 * as "needs attention." Missing data (`null`/`undefined`, e.g. VM disk
 * usage with no QEMU agent) is treated the same as "fine" here — nothing
 * to draw the eye to — even though `MeterRow` itself still renders an
 * explicit N/A ring if it's ever called with one directly. */
export function meterNeedsAttention(value: number | null | undefined, warningThreshold: number): boolean {
  return value !== null && value !== undefined && value >= warningThreshold
}

function iconFor(label: string): Parameters<typeof MeterIcon>[0]['name'] {
  if (label === 'CPU') return 'cpu'
  if (label === 'RAM') return 'ram'
  // "repeat" — two arrows forming a cycle — is feather-icons' closest
  // built-in to the conventional swap/exchange symbol; there's no
  // dedicated swap-memory glyph in the set.
  if (label === 'Swap') return 'repeat'
  return 'hard-drive'
}

/** One CPU/RAM/Disk ring inside a ResourceCard. `value` of null/undefined
 * renders a muted "N/A" instead of a ring — used for VM disk usage, which
 * Proxmox can only report if the in-guest QEMU agent is installed.
 *
 * Follows the dataviz-skill meter spec — fill carries severity on an
 * accent → warning → danger progression rather than a flat on/off red —
 * via CircularProgressBar's own documented `theme` prop, since that's the
 * one color-control surface the component actually exposes (it doesn't
 * take arbitrary hex, only a small fixed set of named themes). A small
 * identifying icon sits as a badge on the ring's rim.
 *
 * CircularProgressBar always renders a number in the center with no prop
 * to suppress it (`showPercentage={false}` falls back to the raw step
 * count, not blank) — so on warning/critical, an opaque badge is overlaid
 * on top of that number instead, covering it with a warning/critical icon.
 * The ring itself gets a pulsing glow on critical to draw the eye, reusing
 * the same box-shadow technique as the existing heartbeat-dot animation. */
export function MeterRow({
  label,
  value,
  warningThreshold,
  criticalThreshold,
}: {
  label: string
  value?: number | null
  warningThreshold: number
  criticalThreshold: number
}) {
  if (value === undefined || value === null) {
    return (
      <div className="flex flex-col items-center gap-2">
        {/* Matches CircularProgressBar's "lg" ring diameter (84px) below, so the
         * N/A placeholder sits flush with real meters in the same row. */}
        <div className="flex h-[84px] w-[84px] items-center justify-center rounded-full border-2 border-dashed border-border-hairline text-sm text-ink-muted">
          N/A
        </div>
        <span className="text-sm text-ink-secondary">{label}</span>
      </div>
    )
  }

  // Mirrors the backend's own two-phase severity (see poller.py's
  // _track_severity) — this is a live snapshot of the *current* reading,
  // not the sustained-5-minutes signal the backend actually alerts on, so
  // a ring can show amber here on its very first over-85% cycle rather
  // than waiting out the same grace period. That's intentional: the ring
  // is glanceable live status, not a promise that an alert has fired.
  const critical = value >= criticalThreshold
  const approaching = !critical && value >= warningThreshold
  const theme = critical ? 'red' : approaching ? 'orange' : 'blue'
  const labelClass = critical ? 'text-status-critical' : approaching ? 'text-status-warning' : 'text-ink-secondary'

  return (
    <div className="flex flex-col items-center gap-2">
      <div className={`relative rounded-full ${critical ? 'meter-critical-pulse' : ''}`}>
        {/* "lg" (84px) — smaller than the previous "xl" (108px): a card no
         * longer shows every ring all the time (see ResourceCard's
         * filtering), only the ones that need attention, so the rings
         * themselves can afford to sit less prominently too. */}
        <CircularProgressBar step={Math.round(value)} totalSteps={100} size="lg" showPercentage theme={theme} />
        {(critical || approaching) && (
          // z-10: CircularProgressBar's own inner text wrapper sets
          // z-index: 2 with no stacking context of its own, so it'd
          // otherwise paint on top of a later, unindexed sibling despite
          // DOM order — this badge needs an explicit higher z-index to
          // actually cover that number.
          <div className="absolute inset-0 z-10 flex items-center justify-center">
            <div className="flex h-11 w-11 items-center justify-center rounded-full bg-surface-card">
              <MeterIcon name={critical ? 'alert-octagon' : 'alert-triangle'} className={`h-6 w-6 ${labelClass}`} />
            </div>
          </div>
        )}
        <div className="absolute left-1/2 top-0 flex h-6 w-6 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full bg-surface-card">
          <MeterIcon name={iconFor(label)} className={`h-4 w-4 ${labelClass}`} />
        </div>
      </div>
      <span className={`text-sm font-medium ${labelClass}`}>
        {label} {formatPercent(value)}
      </span>
    </div>
  )
}
