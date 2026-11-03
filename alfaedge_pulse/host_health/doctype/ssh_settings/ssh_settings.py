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
