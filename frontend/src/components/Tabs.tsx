/** Hand-rolled segmented tab control.
 *
 * Replaces the library's `TabButtons` (whose public API is just
 * `buttons`/`value`/`onChange`/`className`, with no size/padding control)
 * so we can guarantee a comfortably thick, readable bar. Colors are the
 * shared design tokens (see index.css) so it re-themes for free — no
 * `dark:` variants needed here. */
/** `'lg'` is for the dashboard's one primary navigation bar (Overview/
 * Backups/Alerts/...) — every other use of this component (the AI Usage/
 * Backups/Uptime tabs' own internal filter-pill rows) is a secondary
 * control and should stay at the default size, not read as equally
 * important. */
const SIZE_CLASS = {
  md: { container: 'p-1.5', button: 'px-4 py-2.5 text-sm font-medium' },
  lg: { container: 'p-2', button: 'px-6 py-3.5 text-base font-semibold' },
}

export function Tabs({
  options,
  value,
  onChange,
  size = 'md',
}: {
  options: string[]
  value: string
  onChange: (value: string) => void
  size?: 'md' | 'lg'
}) {
  const sizeClass = SIZE_CLASS[size]
  return (
    <div className={`inline-flex flex-wrap gap-1 rounded-xl bg-ink-primary/5 ${sizeClass.container}`}>
      {options.map((option) => {
        const active = option === value
        return (
          <button
            key={option}
            type="button"
            onClick={() => onChange(option)}
            className={`rounded-lg transition-colors ${sizeClass.button} ${
              active
                ? 'bg-surface-card text-ink-primary shadow-sm'
                : 'text-ink-secondary hover:text-ink-primary'
            }`}
          >
            {option}
          </button>
        )
      })}
    </div>
  )
}
