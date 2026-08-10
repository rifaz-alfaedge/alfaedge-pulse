/** Extracts a human-readable message from whatever `frappe-react-sdk`
 * throws — a plain `{httpStatus, message, exception, _server_messages?, ...}`
 * object (see frappe-js-sdk's `getFrappeError`), not a real `Error`
 * instance. `e instanceof Error` is always false for it, so treating it
 * like one and falling back to `String(e)` renders "[object Object]".
 *
 * A `frappe.throw()` validation message lives in `_server_messages` (a
 * JSON-encoded array of JSON-encoded `{message: "..."}` strings) — the
 * top-level `message` field is usually just the SDK's generic fallback
 * text. An unexpected server exception has neither, only `exception`
 * (e.g. "AttributeError: ..."), which is still far more useful than the
 * generic fallback. */
export function getErrorMessage(e: unknown): string {
  if (e instanceof Error) return e.message
  if (e && typeof e === 'object') {
    const err = e as Record<string, unknown>
    if (typeof err._server_messages === 'string') {
      try {
        const messages = (JSON.parse(err._server_messages) as string[])
          .map((raw) => {
            try {
              return (JSON.parse(raw) as { message?: string }).message
            } catch {
              return undefined
            }
          })
          .filter((m): m is string => !!m)
        if (messages.length > 0) return messages.join(' ')
      } catch {
        // fall through to the other fields below
      }
    }
    if (typeof err.exception === 'string' && err.exception) return err.exception
    if (typeof err.message === 'string' && err.message) return err.message
  }
  return String(e)
}
