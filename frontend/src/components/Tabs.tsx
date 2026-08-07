/** Hand-rolled segmented tab control.
 *
 * Replaces the library's `TabButtons` (whose public API is just
 * `buttons`/`value`/`onChange`/`className`, with no size/padding control)
 * so we can guarantee a comfortably thick, readable bar. Colors are the
 * shared design tokens (see index.css) so it re-themes for free — no
 * `dark:` variants needed here. */
export function Tabs({
  options,
  value,
  onChange,
}: {
  options: string[]
  value: string
  onChange: (value: string) => void
}) {
  return (
    <div className="inline-flex flex-wrap gap-1 rounded-xl bg-ink-primary/5 p-1.5">
      {options.map((option) => {
        const active = option === value
        return (
          <button
            key={option}
            type="button"
            onClick={() => onChange(option)}
            className={`rounded-lg px-4 py-2.5 text-sm font-medium transition-colors ${
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
