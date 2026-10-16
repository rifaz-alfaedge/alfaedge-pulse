import { useState } from 'react'
import { Dialog } from '@rtcamp/frappe-ui-react'
import { useFrappePostCall } from 'frappe-react-sdk'
import type { LogSettings } from '../lib/types'
import { getErrorMessage } from '../lib/errors'

type SecretField = 'username' | 'password' | 'api_key'
type EditableValues = Omit<
  LogSettings,
  'has_username' | 'has_password' | 'has_api_key' | 'effective_url' | 'last_checked' | 'last_error' | 'can_edit'
>

const inputClass =
  'w-full rounded-lg border border-border-hairline bg-surface-card px-3 py-2 text-sm text-ink-primary placeholder:text-ink-muted focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/20'

const READ_ONLY_KEYS = ['has_username', 'has_password', 'has_api_key', 'effective_url', 'last_checked', 'last_error', 'can_edit'] as const

function pickEditable(s: LogSettings): EditableValues {
  const copy: Record<string, unknown> = { ...s }
  for (const key of READ_ONLY_KEYS) delete copy[key]
  return copy as EditableValues
}

/** Same settings as Desk's ClickHouse Log Settings form, editable from the
 * dashboard. Credentials are write-only: the backend never sends them back
 * (only has_<field> flags), a blank input keeps the stored value, and
 * "Remove" explicitly clears one. Saved values are encrypted server-side
 * (Frappe Password fields). */
export function LogSettingsDialog({
  settings,
  onClose,
  onSaved,
}: {
  settings: LogSettings
  onClose: () => void
  onSaved: () => void
}) {
  const [values, setValues] = useState<EditableValues>(() => pickEditable(settings))
  const [secrets, setSecrets] = useState<Record<SecretField, string>>({ username: '', password: '', api_key: '' })
  const [clear, setClear] = useState<Record<SecretField, boolean>>({ username: false, password: false, api_key: false })
  const [status, setStatus] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)
  const [busy, setBusy] = useState(false)

  const { call: save } = useFrappePostCall<{ message: LogSettings }>('alfaedge_pulse.log_monitor.api.save_log_settings')
  const { call: test } = useFrappePostCall<{ message: { ok: boolean; message?: string; error?: string } }>(
    'alfaedge_pulse.log_monitor.api.test_log_connection',
  )

  const set = <K extends keyof EditableValues>(key: K, value: EditableValues[K]) =>
    setValues((v) => ({ ...v, [key]: value }))

  const payload = () => {
    const out: Record<string, unknown> = { ...values }
    for (const field of ['username', 'password', 'api_key'] as SecretField[]) {
      if (clear[field]) out[`clear_${field}`] = 1
      else if (secrets[field]) out[field] = secrets[field]
    }
    return out
  }

  const onTest = async () => {
    setBusy(true)
    setStatus(null)
    try {
      const r = await test({ values: JSON.stringify(payload()) })
      setStatus(r.message.ok ? { tone: 'ok', text: r.message.message ?? 'Connected' } : { tone: 'error', text: r.message.error ?? 'Failed' })
    } catch (e) {
      setStatus({ tone: 'error', text: getErrorMessage(e) })
    } finally {
      setBusy(false)
    }
  }

  const onSave = async () => {
    setBusy(true)
    setStatus(null)
    try {
      await save({ values: JSON.stringify(payload()) })
      onSaved()
      onClose()
    } catch (e) {
      setStatus({ tone: 'error', text: getErrorMessage(e) })
      setBusy(false)
    }
  }

  const secretInput = (field: SecretField, label: string, has: boolean, type: 'text' | 'password' = 'password') => (
    <label className="flex flex-col gap-1">
      <span className="flex items-center justify-between text-xs font-medium text-ink-secondary">
        {label}
        {has && (
          <span className="flex items-center gap-2">
            <span className={clear[field] ? 'text-status-critical' : 'text-status-good'}>
              {clear[field] ? 'Will be removed' : 'Saved (encrypted)'}
            </span>
            <button
              type="button"
              className="text-accent hover:underline"
              onClick={() => setClear((c) => ({ ...c, [field]: !c[field] }))}
            >
              {clear[field] ? 'Undo' : 'Remove'}
            </button>
          </span>
        )}
      </span>
      <input
        type={type}
        autoComplete="new-password"
        className={inputClass}
        disabled={clear[field]}
        value={secrets[field]}
        placeholder={has ? 'Leave blank to keep the saved value' : 'Not set'}
        onChange={(e) => setSecrets((s) => ({ ...s, [field]: e.target.value }))}
      />
    </label>
  )

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()} options={{ title: 'ClickHouse Log Settings', size: '3xl' }}>
      <div className="flex max-h-[70vh] flex-col gap-6 overflow-y-auto py-2 pr-1">
        <Section title="Connection">
          <Check label="Enabled" checked={!!values.enabled} onChange={(v) => set('enabled', v ? 1 : 0)} />
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-[7rem_1fr_7rem]">
            <TextField label="Protocol">
              <select className={inputClass} value={values.protocol} onChange={(e) => set('protocol', e.target.value as 'http' | 'https')}>
                <option value="http">http</option>
                <option value="https">https</option>
              </select>
            </TextField>
            <TextField label="Host">
              <input className={inputClass} value={values.host ?? ''} placeholder="10.10.10.202 or chlogs.alfaedge.in" onChange={(e) => set('host', e.target.value)} />
            </TextField>
            <TextField label="Port">
              <input type="number" className={inputClass} value={values.port ?? ''} onChange={(e) => set('port', e.target.value === '' ? null : Number(e.target.value))} />
            </TextField>
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <TextField label="URL path (optional)">
              <input className={inputClass} value={values.url_path ?? ''} placeholder="/clickhouse" onChange={(e) => set('url_path', e.target.value)} />
            </TextField>
            <TextField label="Query timeout (seconds)">
              <input type="number" className={inputClass} value={values.query_timeout_seconds ?? 30} onChange={(e) => set('query_timeout_seconds', Number(e.target.value))} />
            </TextField>
          </div>
          {values.protocol === 'https' && (
            <Check label="Verify SSL certificate" checked={!!values.verify_ssl} onChange={(v) => set('verify_ssl', v ? 1 : 0)} />
          )}
        </Section>

        <Section title="Credentials" hint="Stored encrypted. Never shown again after saving.">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {secretInput('username', 'Username', settings.has_username, 'text')}
            {secretInput('password', 'Password', settings.has_password)}
          </div>
          {secretInput('api_key', 'API key / token (optional, for a reverse proxy)', settings.has_api_key)}
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <TextField label="API key header">
              <input className={inputClass} value={values.api_key_header ?? ''} onChange={(e) => set('api_key_header', e.target.value)} />
            </TextField>
            <TextField label="API key prefix">
              <input className={inputClass} value={values.api_key_prefix ?? ''} placeholder="Bearer " onChange={(e) => set('api_key_prefix', e.target.value)} />
            </TextField>
          </div>
        </Section>

        <Section title="Data source">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <TextField label="Database">
              <input className={inputClass} value={values.database_name} onChange={(e) => set('database_name', e.target.value)} />
            </TextField>
            <TextField label="Table">
              <input className={inputClass} value={values.table_name} onChange={(e) => set('table_name', e.target.value)} />
            </TextField>
          </div>
          <details className="rounded-lg border border-border-hairline px-3 py-2">
            <summary className="cursor-pointer text-xs font-medium text-ink-secondary">Column mapping</summary>
            <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-3">
              <TextField label="Timestamp">
                <input className={inputClass} value={values.ts_column} onChange={(e) => set('ts_column', e.target.value)} />
              </TextField>
              <TextField label="Site URL">
                <input className={inputClass} value={values.site_column} onChange={(e) => set('site_column', e.target.value)} />
              </TextField>
              <TextField label="Content">
                <input className={inputClass} value={values.content_column} onChange={(e) => set('content_column', e.target.value)} />
              </TextField>
              <TextField label="Title (optional)">
                <input className={inputClass} value={values.title_column ?? ''} onChange={(e) => set('title_column', e.target.value)} />
              </TextField>
              <TextField label="ID (optional)">
                <input className={inputClass} value={values.id_column ?? ''} onChange={(e) => set('id_column', e.target.value)} />
              </TextField>
              <TextField label="Log type (optional)">
                <input className={inputClass} value={values.log_type_column ?? ''} onChange={(e) => set('log_type_column', e.target.value)} />
              </TextField>
            </div>
          </details>
          <TextField label="Error keyword (case-insensitive)">
            <input className={inputClass} value={values.error_keyword} onChange={(e) => set('error_keyword', e.target.value)} />
          </TextField>
        </Section>

        <Section title="Recurring error alerts" hint="Sent through the existing Email/WhatsApp alert channels, batched every 2 minutes.">
          <Check
            label="Enable recurring error alerts"
            checked={!!values.enable_recurring_alerts}
            onChange={(v) => set('enable_recurring_alerts', v ? 1 : 0)}
          />
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <TextField label="Alert at ≥ occurrences">
              <input type="number" min={1} className={inputClass} value={values.occurrence_threshold} onChange={(e) => set('occurrence_threshold', Number(e.target.value))} />
            </TextField>
            <TextField label="Within (minutes)">
              <input type="number" min={1} className={inputClass} value={values.window_minutes} onChange={(e) => set('window_minutes', Number(e.target.value))} />
            </TextField>
            <TextField label="Check every (minutes)">
              <input type="number" min={1} className={inputClass} value={values.check_interval_minutes} onChange={(e) => set('check_interval_minutes', Number(e.target.value))} />
            </TextField>
          </div>
        </Section>

        {status && (
          <div
            className={`break-words rounded-lg px-3 py-2 text-sm ${
              status.tone === 'ok' ? 'bg-status-good/10 text-status-good' : 'bg-status-critical/10 text-status-critical'
            }`}
          >
            {status.text}
          </div>
        )}

        <div className="flex justify-end gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={onTest}
            className="rounded-lg border border-border-hairline px-4 py-2 text-sm font-medium text-ink-primary enabled:hover:border-accent disabled:opacity-50"
          >
            Test connection
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={onSave}
            className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white enabled:hover:opacity-90 disabled:opacity-50"
          >
            Save
          </button>
        </div>
      </div>
    </Dialog>
  )
}

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-3">
      <div>
        <h4 className="text-sm font-semibold text-ink-primary">{title}</h4>
        {hint && <p className="text-xs text-ink-muted">{hint}</p>}
      </div>
      {children}
    </section>
  )
}

function TextField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs font-medium text-ink-secondary">{label}</span>
      {children}
    </label>
  )
}

function Check({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-center gap-2 text-sm text-ink-primary">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} className="h-4 w-4 accent-[var(--color-accent)]" />
      {label}
    </label>
  )
}
