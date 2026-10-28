import { useEffect, useRef, useState } from 'react'

const triggerClass =
  'rounded-lg border border-border-hairline bg-surface-page px-3 py-2 text-sm text-ink-primary outline-none focus:border-accent'

/** Compact multi-select dropdown — a button showing a summary of the
 * current selection, opening a checkbox-list popover on click. Replaces a
 * plain single-value `<select>` wherever a filter needs to allow picking
 * several values at once (AI Usage's Provider/Model/Virtual Key, the
 * dashboard header's server filter). Checkbox-list styling follows
 * UptimePanel.tsx's existing import-monitor picker for consistency, since
 * that's the only other checkbox-list in this app. Empty `selected` always
 * means "no filter" (everything shown), matching how the single-selects
 * this replaces used `''` for "All" — never "select nothing to show
 * nothing."
 *
 * ``searchable`` adds a type-to-filter box above the list (for long lists
 * such as the Error Logs tab's Sites); Select all / Clear then act on the
 * matching options only. */
export function MultiSelectFilter({
  label,
  options,
  selected,
  onChange,
  searchable = false,
}: {
  label: string
  options: string[]
  selected: string[]
  onChange: (next: string[]) => void
  searchable?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const rootRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!open) return
    const onClickOutside = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) close()
    }
    document.addEventListener('mousedown', onClickOutside)
    return () => document.removeEventListener('mousedown', onClickOutside)
  }, [open])

  const close = () => {
    setOpen(false)
    setQuery('')
  }

  const needle = query.trim().toLowerCase()
  const shown = needle ? options.filter((o) => o.toLowerCase().includes(needle)) : options
  const allShownSelected = shown.length > 0 && shown.every((o) => selected.includes(o))
  const toggleShown = () =>
    onChange(allShownSelected ? selected.filter((v) => !shown.includes(v)) : [...new Set([...selected, ...shown])])

  const toggle = (option: string) => {
    onChange(selected.includes(option) ? selected.filter((v) => v !== option) : [...selected, option])
  }

  const summary =
    selected.length === 0 ? `All ${label}` : selected.length === 1 ? selected[0] : `${selected.length} selected`

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => (open ? close() : setOpen(true))}
        aria-haspopup="listbox"
        aria-expanded={open}
        className={`${triggerClass} flex items-center gap-2 hover:border-accent`}
      >
        <span className={selected.length === 0 ? 'text-ink-secondary' : 'text-ink-primary'}>{summary}</span>
        <span className="text-ink-muted">▾</span>
      </button>

      {open && (
        <div
          className={`absolute left-0 top-full z-20 mt-1 rounded-lg border border-border-hairline bg-surface-card shadow-lg ${
            searchable ? 'w-72' : 'w-56'
          }`}
        >
          {searchable && (
            <div className="border-b border-border-hairline p-2">
              <input
                type="search"
                autoFocus
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => e.key === 'Escape' && close()}
                placeholder={`Search ${label.toLowerCase()}…`}
                aria-label={`Search ${label}`}
                className="w-full rounded-md border border-border-hairline bg-surface-page px-2.5 py-1.5 text-sm text-ink-primary outline-none focus:border-accent"
              />
            </div>
          )}
          <div className="flex items-center justify-between border-b border-border-hairline px-3 py-2 text-xs text-ink-muted">
            <span>
              {label}
              {needle ? ` · ${shown.length} of ${options.length}` : ''}
            </span>
            <button
              type="button"
              onClick={toggleShown}
              disabled={shown.length === 0}
              className="font-medium text-accent hover:underline disabled:opacity-40"
            >
              {allShownSelected ? 'Clear' : 'Select all'}
            </button>
          </div>
          <div className="max-h-64 overflow-y-auto">
            {shown.length === 0 ? (
              <p className="px-3 py-3 text-sm text-ink-muted">{options.length === 0 ? 'No options.' : 'No matches.'}</p>
            ) : (
              shown.map((option) => (
                <label
                  key={option}
                  className="flex cursor-pointer items-center gap-2.5 px-3 py-2 text-sm hover:bg-ink-primary/5"
                >
                  <input
                    type="checkbox"
                    checked={selected.includes(option)}
                    onChange={() => toggle(option)}
                    className="h-4 w-4 accent-accent"
                  />
                  <span className="truncate text-ink-primary">{option}</span>
                </label>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  )
}
