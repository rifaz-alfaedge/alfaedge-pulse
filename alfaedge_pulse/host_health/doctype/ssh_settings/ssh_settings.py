# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from __future__ import annotations

import io

import frappe
from frappe.model.document import Document
from frappe.utils.password import set_encrypted_password

MANAGE_ROLES = ("System Manager", "Proxmox Monitor Manager")


class SSHSettings(Document):
	"""Where and how Pulse logs in to monitored hosts (see ssh_client.py).

	The private key is a write-only Password field: it is only ever set by
	``generate_key_pair`` / ``import_private_key`` below and never returned
	to the browser; only the public key and fingerprint are shown."""


def _store_key(pem: str, passphrase: str | None) -> dict:
	from alfaedge_pulse.host_health.ssh_client import parse_private_key

	key = parse_private_key(pem, passphrase)
	public = f"{key.get_name()} {key.get_base64()} pulse@{frappe.local.site}"
	set_encrypted_password("SSH Settings", "SSH Settings", pem, "private_key")
	if passphrase:
		set_encrypted_password("SSH Settings", "SSH Settings", passphrase, "key_passphrase")
	frappe.db.set_single_value(
		"SSH Settings", {"public_key": public, "key_fingerprint": key.fingerprint}
	)
	frappe.clear_cache(doctype="SSH Settings")
	return {"public_key": public, "fingerprint": key.fingerprint}


@frappe.whitelist()
def generate_key_pair() -> dict:
	"""Creates a new Ed25519 key pair, stores the private half encrypted and
	returns only the public half. Replaces any existing key."""
	frappe.only_for(MANAGE_ROLES)
	from cryptography.hazmat.primitives import serialization
	from cryptography.hazmat.primitives.asymmetric import ed25519

	private = ed25519.Ed25519PrivateKey.generate()
	pem = private.private_bytes(
		serialization.Encoding.PEM,
		serialization.PrivateFormat.OpenSSH,
		serialization.NoEncryption(),
	).decode()
	set_encrypted_password("SSH Settings", "SSH Settings", "", "key_passphrase")
	return _store_key(pem, None)


@frappe.whitelist()
def import_private_key(private_key: str, passphrase: str | None = None) -> dict:
	frappe.only_for(MANAGE_ROLES)
	try:
		return _store_key(private_key.strip() + "\n", passphrase)
	except Exception:
		frappe.throw("That is not a readable SSH private key (or the passphrase is wrong).")


@frappe.whitelist()
def reset_pinned_key(monitored_host: str | None = None) -> None:
	"""Forget a pinned host key so the next connection pins the key it sees."""
	frappe.only_for(MANAGE_ROLES)
	if monitored_host:
		frappe.db.set_value("Monitored Host", monitored_host, "pinned_host_key", None)
	else:
		frappe.db.set_single_value("SSH Settings", "server_host_key", "")
	frappe.clear_cache(doctype="SSH Settings")


@frappe.whitelist()
def test_connection(monitored_host: str) -> dict:
	"""One real poll of one host, reported but not stored."""
	frappe.only_for(MANAGE_ROLES)
	from alfaedge_pulse.host_health import ssh_client

	target = ssh_client.resolve_target(monitored_host)
	try:
		report, _ = ssh_client.collect(target, ssh_client.load_private_key())
	except ssh_client.SSHPollError as exc:
		return {"ok": False, "kind": exc.kind, "message": str(exc)}
	status = report.get("status", {})
	return {
		"ok": True,
		"services": len(status.get("services", [])),
		"benches": len(status.get("benches", [])),
		"sites": len(status.get("sites", [])),
		"disks": len(report.get("resources", {}).get("disks", [])),
		"errors": report.get("errors", []),
	}


@frappe.whitelist()
def get_polling_state() -> dict:
	"""For the form banner: is this server allowed to poll at all (see
	``ssh_client.polling_allowed``)."""
	frappe.only_for(MANAGE_ROLES)
	from alfaedge_pulse.host_health.ssh_client import polling_allowed

	return {"allowed": polling_allowed()}


# ---------------------------------------------------------------------------
# Adding hosts in bulk
# ---------------------------------------------------------------------------

TARGET_PATTERNS = {
	# altaza-staging.alfaedge.in -> Altaza-Staging (the usual Warpgate target naming)
	"title": lambda guest_name: "-".join(part.capitalize() for part in guest_name.split(".")[0].split("-")),
	"short": lambda guest_name: guest_name.split(".")[0],
	"full": lambda guest_name: guest_name,
}
MAX_BULK_HOSTS = 200


def _candidate_guests(role: str, only_running: bool, pattern: str) -> list[dict]:
	if pattern not in TARGET_PATTERNS:
		frappe.throw("Unknown target name pattern.")
	servers = frappe.get_all("Proxmox Server", filters={"role": role, "server_type": "PVE"}, pluck="name")
	if not servers:
		return []
	filters = {"server": ["in", servers]}
	if only_running:
		filters["status"] = "running"
	existing = set(frappe.get_all("Monitored Host", pluck="proxmox_guest"))
	rows = []
	for guest in frappe.get_all(
		"Proxmox Guest", filters=filters, fields=["name", "guest_name", "status", "ip_address"], order_by="guest_name asc"
	):
		if guest.name in existing or not guest.guest_name:
			continue
		rows.append(
			{
				"guest": guest.name,
				"guest_name": guest.guest_name,
				"status": guest.status,
				"target": TARGET_PATTERNS[pattern](guest.guest_name),
			}
		)
	return rows[:MAX_BULK_HOSTS]


@frappe.whitelist()
def preview_hosts_from_guests(role: str = "Staging", only_running: int = 1, pattern: str = "title") -> list[dict]:
	"""Guests of this role that have no Monitored Host yet, with the Warpgate
	target name each would get. Nothing is created."""
	frappe.only_for(MANAGE_ROLES)
	return _candidate_guests(role, bool(int(only_running)), pattern)


@frappe.whitelist()
def create_hosts_from_guests(guests: str, role: str = "Staging", only_running: int = 1, pattern: str = "title") -> dict:
	"""Creates a Monitored Host for each chosen guest, **switched off**, with
	the Warpgate target filled in. They start being polled only after someone
	adds the matching target in Warpgate and enables the host. The target
	names are recomputed here, never taken from the browser."""
	frappe.only_for(MANAGE_ROLES)
	chosen = set(frappe.parse_json(guests) or [])
	by_guest = {row["guest"]: row for row in _candidate_guests(role, bool(int(only_running)), pattern)}
	created = []
	for guest in chosen:
		row = by_guest.get(guest)
		if not row:
			continue  # already monitored, wrong role, or not a candidate
		doc = frappe.get_doc(
			{"doctype": "Monitored Host", "proxmox_guest": guest, "enabled": 0, "ssh_target": row["target"]}
		).insert()
		created.append(doc.name)
	return {"created": len(created), "skipped": len(chosen) - len(created)}
