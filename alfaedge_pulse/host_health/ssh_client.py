# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""SSH access to monitored hosts, optionally through a Warpgate bastion.

Pulse holds one private key (``SSH Settings``, encrypted). In **Warpgate**
mode that key only authenticates to Warpgate (username
``<warpgate user>:<target>``); Warpgate then reaches the server with its own
key, so Pulse never holds a key that touches servers directly. In **Direct**
mode it logs in to the host itself.

Connections are kept open per host and reused for every poll (one SSH
session, many exec channels) so a bastion does not see a new session each
time. The key is loaded from memory and never written to disk. Host keys are
pinned: the first key seen is stored, any later change refuses the
connection until a manager resets the pin.
"""

from __future__ import annotations

import io
import json
import os
import shlex
import threading
import time
from dataclasses import dataclass

import frappe
import paramiko
from frappe.utils.password import get_decrypted_password

COLLECTOR_PATH = os.path.join(os.path.dirname(__file__), "collector", "collect.py")
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
RECONNECT_AFTER_SECONDS = 3600


def polling_allowed() -> bool:
	"""Whether this server may poll hosts at all: ``pulse_ssh_polling`` must be
	set in ``site_config.json``. That file is not part of a database backup, so
	a restored copy of production (which carries SSH Settings, including the
	key and the *Polling Enabled* switch) stays silent until someone sets it
	here on purpose, the same way ``mute_emails`` works."""
	return bool(frappe.conf.get("pulse_ssh_polling"))


class SSHPollError(Exception):
	"""A poll failed in a way worth showing on the host. ``kind`` is a short
	machine-friendly reason (auth, timeout, hostkey, refused, collector).
	``scope`` is "bastion" when the failure is reaching the Warpgate server
	itself (so it affects every host the same way) and "host" otherwise."""

	def __init__(self, kind: str, message: str, scope: str = "host"):
		super().__init__(message)
		self.kind = kind
		self.scope = scope


class HostKeyChanged(paramiko.SSHException):
	pass


@dataclass
class Target:
	"""Everything needed to reach one host, resolved from SSH Settings and
	the Monitored Host record (no Frappe access needed after this)."""

	name: str  # Monitored Host name
	hostname: str  # where to open the TCP connection
	port: int
	username: str
	pinned_key: str | None  # "<type> <base64>" or None (trust on first use)
	pin_scope: str  # "settings" (Warpgate) or "host" (Direct)
	bench_path: str
	services: list[dict]
	connect_timeout: int
	command_timeout: int
	slow: bool = False  # also do the heavier, slower-changing reads this time


class _PinPolicy(paramiko.MissingHostKeyPolicy):
	def __init__(self, pinned: str | None):
		self.pinned = pinned
		self.seen: str | None = None

	def missing_host_key(self, client, hostname, key):
		presented = f"{key.get_name()} {key.get_base64()}"
		self.seen = presented
		if self.pinned and self.pinned != presented:
			raise HostKeyChanged(f"host key for {hostname} does not match the pinned key")


_pool: dict[str, tuple[paramiko.SSHClient, float]] = {}
_pool_lock = threading.Lock()
_host_locks: dict[str, threading.Lock] = {}


def _host_lock(name: str) -> threading.Lock:
	with _pool_lock:
		return _host_locks.setdefault(name, threading.Lock())


def parse_private_key(pem: str, passphrase: str | None = None) -> paramiko.PKey:
	"""Reads a private key of any supported type (paramiko's base class no
	longer auto-detects the type). Raises paramiko.SSHException if none fit."""
	last: Exception | None = None
	for cls in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
		try:
			return cls.from_private_key(io.StringIO(pem), password=passphrase or None)
		except paramiko.SSHException as exc:
			last = exc
	raise paramiko.SSHException(f"unsupported or unreadable private key ({last})")


def load_private_key() -> paramiko.PKey:
	pem = get_decrypted_password("SSH Settings", "SSH Settings", "private_key", raise_exception=False)
	if not pem:
		raise SSHPollError("auth", "No SSH private key is configured in SSH Settings.")
	passphrase = get_decrypted_password("SSH Settings", "SSH Settings", "key_passphrase", raise_exception=False)
	try:
		return parse_private_key(pem, passphrase)
	except Exception as exc:
		raise SSHPollError("auth", f"Could not read the SSH private key ({type(exc).__name__}).")


def allowed_roles() -> list[str] | None:
	"""The Proxmox server roles this server may poll, from ``pulse_ssh_roles`` in
	its own site_config.json, or None for any. A restored copy of production
	carries production's hosts, settings and key, but not this file, so a dev
	server that lists only Staging and Development can never reach a production
	guest, whatever else it was restored with."""
	value = frappe.conf.get("pulse_ssh_roles")
	if not value:
		return None
	if isinstance(value, str):
		value = [part.strip() for part in value.split(",")]
	return [r for r in value if r]


def check_role_allowed(host_name: str) -> None:
	allowed = allowed_roles()
	if not allowed:
		return
	from alfaedge_pulse.host_health.doctype.monitored_host.monitored_host import get_host_role

	role = get_host_role(host_name)
	if role not in allowed:
		raise SSHPollError(
			"config",
			f"This server only polls hosts of role {', '.join(allowed)}; this host is {role or 'unknown'} "
			"(pulse_ssh_roles in this server's site_config.json).",
		)


def warpgate_target_name(host, settings) -> str:
	"""The Warpgate target for a host. A name typed on the host wins (for a
	server Warpgate calls something else); otherwise, with the *Hostname*
	convention in SSH Settings, the host's own hostname. Empty if neither."""
	custom = (host.ssh_target or "").strip()
	if custom:
		return custom
	if (settings.target_naming or "Hostname") == "Hostname":
		return (host.hostname or "").strip()
	return ""


def resolve_target(host_name: str) -> Target:
	"""Builds the connection details for a Monitored Host. Run in the Frappe
	context; the result is a plain object safe to use from worker threads."""
	check_role_allowed(host_name)
	settings = frappe.get_cached_doc("SSH Settings")
	host = frappe.get_doc("Monitored Host", host_name)

	# Through Warpgate the target is a name that someone created there on
	# purpose: either typed on the host, or the hostname by the convention the
	# settings name. It is never guessed from the guest IP, or an unconfigured
	# host would be sent to the bastion as a made-up target.
	if (settings.connection_mode or "Warpgate") == "Warpgate":
		target = warpgate_target_name(host, settings)
		if not target:
			raise SSHPollError(
				"config",
				"No Warpgate target for this host: type one in its SSH Access section, "
				"or give it a hostname and use the Hostname naming in SSH Settings.",
			)
		if any(ch.isspace() for ch in target) or ":" in target:
			raise SSHPollError("config", f"'{target}' cannot be a Warpgate target (no spaces or colons); set a different one on the host.")
	else:
		guest_ip = frappe.db.get_value("Proxmox Guest", host.proxmox_guest, "ip_address")
		target = (host.ssh_target or "").strip() or (guest_ip or "").strip()
		if not target:
			raise SSHPollError("config", "No SSH target: set one on the host or wait for the guest IP to be learned.")

	services = [
		{"service_name": r.service_name, "unit_name": r.unit_name}
		for r in (host.expected_services or frappe.get_cached_doc("Host Monitor Settings").default_expected_services)
	]

	if (settings.connection_mode or "Warpgate") == "Warpgate":
		if not (settings.server_host and settings.warpgate_user):
			raise SSHPollError("config", "Warpgate host and user are not set in SSH Settings.")
		return Target(
			name=host_name,
			hostname=settings.server_host,
			port=int(settings.server_port or 2222),
			username=f"{settings.warpgate_user}:{target}",
			pinned_key=settings.server_host_key or None,
			pin_scope="settings",
			bench_path=(host.bench_path or settings.default_bench_path or "").strip(),
			services=services,
			connect_timeout=int(settings.connect_timeout or 20),
			command_timeout=int(settings.command_timeout or 60),
		)
	return Target(
		name=host_name,
		hostname=target,
		port=int(host.ssh_port or 22),
		username=(host.ssh_user or settings.default_user or "").strip(),
		pinned_key=host.pinned_host_key or None,
		pin_scope="host",
		bench_path=(host.bench_path or settings.default_bench_path or "").strip(),
		services=services,
		connect_timeout=int(settings.connect_timeout or 20),
		command_timeout=int(settings.command_timeout or 60),
	)


def _connect_scope(target: Target) -> str:
	"""A failed connection is a bastion problem only in Warpgate mode, where
	the connection goes to Warpgate rather than to the host."""
	return "bastion" if target.pin_scope == "settings" else "host"


def _connect(target: Target, pkey: paramiko.PKey) -> tuple[paramiko.SSHClient, str | None]:
	policy = _PinPolicy(target.pinned_key)
	client = paramiko.SSHClient()
	client.set_missing_host_key_policy(policy)
	try:
		client.connect(
			hostname=target.hostname,
			port=target.port,
			username=target.username,
			pkey=pkey,
			allow_agent=False,
			look_for_keys=False,
			timeout=target.connect_timeout,
			banner_timeout=target.connect_timeout,
			auth_timeout=target.connect_timeout,
		)
	except HostKeyChanged as exc:
		client.close()
		raise SSHPollError("hostkey", str(exc), scope=_connect_scope(target))
	except paramiko.AuthenticationException:
		client.close()
		raise SSHPollError("auth", "SSH authentication was refused.")
	except (TimeoutError, OSError) as exc:
		client.close()
		kind = "timeout" if isinstance(exc, TimeoutError) else "refused"
		raise SSHPollError(
			kind,
			f"Could not connect to {target.hostname}:{target.port} ({type(exc).__name__}).",
			scope=_connect_scope(target),
		)
	except paramiko.SSHException as exc:
		client.close()
		raise SSHPollError("refused", f"SSH negotiation failed ({type(exc).__name__}).", scope=_connect_scope(target))
	transport = client.get_transport()
	if transport:
		transport.set_keepalive(30)
	return client, (policy.seen if not target.pinned_key else None)


def _get_client(target: Target, pkey: paramiko.PKey) -> tuple[paramiko.SSHClient, str | None]:
	"""Reuses the host's open connection when it is still alive and young."""
	with _pool_lock:
		entry = _pool.get(target.name)
	if entry:
		client, born = entry
		transport = client.get_transport()
		if transport and transport.is_active() and time.time() - born < RECONNECT_AFTER_SECONDS:
			return client, None
		client.close()
	client, new_key = _connect(target, pkey)
	with _pool_lock:
		_pool[target.name] = (client, time.time())
	return client, new_key


def drop_connection(name: str) -> None:
	with _pool_lock:
		entry = _pool.pop(name, None)
	if entry:
		entry[0].close()


def _read_collector() -> str:
	with open(COLLECTOR_PATH) as handle:
		return handle.read()


def _exec_collector(client: paramiko.SSHClient, target: Target) -> dict:
	args = json.dumps({"services": target.services, "bench_path": target.bench_path, "slow": target.slow})
	bench_python = f"{target.bench_path.rstrip('/')}/env/bin/python" if target.bench_path else ""
	command = (
		f"P={shlex.quote(bench_python)}; [ -n \"$P\" ] && [ -x \"$P\" ] || P=python3; "
		f"exec \"$P\" - {shlex.quote(args)}"
	)
	stdin, stdout, stderr = client.exec_command(command, timeout=target.command_timeout)
	stdin.write(_read_collector())
	stdin.channel.shutdown_write()
	raw = stdout.read(MAX_OUTPUT_BYTES + 1)
	err = stderr.read(4096).decode("utf-8", "replace").strip()
	code = stdout.channel.recv_exit_status()
	if len(raw) > MAX_OUTPUT_BYTES:
		raise SSHPollError("collector", "Collector output was larger than the allowed size.")
	if code != 0 or not raw:
		raise SSHPollError("collector", f"Collector exited with status {code}: {err[:300] or 'no output'}")
	try:
		return json.loads(raw)
	except ValueError:
		raise SSHPollError("collector", "Collector returned output that is not valid JSON.")


def collect(target: Target, pkey: paramiko.PKey) -> tuple[dict, str | None]:
	"""One poll of one host. Returns (report, newly_seen_host_key). Safe to
	call from worker threads: only ``target`` and ``pkey`` are used, no
	Frappe state."""
	with _host_lock(target.name):
		try:
			client, new_key = _get_client(target, pkey)
			try:
				return _exec_collector(client, target), new_key
			except (paramiko.SSHException, EOFError, OSError):
				# Stale connection: reconnect once and retry.
				drop_connection(target.name)
				client, new_key = _get_client(target, pkey)
				return _exec_collector(client, target), new_key
		except SSHPollError:
			drop_connection(target.name)
			raise
		except TimeoutError:
			drop_connection(target.name)
			raise SSHPollError("timeout", f"Timed out after {target.command_timeout}s.")
		except (paramiko.SSHException, OSError) as exc:
			drop_connection(target.name)
			raise SSHPollError("refused", f"SSH error ({type(exc).__name__}).")
