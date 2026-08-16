import type { ProxmoxGuest, ProxmoxServer } from './types'

/** Deep-link to a guest's console on Proxmox's OWN web UI, opened in a new
 * tab rather than embedded — see the embedded-relay attempt this replaced:
 * Proxmox's vncwebsocket endpoint requires a same-origin PVEAuthCookie
 * ticket handshake that couldn't be reliably reproduced from a standalone
 * relay process (consistent "invalid PVEVNC ticket" rejections even with
 * every documented requirement met — a long-standing, still-unresolved
 * pain point in Proxmox's own community). Proxmox's own JS already
 * implements this exact protocol correctly, so linking straight to it
 * sidesteps the problem entirely, at the cost of exposing the Proxmox
 * host's own URL and requiring a separate login there — the trade-off
 * chosen over continuing to chase the embedded approach.
 *
 * URL format confirmed against Proxmox's own console-link convention:
 * `?console=<kvm|lxc>&<novnc|xtermjs>=1&vmid=&vmname=&node=`. LXC has no
 * graphical console at all (xtermjs is its only option); QEMU defaults to
 * the standard graphical noVNC console.
 *
 * Returns null if the guest has no node recorded yet (a fresh guest that
 * hasn't been through a poll cycle) or its server is missing — there's
 * nothing sensible to link to yet. */
export function buildConsoleUrl(guest: ProxmoxGuest, server: ProxmoxServer | undefined): string | null {
  if (!server || !guest.node) return null

  const isQemu = guest.guest_type === 'QEMU (VM)'
  const params = new URLSearchParams({
    console: isQemu ? 'kvm' : 'lxc',
    vmid: String(guest.vmid),
    vmname: guest.guest_name,
    node: guest.node,
  })
  params.set(isQemu ? 'novnc' : 'xtermjs', '1')

  return `https://${server.hostname}:${server.port ?? 8006}/?${params.toString()}`
}
