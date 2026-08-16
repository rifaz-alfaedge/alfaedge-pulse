/** @novnc/novnc ships as a plain ES module with no first-party TypeScript
 * types (confirmed: no `types`/`typings` field in its package.json, and
 * `@novnc/novnc`'s `exports` field points straight at `core/rfb.js`). This
 * covers only the surface ConsoleDialog.tsx actually uses — see
 * node_modules/@novnc/novnc/docs/API.md for the full API if more is
 * needed later. */
declare module '@novnc/novnc' {
  export default class RFB extends EventTarget {
    constructor(
      target: HTMLElement,
      urlOrChannel: string,
      options?: {
        shared?: boolean
        credentials?: { username?: string; password?: string; target?: string }
      },
    )
    disconnect(): void
    scaleViewport: boolean
    resizeSession: boolean
  }
}
