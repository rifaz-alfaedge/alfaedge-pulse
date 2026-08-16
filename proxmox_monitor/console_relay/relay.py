# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Standalone WebSocket relay: browser <-> Proxmox vncwebsocket, brokered by
a short-lived session_id resolved against the Frappe site (see
console_relay/api.py's resolve_session).

Deliberately NOT part of the Frappe app's import graph — this is a plain
script, run under supervisor as its own long-running process
(frappe-bench-console-relay), the same shape `bench socketio` already runs
as a standalone Node process alongside the gunicorn web workers. Invoked
directly as `python -m proxmox_monitor.console_relay.relay`, not through
any `bench` subcommand (there isn't one for a custom relay like this).

Why this process exists at all rather than the browser talking to Proxmox
directly: a browser's native WebSocket object cannot set an Authorization
header on its handshake, but Proxmox's vncwebsocket endpoint needs one
(the same PVEAPIToken header every other call in this app already sends).
This relay is a real HTTP-capable WS client, so it can set that header —
and doing the connection server-side also means the Proxmox host is never
exposed to the viewer's browser at all, per this feature's design.

Two things are marked TODO-VERIFY below — Proxmox's exact wire framing for
the LXC/termproxy case, and whether vncticket needs to be a query param,
a Sec-WebSocket-Protocol value, or both. These were flagged as
needs-hands-on-verification in the implementation plan (not something to
assume from documentation alone) — confirm against a real test VM/CT with
a curl + short websockets script before trusting this in production, and
update the two spots below if reality differs from what's coded here.
"""

from __future__ import annotations

import asyncio
import logging
import os
import ssl
import urllib.parse

import requests
import websockets
from websockets.exceptions import ConnectionClosed

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("console-relay")

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = int(os.environ.get("CONSOLE_RELAY_PORT", "9001"))
FRAPPE_RESOLVE_URL = os.environ.get(
	"FRAPPE_INTERNAL_URL", "http://127.0.0.1:8000/api/method/proxmox_monitor.console_relay.api.resolve_session"
)
RELAY_SECRET = os.environ["CONSOLE_RELAY_SECRET"]  # required — fail fast at startup, not on first connection
# Frappe's gunicorn workers are multi-tenant and route purely off the Host
# header — hitting 127.0.0.1:8000 directly without it 404s with "127.0.0.1
# does not exist" regardless of the path, confirmed live. FRAPPE_SITE_NAME
# must be the real site name (e.g. "alfaedge"), not a public hostname.
FRAPPE_SITE_NAME = os.environ["FRAPPE_SITE_NAME"]

#: A resolve_session round-trip to the local Frappe web worker; generous
#: but bounded so one hung Frappe worker doesn't wedge a connection handler
#: forever.
RESOLVE_TIMEOUT_SECONDS = 10


def resolve_session(session_id: str) -> dict | None:
	try:
		response = requests.post(
			FRAPPE_RESOLVE_URL,
			data={"session_id": session_id, "shared_secret": RELAY_SECRET},
			headers={"Host": FRAPPE_SITE_NAME},
			timeout=RESOLVE_TIMEOUT_SECONDS,
		)
		response.raise_for_status()
	except requests.RequestException as e:
		log.warning("resolve_session request failed for %s: %s", session_id, e)
		return None

	message = response.json().get("message")
	return message or None


async def pipe(source, destination, label: str) -> None:
	try:
		async for chunk in source:
			await destination.send(chunk)
	except ConnectionClosed:
		pass
	except Exception:
		log.exception("pipe error (%s)", label)


async def handle_connection(browser_ws) -> None:
	# websockets>=13 hands the request path via browser_ws.request.path;
	# /console-ws/ itself is stripped by nginx's location match, so what
	# arrives here is just /{session_id}.
	path = browser_ws.request.path if hasattr(browser_ws, "request") else browser_ws.path
	session_id = path.rsplit("/", 1)[-1]

	bundle = resolve_session(session_id)
	if not bundle:
		log.info("rejecting connection: session %s not found/expired/already used", session_id)
		await browser_ws.close(code=4401, reason="invalid or expired session")
		return

	# TODO-VERIFY: confirm vncticket as a query param is sufficient on this
	# PVE version, vs. also needing it as a Sec-WebSocket-Protocol value.
	# The ticket contains characters (":", "+", "/", "=") that need
	# percent-encoding once to survive as a query value — Proxmox's own
	# web UI does the same single encodeURIComponent() before building
	# this URL.
	vncticket = urllib.parse.quote(bundle["vnc_ticket"], safe="")
	proxmox_url = (
		f"wss://{bundle['hostname']}:{bundle['port']}/api2/json/nodes/{bundle['node']}"
		f"/vncwebsocket?port={bundle['vnc_port']}&vncticket={vncticket}"
	)
	# NOT the Authorization/PVEAPIToken header used everywhere else in this
	# app — confirmed live that vncwebsocket rejects API-token auth outright
	# with a bare HTTP 401. This endpoint only accepts a PVEAuthCookie
	# session cookie (see console_relay/api.py's pve_login call, and
	# proxmox_client/base.py's from_ticket docstring for the full story).
	cookie_header = f"PVEAuthCookie={bundle['pve_auth_ticket']}"
	ssl_ctx = ssl.create_default_context() if bundle["verify_ssl"] else ssl._create_unverified_context()

	try:
		async with websockets.connect(
			proxmox_url, additional_headers={"Cookie": cookie_header}, ssl=ssl_ctx
		) as proxmox_ws:
			log.info("session %s: bridging browser to %s", session_id, bundle["node"])
			if bundle["protocol"] == "terminal":
				# termproxy sessions additionally require this exact line as
				# the first message before any real shell data flows —
				# confirmed via the Proxmox community, not documented in the
				# API schema. QEMU/VNC sessions skip this: the vnc_ticket
				# already doubles as the RFB handshake's password, no
				# separate line-based auth step needed.
				await proxmox_ws.send(f"{bundle['console_username']}:{bundle['vnc_ticket']}\n")
			await asyncio.gather(
				pipe(browser_ws, proxmox_ws, "browser->proxmox"),
				pipe(proxmox_ws, browser_ws, "proxmox->browser"),
			)
	except websockets.exceptions.InvalidStatus as e:
		# Proxmox's own rejection reason (e.g. "No ticket", "invalid ticket")
		# lives in the response body/reason phrase, which the bare exception
		# string doesn't include — surfacing it here to actually diagnose
		# auth failures instead of guessing from a bare status code.
		body = e.response.body.decode("utf-8", errors="replace") if e.response.body else ""
		log.error(
			"session %s: Proxmox rejected the vncwebsocket handshake: HTTP %s %s — body: %s",
			session_id, e.response.status_code, e.response.reason_phrase, body,
		)
		await browser_ws.close(code=1011, reason="upstream connection failed")
	except Exception:
		log.exception("session %s: failed to bridge to Proxmox", session_id)
		await browser_ws.close(code=1011, reason="upstream connection failed")


async def main() -> None:
	log.info("console relay listening on %s:%s", LISTEN_HOST, LISTEN_PORT)
	async with websockets.serve(handle_connection, LISTEN_HOST, LISTEN_PORT):
		await asyncio.Future()


if __name__ == "__main__":
	asyncio.run(main())
