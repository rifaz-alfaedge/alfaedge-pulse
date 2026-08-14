import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { FrappeProvider } from 'frappe-react-sdk'
import App from './App'
import { applySystemOrStoredTheme, watchSystemTheme } from './lib/theme'
import './index.css'

declare global {
  interface Window {
    csrf_token?: string
    site_name?: string
  }
}

// Tailwind's `dark:` variant (and frappe-ui-react's own dark-mode CSS —
// see theme.css's `@custom-variant dark (&:is(.dark *))`) both key off a
// `.dark` class on an ancestor element, not the `prefers-color-scheme`
// media query directly. Without this, none of those classes ever
// activate, no matter the OS theme — so we mirror it onto `<html>`
// ourselves (see lib/theme.ts), once at startup and on every OS change,
// unless the header's ThemeToggle has stored an explicit manual choice.
applySystemOrStoredTheme()
watchSystemTheme()

// In production this is served from the same origin as the Frappe site
// (via the /alfaedge-pulse www route), so no explicit url is needed — the SDK
// defaults to window.location.origin. In dev (`npm run dev`), Vite's proxy
// (see vite.config.ts) forwards /api and /app calls to the real bench, so
// this stays unset there too; VITE_BASE_URL only matters to the proxy.
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <FrappeProvider
      socketPort={import.meta.env.VITE_SOCKET_PORT}
      siteName={window.site_name}
      enableSocket={false}
    >
      <App />
    </FrappeProvider>
  </StrictMode>,
)
