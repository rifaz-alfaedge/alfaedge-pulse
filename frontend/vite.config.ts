import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Built assets are served by the alfaedge_pulse Frappe app from
// `/assets/alfaedge_pulse/alfaedge-pulse/...` (see www/alfaedge-pulse/index.py),
// so `base` must match that path. Filenames are content-hashed (Vite's
// default) — nginx caches everything under /assets for a full year
// (max-age=31536000, no revalidation), so a fixed filename would mean
// every deploy silently keeps serving old cached JS/CSS to anyone who
// already loaded the page. `build.manifest` records the current hashed
// filenames so www/alfaedge-pulse/index.py can look them up per-request and
// always reference whatever was actually built last.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  return {
    plugins: [react(), tailwindcss()],
    base: '/assets/alfaedge_pulse/alfaedge-pulse/',
    server: {
      proxy: {
        // In dev, proxy API/socket calls to the local Frappe site so the
        // Vite dev server (5173) can talk to the bench (8000) without CORS
        // headaches — same pattern Frappe Helpdesk/CRM use for `yarn dev`.
        '^/(api|app|assets|files)': {
          target: env.VITE_BASE_URL || 'http://alfaedge.localhost:8000',
          changeOrigin: true,
        },
      },
    },
    build: {
      outDir: '../alfaedge_pulse/public/alfaedge-pulse',
      emptyOutDir: true,
      manifest: true,
    },
  }
})
