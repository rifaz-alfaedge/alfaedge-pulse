# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Session handoff between the dashboard, this Frappe site, and the
standalone console-relay process (``console_relay/relay.py``).

Two endpoints, two very different callers:

- ``open_console`` is reached by the dashboard's own browser session (CSRF-
  protected, normal Desk auth) — it does all the privileged work (decrypts
  the console token, calls Proxmox's vncproxy/termproxy, mints a session)
  and hands back only an opaque, short-lived ``session_id``. The browser
  never sees real Proxmox credentials.
- ``resolve_session`` is reached by the relay process over plain loopback
  HTTP (never the public network, never the browser) — it has no Frappe
  session, so a static shared secret (``console_relay_secret`` in
  site_config.json) stands in for auth instead. It resolves the
  ``session_id`` exactly once (the cache entry is deleted on read) and
  hands the relay everything it needs to open the real Proxmox connection.

See this app's console-feature plan for the full sequence diagram — the
short version: Browser -> open_console -> Frappe -> Browser -> Relay ->
resolve_session -> Frappe (loopback) -> Relay -> Proxmox.
"""

from __future__ import annotations

import hmac

import frappe
from frappe.utils import cint, now_datetime
from frappe.utils.password import get_decrypted_password

from alfaedge_pulse.proxmox_client.pve import PVEClient, login as pve_login

#: How long a minted session_id is redeemable for. Long enough for the
#: browser to receive open_console's response and immediately open the WS
#: to the relay; short enough that a leaked ws_path (e.g. browser devtools/
#: history) is useless within a minute. Single-use regardless — see
#: resolve_session, which deletes the cache entry on first read.
SESSION_TTL_SECONDS = 45

CONSOLE_MANAGER_ROLES = {"System Manager", "Proxmox Monitor Manager"}


def _cache_key(session_id: str) -> str:
	return f"proxmox_console_session:{session_id}"


@frappe.whitelist()
def open_console(guest_name: str) -> dict:
	"""Resolve a guest, open a Proxmox console proxy for it, and mint a
	short-lived opaque session the browser can hand to the relay.

	``guest_name`` is the *only* input — deliberately not a node/vmid/type
	triple — so there is no way for this endpoint to ever address a bare
	Proxmox host, only a real, already-synced ``Proxmox Guest`` row.
	"""
	user = frappe.session.user
	if not (user == "Administrator" or set(frappe.get_roles(user)) & CONSOLE_MANAGER_ROLES):
		frappe.throw(
			"Console access requires the Proxmox Monitor Manager role.", frappe.PermissionError
		)

	guest = frappe.get_doc("Proxmox Guest", guest_name)
	if not guest.node:
		frappe.throw(
			f"{guest.guest_name}: no Proxmox node recorded yet for this guest — wait for the next poll cycle and try again."
		)

	server = frappe.get_doc("Proxmox Server", guest.server)
	if server.server_type != "PVE":
		# Unreachable today (Proxmox Guest rows only ever come from PVE
		# hosts — PBS has no VMs/CTs), kept as explicit documentation that
		# console access is a PVE-guest-only concept, not enforced by luck.
		frappe.throw("Console access is only available for guests on a PVE server.")
	console_password = get_decrypted_password(
		"Proxmox Server", server.name, "console_password", raise_exception=False
	)
	if not server.console_username or not console_password:
		frappe.throw(
			f"Console access is not configured for {server.server_name} — "
			"set Console Username/Password on its Proxmox Server record."
		)

	verify_ssl = cint(server.verify_ssl)
	try:
		auth = pve_login(server.hostname, server.port, server.console_username, console_password, verify_ssl)
	except Exception:
		frappe.log_error(
			title=f"Console Relay: login failed for {server.name}", message=frappe.get_traceback()
		)
		frappe.throw(f"Could not log in to {server.server_name} — check the Console Username/Password.")

	client = PVEClient.from_ticket(server.hostname, server.port, auth["ticket"], auth["csrf_token"], verify_ssl)

	is_qemu = guest.guest_type == "QEMU (VM)"
	try:
		proxy = client.open_vnc_proxy(guest.node, guest.vmid) if is_qemu else client.open_term_proxy(guest.node, guest.vmid)
	except Exception:
		frappe.log_error(
			title=f"Console Relay: failed to open proxy for {guest.name}", message=frappe.get_traceback()
		)
		frappe.throw(
			f"Could not open a console proxy for {guest.guest_name} — check that the Console user has "
			"VM.Console permission on this guest in Proxmox."
		)

	session_id = frappe.generate_hash(length=32)
	bundle = {
		"hostname": server.hostname,
		"port": cint(server.port),
		"verify_ssl": bool(verify_ssl),
		"node": guest.node,
		"vnc_port": proxy.get("port"),
		"vnc_ticket": proxy.get("ticket"),
		# The vncwebsocket endpoint only accepts PVEAuthCookie auth, not the
		# API-token header used everywhere else in this app — confirmed
		# live (a token-authed connection gets a bare HTTP 401) and
		# documented in the Proxmox community. This is the same ticket
		# pve_login() just obtained, handed to the relay to set as a
		# Cookie header on its own connection to Proxmox.
		"pve_auth_ticket": auth["ticket"],
		"protocol": "vnc" if is_qemu else "terminal",
		# Only used for the terminal (LXC termproxy) case — see relay.py's
		# handle_connection. Once connected, termproxy sessions additionally
		# require the client to send "{username}:{vnc_ticket}\n" as the
		# very first WS message before any real shell data flows; confirmed
		# via the Proxmox community (this isn't documented in the API
		# schema itself). QEMU/VNC sessions don't need this — the vnc_ticket
		# already doubles as the RFB security handshake's password.
		"console_username": server.console_username,
	}
	frappe.cache().set_value(_cache_key(session_id), frappe.as_json(bundle), expires_in_sec=SESSION_TTL_SECONDS)

	frappe.get_doc(
		{
			"doctype": "Proxmox Console Session Log",
			"user": user,
			"guest": guest.name,
			"server": server.name,
			"protocol": "vnc" if is_qemu else "terminal",
			"opened_at": now_datetime(),
		}
	).insert(ignore_permissions=True)

	return {
		"session_id": session_id,
		"ws_path": f"/console-ws/{session_id}",
		"protocol": "vnc" if is_qemu else "terminal",
	}


@frappe.whitelist(allow_guest=True)
def resolve_session(session_id: str, shared_secret: str) -> dict | None:
	"""Called by the standalone relay process over loopback HTTP, never by
	a browser — ``allow_guest=True`` because the relay has no Frappe
	session; ``shared_secret`` (checked in constant time, since this
	endpoint is deliberately reachable without one) stands in for auth
	instead.

	Single-use: the cache entry is deleted the instant it's read, so a
	stolen/replayed session_id can't be resolved a second time even inside
	its TTL window.
	"""
	expected = frappe.conf.get("console_relay_secret")
	if not expected or not hmac.compare_digest(str(shared_secret), str(expected)):
		frappe.throw("Invalid relay credentials", frappe.AuthenticationError)

	key = _cache_key(session_id)
	raw = frappe.cache().get_value(key)
	if raw is None:
		return None
	frappe.cache().delete_value(key)
	return frappe.parse_json(raw)
