const STORAGE_KEY = 'alfaedge-pulse-theme'

/** `.dark` on `<html>` is what Tailwind's `dark:` variant and frappe-ui-react
 * both key off (see index.css's own comment on `.dark`), not the
 * `prefers-color-scheme` media query directly — this file is the one place
 * that decides whether it's set, so the manual toggle and the OS-preference
 * listener never fight each other. A manually chosen theme (persisted here)
 * always wins over the OS preference once set; until the user ever toggles
 * it, the OS preference drives it live, matching this app's original
 * system-only behavior. */
export function isDarkMode(): boolean {
  return document.documentElement.classList.contains('dark')
}

export function applySystemOrStoredTheme(): void {
  const stored = localStorage.getItem(STORAGE_KEY)
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
  document.documentElement.classList.toggle('dark', stored ? stored === 'dark' : prefersDark)
}

export function setDarkMode(isDark: boolean): void {
  document.documentElement.classList.toggle('dark', isDark)
  localStorage.setItem(STORAGE_KEY, isDark ? 'dark' : 'light')
}

/** Keeps following the OS live for anyone who's never manually toggled —
 * only a stored, explicit choice ever overrides it. */
export function watchSystemTheme(): void {
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (!localStorage.getItem(STORAGE_KEY)) applySystemOrStoredTheme()
  })
}
