import { useState } from 'react'
import { useFrappePostCall } from 'frappe-react-sdk'
import { getErrorMessage } from '../lib/errors'

/** Per-error actions shared by the Logs tab's tables and log dialog:
 * Create Task (opens Desk's New Task pre-filled from the error) and the
 * manager-only Ignore checkbox (hides the error everywhere and stops its
 * alerts — see log_monitor/api.py's set_error_ignored). */

export function CreateTaskButton({ signature, compact }: { signature: string; compact?: boolean }) {
  const { call } = useFrappePostCall<{ message: { url: string } }>('alfaedge_pulse.log_monitor.api.create_task_url')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const open = async (e: React.MouseEvent) => {
    e.stopPropagation()
    setError(null)
    setBusy(true)
    // Opened synchronously, inside the click, so popup blockers allow it;
    // pointed at the Desk form once the server has built its URL.
    const win = window.open('about:blank', '_blank')
    try {
      const { url } = (await call({ signature })).message
      if (win) {
        win.opener = null
        win.location.href = url
      } else {
        window.location.href = url
      }
    } catch (err) {
      win?.close()
      setError(getErrorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <span className="inline-flex flex-col items-end">
      <button
        type="button"
        onClick={open}
        disabled={busy}
        title="Open a new Task in Desk, pre-filled from this error"
        className={`rounded-lg border border-border-hairline font-medium text-ink-primary hover:border-accent hover:text-accent disabled:opacity-40 ${
          compact ? 'px-2 py-0.5 text-[11px]' : 'px-3 py-1 text-xs'
        }`}
      >
        {busy ? 'Opening…' : 'Create task'}
      </button>
      {error && <span className="mt-1 max-w-[14rem] text-[11px] text-status-critical">{error}</span>}
    </span>
  )
}

export function IgnoreCheckbox({
  signature,
  ignored,
  canIgnore,
  onChanged,
}: {
  signature: string
  ignored: boolean
  canIgnore: boolean
  /** Called with the new state after the server accepted it. */
  onChanged: (ignored: boolean) => void
}) {
  const { call } = useFrappePostCall<{ message: { ignored: boolean } }>('alfaedge_pulse.log_monitor.api.set_error_ignored')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const toggle = async (next: boolean) => {
    setError(null)
    setBusy(true)
    try {
      await call({ signature, ignored: next ? 1 : 0 })
      onChanged(next)
    } catch (err) {
      setError(getErrorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <span className="inline-flex flex-col items-start" onClick={(e) => e.stopPropagation()}>
      <label
        className={`inline-flex items-center gap-1.5 text-xs ${canIgnore ? 'cursor-pointer text-ink-secondary' : 'cursor-not-allowed text-ink-muted'}`}
        title={
          canIgnore
            ? 'Hide this error everywhere (Logs, task board) and stop its alerts'
            : 'Only a Proxmox Monitor Manager or System Manager can ignore errors'
        }
      >
        <input type="checkbox" checked={ignored} disabled={!canIgnore || busy} onChange={(e) => toggle(e.target.checked)} />
        Ignore
      </label>
      {error && <span className="mt-1 max-w-[14rem] text-[11px] text-status-critical">{error}</span>}
    </span>
  )
}
