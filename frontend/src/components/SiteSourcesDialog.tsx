import { useState } from 'react'
import { Dialog } from '@rtcamp/frappe-ui-react'
import { useFrappeGetCall, useFrappePostCall } from 'frappe-react-sdk'
import type { CloudflareInfo, NPMInstanceInfo, ProxmoxServer, SiteSources } from '../lib/types'
import { getErrorMessage } from '../lib/errors'
import { timeAgo } from '../lib/format'

const API = 'alfaedge_pulse.site_discovery.api'
const inputClass =
  'w-full rounded-lg border border-border-hairline bg-surface-card px-3 py-2 text-sm text-ink-primary placeholder:text-ink-muted focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/20'
const btn =
  'rounded-lg border border-border-hairline px-3 py-1.5 text-xs font-medium text-ink-primary enabled:hover:border-accent disabled:opacity-50'
const btnPrimary = 'rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white enabled:hover:opacity-90 disabled:opacity-50'

type Status = { tone: 'ok' | 'error'; text: string } | null
type TestResult = { ok: boolean; message?: string; error?: string }

/** Where site URLs come from: one Cloudflare token and one Nginx Proxy
 * Manager per Proxmox host. Proxmox Monitor Manager only. Secrets are
 * write-only — the server never sends them back (only has_<field> flags);
 * a blank input keeps the stored value. */
export function SiteSourcesDialog({
  servers,
  onClose,
  onChanged,
}: {
  servers: ProxmoxServer[]
  onClose: () => void
  onChanged: () => void
}) {
  const { data, mutate, error } = useFrappeGetCall<{ message: SiteSources }>(`${API}.get_site_sources`)
  const sources = data?.message
  const [editing, setEditing] = useState<NPMInstanceInfo | 'new' | null>(null)
  const [status, setStatus] = useState<Status>(null)
  const { call: syncNow, loading: syncing } = useFrappePostCall(`${API}.sync_now`)

  const refresh = () => {
    mutate()
    onChanged()
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()} options={{ title: 'Site sources', size: '4xl' }}>
      <div className="flex max-h-[72vh] flex-col gap-6 overflow-y-auto py-2 pr-1">
        <p className="text-sm text-ink-secondary">
          Site URLs are discovered once a day from each host's Nginx Proxy Manager, Cloudflare DNS, and the Pulse agent.
          When a domain is in more than one NPM, Cloudflare's target decides which one serves it.
        </p>
        {error && <p className="text-sm text-status-critical">{getErrorMessage(error)}</p>}
        {!sources ? (
          <p className="text-sm text-ink-muted">Loading…</p>
        ) : (
          <>
            <CloudflareSection info={sources.cloudflare} onSaved={refresh} />

            <section className="flex flex-col gap-3">
              <div className="flex items-center justify-between">
                <h4 className="text-sm font-semibold text-ink-primary">Nginx Proxy Managers</h4>
                {editing === null && (
                  <button type="button" className={btn} onClick={() => setEditing('new')}>
                    Add NPM
                  </button>
                )}
              </div>
              {editing !== null && (
                <NPMForm
                  key={editing === 'new' ? 'new' : editing.name}
                  initial={editing === 'new' ? null : editing}
                  servers={servers}
                  onCancel={() => setEditing(null)}
                  onSaved={() => {
                    setEditing(null)
                    refresh()
                  }}
                />
              )}
              {sources.npms.length === 0 && editing === null && (
                <p className="text-sm text-ink-muted">No NPM added yet.</p>
              )}
              {sources.npms.map((n) => (
                <NPMRow key={n.name} npm={n} onEdit={() => setEditing(n)} onDeleted={refresh} />
              ))}
            </section>

            <section className="flex flex-col gap-2">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h4 className="text-sm font-semibold text-ink-primary">Sync</h4>
                <button
                  type="button"
                  className={btnPrimary}
                  disabled={syncing}
                  onClick={async () => {
                    setStatus(null)
                    try {
                      await syncNow({})
                      setStatus({ tone: 'ok', text: 'Sync started — results appear in a minute or two. Reopen this to see them.' })
                    } catch (e) {
                      setStatus({ tone: 'error', text: getErrorMessage(e) })
                    }
                  }}
                >
                  Sync now
                </button>
              </div>
              <p className="text-xs text-ink-muted">
                Last sync {timeAgo(sources.cloudflare.last_synced)} · {sources.cloudflare.site_count ?? 0} sites found
              </p>
              {status && <StatusLine status={status} />}
            </section>

            {sources.problems.length > 0 && (
              <section className="flex flex-col gap-2">
                <h4 className="text-sm font-semibold text-ink-primary">Needs attention ({sources.problems.length})</h4>
                <div className="overflow-x-auto rounded-lg border border-border-hairline">
                  <table className="w-full min-w-[640px] text-left text-sm">
                    <tbody>
                      {sources.problems.map((p) => (
                        <tr key={p.name} className="border-b border-border-hairline align-top last:border-0">
                          <td className="whitespace-nowrap px-3 py-2 font-medium text-ink-primary">{p.domain}</td>
                          <td className="whitespace-nowrap px-3 py-2">
                            <span className="rounded-full bg-status-warning/15 px-2 py-0.5 text-[11px] font-medium text-status-warning">
                              {p.status}
                            </span>
                          </td>
                          <td className="px-3 py-2 text-xs text-ink-secondary">{p.note}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            )}
          </>
        )}
      </div>
    </Dialog>
  )
}

function StatusLine({ status }: { status: NonNullable<Status> }) {
  return (
    <div
      className={`break-words rounded-lg px-3 py-2 text-sm ${
        status.tone === 'ok' ? 'bg-status-good/10 text-status-good' : 'bg-status-critical/10 text-status-critical'
      }`}
    >
      {status.text}
    </div>
  )
}

function Label({ text, children }: { text: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs font-medium text-ink-secondary">{text}</span>
      {children}
    </label>
  )
}

function SecretInput({
  label,
  has,
  value,
  onChange,
  type = 'password',
}: {
  label: string
  has: boolean
  value: string
  onChange: (v: string) => void
  type?: 'text' | 'password'
}) {
  return (
    <Label text={`${label}${has ? ' — saved (encrypted)' : ''}`}>
      <input
        type={type}
        autoComplete="new-password"
        className={inputClass}
        value={value}
        placeholder={has ? 'Leave blank to keep the saved value' : 'Not set'}
        onChange={(e) => onChange(e.target.value)}
      />
    </Label>
  )
}

function CloudflareSection({ info, onSaved }: { info: CloudflareInfo; onSaved: () => void }) {
  const [enabled, setEnabled] = useState(!!info.enabled)
  const [zones, setZones] = useState(info.zones ?? '')
  const [syncHour, setSyncHour] = useState(info.sync_hour ?? 2)
  const [token, setToken] = useState('')
  const [status, setStatus] = useState<Status>(null)
  const { call: save, loading: saving } = useFrappePostCall(`${API}.save_cloudflare_settings`)
  const { call: test, loading: testing } = useFrappePostCall<{ message: TestResult }>(`${API}.test_cloudflare`)
  const values = () => JSON.stringify({ enabled: enabled ? 1 : 0, zones, sync_hour: syncHour, ...(token ? { api_token: token } : {}) })

  return (
    <section className="flex flex-col gap-3">
      <h4 className="text-sm font-semibold text-ink-primary">Cloudflare</h4>
      <label className="flex items-center gap-2 text-sm text-ink-primary">
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} className="h-4 w-4" />
        Enabled
      </label>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-[2fr_2fr_1fr]">
        <SecretInput label="API token (Zone:Read + DNS:Read)" has={info.has_api_token} value={token} onChange={setToken} />
        <Label text="Only these zones (optional)">
          <input className={inputClass} value={zones} placeholder="alfaedge.org, alfacrm.org" onChange={(e) => setZones(e.target.value)} />
        </Label>
        <Label text="Daily sync hour">
          <input type="number" min={0} max={23} className={inputClass} value={syncHour} onChange={(e) => setSyncHour(Number(e.target.value))} />
        </Label>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          className={btn}
          disabled={testing}
          onClick={async () => {
            setStatus(null)
            try {
              const r = await test({ values: JSON.stringify({ api_token: token, zones }) })
              setStatus(r.message.ok ? { tone: 'ok', text: r.message.message ?? 'OK' } : { tone: 'error', text: r.message.error ?? 'Failed' })
            } catch (e) {
              setStatus({ tone: 'error', text: getErrorMessage(e) })
            }
          }}
        >
          Test
        </button>
        <button
          type="button"
          className={btnPrimary}
          disabled={saving}
          onClick={async () => {
            setStatus(null)
            try {
              await save({ values: values() })
              setToken('')
              setStatus({ tone: 'ok', text: 'Saved' })
              onSaved()
            } catch (e) {
              setStatus({ tone: 'error', text: getErrorMessage(e) })
            }
          }}
        >
          Save
        </button>
        {info.last_synced && (
          <span className="text-xs text-ink-muted">
            {info.zone_count ?? 0} zones · {info.record_count ?? 0} records
          </span>
        )}
      </div>
      {info.last_error && <p className="break-words text-xs text-status-critical">Last sync: {info.last_error}</p>}
      {status && <StatusLine status={status} />}
    </section>
  )
}

function NPMRow({ npm, onEdit, onDeleted }: { npm: NPMInstanceInfo; onEdit: () => void; onDeleted: () => void }) {
  const [confirming, setConfirming] = useState(false)
  const [status, setStatus] = useState<Status>(null)
  const { call: del } = useFrappePostCall(`${API}.delete_npm_instance`)
  const { call: test, loading: testing } = useFrappePostCall<{ message: TestResult }>(`${API}.test_npm`)
  return (
    <div className="rounded-lg border border-border-hairline px-3 py-2.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="font-medium text-ink-primary">
            {npm.instance_name}
            {!npm.enabled && <span className="ml-2 text-xs text-ink-muted">(disabled)</span>}
          </div>
          <div className="text-xs text-ink-muted">
            {npm.proxmox_server} · <span className="font-mono">{npm.effective_url || 'no URL'}</span>
            {npm.public_ips ? ` · public IP ${npm.public_ips}` : ' · public IP not set'}
          </div>
          <div className="text-xs text-ink-muted">
            {npm.last_synced ? `${npm.proxy_host_count ?? 0} proxy hosts · synced ${timeAgo(npm.last_synced)}` : 'Not synced yet'}
            {!npm.has_email || !npm.has_password ? ' · credentials missing' : ''}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            className={btn}
            disabled={testing}
            onClick={async () => {
              setStatus(null)
              try {
                const r = await test({ values: '{}', name: npm.name })
                setStatus(r.message.ok ? { tone: 'ok', text: r.message.message ?? 'OK' } : { tone: 'error', text: r.message.error ?? 'Failed' })
              } catch (e) {
                setStatus({ tone: 'error', text: getErrorMessage(e) })
              }
            }}
          >
            Test
          </button>
          <button type="button" className={btn} onClick={onEdit}>
            Edit
          </button>
          <button
            type="button"
            className={`${btn} ${confirming ? 'border-status-critical text-status-critical' : ''}`}
            onClick={async () => {
              if (!confirming) {
                setConfirming(true)
                setTimeout(() => setConfirming(false), 4000)
                return
              }
              try {
                await del({ name: npm.name })
                onDeleted()
              } catch (e) {
                setStatus({ tone: 'error', text: getErrorMessage(e) })
              }
            }}
          >
            {confirming ? 'Confirm delete' : 'Delete'}
          </button>
        </div>
      </div>
      {npm.last_error && <p className="mt-1 break-words text-xs text-status-critical">Last sync: {npm.last_error}</p>}
      {status && (
        <div className="mt-2">
          <StatusLine status={status} />
        </div>
      )}
    </div>
  )
}

function NPMForm({
  initial,
  servers,
  onCancel,
  onSaved,
}: {
  initial: NPMInstanceInfo | null
  servers: ProxmoxServer[]
  onCancel: () => void
  onSaved: () => void
}) {
  const pve = servers.filter((s) => s.server_type === 'PVE')
  const [v, setV] = useState({
    instance_name: initial?.instance_name ?? '',
    proxmox_server: initial?.proxmox_server ?? pve[0]?.name ?? '',
    enabled: initial ? !!initial.enabled : true,
    url: initial?.url ?? '',
    protocol: initial?.protocol ?? 'http',
    port: initial?.port ?? 81,
    public_ips: initial?.public_ips ?? '',
    verify_ssl: initial ? !!initial.verify_ssl : true,
  })
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [status, setStatus] = useState<Status>(null)
  const { call: save, loading: saving } = useFrappePostCall(`${API}.save_npm_instance`)
  const { call: test, loading: testing } = useFrappePostCall<{ message: TestResult }>(`${API}.test_npm`)
  const set = <K extends keyof typeof v>(k: K, val: (typeof v)[K]) => setV((cur) => ({ ...cur, [k]: val }))
  const hostname = servers.find((s) => s.name === v.proxmox_server)?.hostname
  const defaultUrl = hostname ? `${v.protocol}://${hostname}:${v.port || 81}` : 'http://<host>:81'

  const payload = () =>
    JSON.stringify({
      ...v,
      enabled: v.enabled ? 1 : 0,
      verify_ssl: v.verify_ssl ? 1 : 0,
      ...(email ? { email } : {}),
      ...(password ? { password } : {}),
    })

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-accent/40 bg-surface-page p-3">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Label text="Name">
          <input
            className={inputClass}
            value={v.instance_name}
            disabled={!!initial}
            placeholder="Devalayam NPM"
            onChange={(e) => set('instance_name', e.target.value)}
          />
        </Label>
        <Label text="Proxmox host">
          <select className={inputClass} value={v.proxmox_server} onChange={(e) => set('proxmox_server', e.target.value)}>
            {pve.map((s) => (
              <option key={s.name} value={s.name}>
                {s.server_name}
              </option>
            ))}
          </select>
        </Label>
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-[2fr_1fr_1fr]">
        <Label text="URL (optional)">
          <input className={inputClass} value={v.url} placeholder={defaultUrl} onChange={(e) => set('url', e.target.value)} />
        </Label>
        <Label text="Protocol (if no URL)">
          <select className={inputClass} value={v.protocol} disabled={!!v.url} onChange={(e) => set('protocol', e.target.value as 'http' | 'https')}>
            <option value="http">http</option>
            <option value="https">https</option>
          </select>
        </Label>
        <Label text="Port (if no URL)">
          <input type="number" className={inputClass} value={v.port} disabled={!!v.url} onChange={(e) => set('port', Number(e.target.value))} />
        </Label>
      </div>
      <p className="-mt-1 text-xs text-ink-muted">
        Connects to <span className="font-mono">{v.url || defaultUrl}</span>
      </p>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <SecretInput label="Admin email" has={!!initial?.has_email} value={email} onChange={setEmail} type="text" />
        <SecretInput label="Admin password" has={!!initial?.has_password} value={password} onChange={setPassword} />
      </div>
      <Label text="Public IP(s) this NPM receives traffic on (comma-separated)">
        <input className={inputClass} value={v.public_ips} placeholder="203.0.113.10" onChange={(e) => set('public_ips', e.target.value)} />
      </Label>
      <div className="flex flex-wrap gap-4 text-sm text-ink-primary">
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={v.enabled} onChange={(e) => set('enabled', e.target.checked)} className="h-4 w-4" />
          Enabled
        </label>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={v.verify_ssl} onChange={(e) => set('verify_ssl', e.target.checked)} className="h-4 w-4" />
          Verify SSL certificate
        </label>
      </div>
      {status && <StatusLine status={status} />}
      <div className="flex justify-end gap-2">
        <button type="button" className={btn} onClick={onCancel}>
          Cancel
        </button>
        <button
          type="button"
          className={btn}
          disabled={testing}
          onClick={async () => {
            setStatus(null)
            try {
              const r = await test({ values: payload(), ...(initial ? { name: initial.name } : {}) })
              setStatus(r.message.ok ? { tone: 'ok', text: r.message.message ?? 'OK' } : { tone: 'error', text: r.message.error ?? 'Failed' })
            } catch (e) {
              setStatus({ tone: 'error', text: getErrorMessage(e) })
            }
          }}
        >
          Test
        </button>
        <button
          type="button"
          className={btnPrimary}
          disabled={saving || !v.instance_name.trim() || !v.proxmox_server}
          onClick={async () => {
            setStatus(null)
            try {
              await save({ values: payload(), ...(initial ? { name: initial.name } : {}) })
              onSaved()
            } catch (e) {
              setStatus({ tone: 'error', text: getErrorMessage(e) })
            }
          }}
        >
          Save
        </button>
      </div>
    </div>
  )
}
