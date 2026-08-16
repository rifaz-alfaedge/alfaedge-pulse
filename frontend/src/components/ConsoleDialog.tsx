import { useEffect, useRef, useState } from 'react'
import { Dialog } from '@rtcamp/frappe-ui-react'
import { useFrappePostCall } from 'frappe-react-sdk'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import RFB from '@novnc/novnc'
import '@xterm/xterm/css/xterm.css'
import type { ProxmoxGuest } from '../lib/types'
import { getErrorMessage } from '../lib/errors'

interface OpenConsoleResponse {
  session_id: string
  ws_path: string
  protocol: 'vnc' | 'terminal'
}

type ConnectionState = 'connecting' | 'open' | 'closed' | 'error'

/** Embedded VNC (QEMU) / terminal (LXC) console — the relay this connects
 * to (see backend's console_relay/relay.py) has already done all the real
 * Proxmox authentication server-side by the time the browser ever opens a
 * socket, so this component only ever holds an opaque, single-use
 * `session_id` baked into `ws_path`, never real credentials. */
export function ConsoleDialog({ guest, onClose }: { guest: ProxmoxGuest; onClose: () => void }) {
  const [state, setState] = useState<ConnectionState>('connecting')
  const [error, setError] = useState<string | null>(null)
  const containerRef = useRef<HTMLDivElement | null>(null)

  // useFrappePostCall's `call` actually resolves to the raw `{ message: T }`
  // envelope, not `T` directly — see HostHealthPanel.tsx's own note on this
  // same gotcha.
  const { call: openConsole } = useFrappePostCall<{ message: OpenConsoleResponse }>(
    'proxmox_monitor.console_relay.api.open_console',
  )

  useEffect(() => {
    let cancelled = false
    let cleanup: (() => void) | undefined

    ;(async () => {
      try {
        const { message } = await openConsole({ guest_name: guest.name })
        if (cancelled || !containerRef.current) return

        const wsProtocol = location.protocol === 'https:' ? 'wss' : 'ws'
        const wsUrl = `${wsProtocol}://${location.host}${message.ws_path}`

        cleanup =
          message.protocol === 'vnc'
            ? mountVncSession(containerRef.current, wsUrl, setState, setError)
            : mountTerminalSession(containerRef.current, wsUrl, setState, setError)
      } catch (e) {
        if (!cancelled) {
          setState('error')
          setError(getErrorMessage(e))
        }
      }
    })()

    return () => {
      cancelled = true
      cleanup?.()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [guest.name])

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()} options={{ title: `Console — ${guest.guest_name}`, size: '3xl' }}>
      <div className="flex flex-col gap-3 py-2">
        {state === 'connecting' && <p className="text-sm text-ink-muted">Connecting…</p>}
        {state === 'error' && <p className="text-sm text-status-critical">{error}</p>}
        {state === 'closed' && <p className="text-sm text-ink-muted">Console session closed.</p>}
        <div ref={containerRef} className="h-[520px] w-full overflow-hidden rounded-lg border border-border-hairline bg-black" />
      </div>
    </Dialog>
  )
}

function mountVncSession(
  container: HTMLElement,
  wsUrl: string,
  setState: (s: ConnectionState) => void,
  setError: (e: string) => void,
): () => void {
  const rfb = new RFB(container, wsUrl)
  rfb.scaleViewport = true
  rfb.resizeSession = true

  const onConnect = () => setState('open')
  const onDisconnect = (e: Event) => {
    const detail = (e as CustomEvent<{ clean?: boolean }>).detail
    if (detail && detail.clean === false) {
      setError('Console connection was lost unexpectedly.')
      setState('error')
    } else {
      setState('closed')
    }
  }

  rfb.addEventListener('connect', onConnect)
  rfb.addEventListener('disconnect', onDisconnect)

  return () => {
    rfb.removeEventListener('connect', onConnect)
    rfb.removeEventListener('disconnect', onDisconnect)
    rfb.disconnect()
  }
}

function mountTerminalSession(
  container: HTMLElement,
  wsUrl: string,
  setState: (s: ConnectionState) => void,
  setError: (e: string) => void,
): () => void {
  const term = new Terminal({ convertEol: true, cursorBlink: true })
  const fitAddon = new FitAddon()
  term.loadAddon(fitAddon)
  term.open(container)
  fitAddon.fit()

  const resizeObserver = new ResizeObserver(() => fitAddon.fit())
  resizeObserver.observe(container)

  const socket = new WebSocket(wsUrl)
  socket.binaryType = 'arraybuffer'
  const decoder = new TextDecoder()

  socket.onopen = () => setState('open')
  socket.onmessage = (event) => {
    const text = typeof event.data === 'string' ? event.data : decoder.decode(event.data as ArrayBuffer)
    term.write(text)
  }
  socket.onerror = () => {
    setError('Console connection failed.')
    setState('error')
  }
  socket.onclose = (event) => {
    if (!event.wasClean) {
      setError('Console connection was lost unexpectedly.')
      setState('error')
    } else {
      setState('closed')
    }
  }

  const dataListener = term.onData((data) => {
    if (socket.readyState === WebSocket.OPEN) socket.send(data)
  })

  return () => {
    resizeObserver.disconnect()
    dataListener.dispose()
    term.dispose()
    socket.close()
  }
}
