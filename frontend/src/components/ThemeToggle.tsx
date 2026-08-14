import { useState } from 'react'
import { isDarkMode, setDarkMode } from '../lib/theme'

/** Manual override for the OS-driven theme main.tsx sets up by default —
 * click once and this dashboard stops following the OS preference,
 * remembering the explicit choice (see lib/theme.ts) across reloads. */
export function ThemeToggle() {
  const [dark, setDark] = useState(isDarkMode())

  const toggle = () => {
    const next = !dark
    setDarkMode(next)
    setDark(next)
  }

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={dark ? 'Switch to light mode' : 'Switch to dark mode'}
      title={dark ? 'Switch to light mode' : 'Switch to dark mode'}
      className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-border-hairline bg-surface-card text-ink-secondary transition-colors hover:text-ink-primary"
    >
      <span aria-hidden>{dark ? '☀️' : '🌙'}</span>
    </button>
  )
}
