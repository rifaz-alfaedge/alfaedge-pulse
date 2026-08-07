import { CircularProgressBar } from '@rtcamp/frappe-ui-react'
import { formatPercent } from '../lib/format'
import { MeterIcon } from './MeterIcon'

/** Which glyph identifies a meter at a glance, keyed off its label rather
 * than a separate explicit prop — storage meters carry all sorts of labels
 * (a guest's plain "Disk", a host's "OS+Backup"/"Guest LVM", or a raw PBS
 * datastore name), so "none of the other named kinds" reliably means
 * "storage" without the caller having to say so. */
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
 * identifying icon sits as a badge on the ring's rim rather than
 * overlapping the percentage in the center — CircularProgressBar always
 * renders that number itself with no way to substitute it, and the
 * percentage is the one thing on this whole dashboard nobody wants
 * crowded out. */
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
        {/* Matches CircularProgressBar's "xl" ring diameter (108px) below, so the
         * N/A placeholder sits flush with real meters in the same row. */}
        <div className="flex h-28 w-28 items-center justify-center rounded-full border-2 border-dashed border-border-hairline text-sm text-ink-muted">
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
      <div className="relative">
        {/* "xl" (108px) — at least 2x the previous "sm" (42px) size per explicit request. */}
        <CircularProgressBar step={Math.round(value)} totalSteps={100} size="xl" showPercentage theme={theme} />
        <div className="absolute left-1/2 top-0 flex h-8 w-8 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full bg-surface-card">
          <MeterIcon name={iconFor(label)} className={`h-5 w-5 ${labelClass}`} />
        </div>
      </div>
      <span className={`text-sm font-medium ${labelClass}`}>
        {label} {formatPercent(value)}
      </span>
    </div>
  )
}
