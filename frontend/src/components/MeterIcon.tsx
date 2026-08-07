import feather from 'feather-icons'

type IconName = keyof typeof feather.icons | 'ram'

/** feather-icons has no RAM-module glyph of its own — the closest built-ins
 * ("server", "database") read as generic infrastructure rather than memory
 * specifically. Hand-drawn instead: a horizontal stick with connector pins
 * along the bottom edge, the standard silhouette for "RAM" across most icon
 * sets, traced in the exact same 24x24 / 2px-stroke / round-cap convention
 * as every feather glyph so it doesn't look out of place next to them. */
const RAM_ICON = {
  attrs: { viewBox: '0 0 24 24' },
  contents:
    '<rect x="2" y="6" width="20" height="9" rx="1"/>' +
    '<line x1="6" y1="15" x2="6" y2="18"/>' +
    '<line x1="10" y1="15" x2="10" y2="18"/>' +
    '<line x1="14" y1="15" x2="14" y2="18"/>' +
    '<line x1="18" y1="15" x2="18" y2="18"/>',
}

/** A single small glyph rendered inside a MeterRow's ring, identifying what
 * the meter measures (CPU/RAM/storage) at a glance without reading the
 * label underneath. Pulls from `feather-icons` directly rather than
 * frappe-ui-react's own FeatherIcon wrapper — that wrapper exists, but
 * isn't part of the library's public exports (only used internally for
 * CircularProgressBar's own completion checkmark) — with one hand-drawn
 * exception (see RAM_ICON above) for the one glyph feather doesn't have. */
export function MeterIcon({ name, className }: { name: IconName; className?: string }) {
  const icon = name === 'ram' ? RAM_ICON : feather.icons[name]
  return (
    <svg
      viewBox={icon.attrs.viewBox}
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth={2}
      className={className}
      dangerouslySetInnerHTML={{ __html: icon.contents }}
    />
  )
}
