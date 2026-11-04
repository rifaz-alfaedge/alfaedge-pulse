"""Phase 1 checks. Run with:
cd /home/ubuntu/frappe-bench && bench --site alfaedge execute 'exec(open("<this file>").read(), {"__name__": "t"})'

- SSH layer: an in-process paramiko server on 127.0.0.1 runs the real collector
  locally (read-only), so connect, pin, reuse, host-key change and bad-key
  paths are exercised without touching any remote host.
- Ingest: the collector report goes through process_status/process_resources
  inside a transaction that is rolled back; alert sends are captured.
"""
import io
import json
import socket
import subprocess
import threading

import frappe
import paramiko
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from alfaedge_pulse.host_health import ingest, ssh_client

BENCH = "/home/ubuntu/frappe-bench"
results = []


def check(name, ok, detail=""):
	results.append((name, bool(ok), detail))


def new_pkey():
	pem = ed25519.Ed25519PrivateKey.generate().private_bytes(
		serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption()
	).decode()
	return ssh_client.parse_private_key(pem)


class Server(paramiko.ServerInterface):
	def __init__(self, allowed):
		self.allowed = allowed
		self.stdin_data = {}

	def check_channel_request(self, kind, chanid):
		return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

	def check_auth_publickey(self, username, key):
		return paramiko.AUTH_SUCCESSFUL if key == self.allowed and username == "pulse-monitor:testhost" else paramiko.AUTH_FAILED

	def get_allowed_auths(self, username):
		return "publickey"

	def check_channel_exec_request(self, channel, command):
		threading.Thread(target=self.run, args=(channel, command.decode()), daemon=True).start()
		return True

	def run(self, channel, command):
		data = b""
		while True:
			chunk = channel.recv(65536)
			if not chunk:
				break
			data += chunk
		proc = subprocess.run(["sh", "-c", command], input=data, capture_output=True)
		channel.sendall(proc.stdout)
		channel.sendall_stderr(proc.stderr)
		channel.send_exit_status(proc.returncode)
		channel.shutdown_write()
		channel.close()


def serve(host_key, allowed):
	sock = socket.socket()
	sock.bind(("127.0.0.1", 0))
	sock.listen(5)

	def loop():
		while True:
			try:
				conn, _ = sock.accept()
			except OSError:
				return
			t = paramiko.Transport(conn)
			t.add_server_key(host_key)
			try:
				t.start_server(server=Server(allowed))
			except Exception:
				pass

	threading.Thread(target=loop, daemon=True).start()
	return sock, sock.getsockname()[1]


def target(port, pinned=None, name="synthetic-test-host"):
	return ssh_client.Target(
		name=name, hostname="127.0.0.1", port=port, username="pulse-monitor:testhost",
		pinned_key=pinned, pin_scope="settings", bench_path=BENCH,
		services=[{"service_name": "SSH", "unit_name": "ssh.service"}],
		connect_timeout=10, command_timeout=60,
	)


client_key = new_pkey()
server_key = paramiko.Ed25519Key.from_private_key(io.StringIO(
	ed25519.Ed25519PrivateKey.generate().private_bytes(
		serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption()).decode()))
sock, port = serve(server_key, client_key)

# 1. first connection: succeeds, reports the host key to pin, JSON shape is right
report, seen = ssh_client.collect(target(port), client_key)
check("collector report has status + resources", "status" in report and "resources" in report, list(report))
check("first connection returns host key to pin", seen and seen.startswith("ssh-ed25519 "), seen)
check("services collected", report["status"].get("services"), report["status"].get("services"))
check("bench section collected", report["status"].get("benches"), report.get("errors"))
check("sites collected", report["status"].get("sites") is not None)
check("disks + load + swap collected", all(k in report["resources"] for k in ("disks", "load", "swap")))
bench = (report["status"].get("benches") or [{}])[0]
check("RQ fields present", all(k in bench for k in ("registered_workers", "queue_depths", "failed_job_count", "active_jobs")), sorted(bench))

# 2. pinned: same key accepted, connection reused (no new key reported)
ssh_client.drop_connection("synthetic-test-host")
_, seen2 = ssh_client.collect(target(port, pinned=seen), client_key)
check("pinned key accepted", seen2 is None)
_, seen3 = ssh_client.collect(target(port, pinned=seen), client_key)
check("connection reused", seen3 is None)

# 3. host key changed -> refused with kind hostkey
other = paramiko.Ed25519Key.from_private_key(io.StringIO(
	ed25519.Ed25519PrivateKey.generate().private_bytes(
		serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption()).decode()))
sock2, port2 = serve(other, client_key)
ssh_client.drop_connection("synthetic-test-host")
try:
	ssh_client.collect(target(port2, pinned=seen), client_key)
	check("changed host key refused", False)
except ssh_client.SSHPollError as exc:
	check("changed host key refused", exc.kind == "hostkey", exc.kind)

# 4. wrong client key -> auth
ssh_client.drop_connection("synthetic-test-host")
try:
	ssh_client.collect(target(port), new_pkey())
	check("wrong key refused", False)
except ssh_client.SSHPollError as exc:
	check("wrong key refused", exc.kind == "auth", exc.kind)

# 5. nothing listening -> refused
dead = socket.socket(); dead.bind(("127.0.0.1", 0)); dead_port = dead.getsockname()[1]; dead.close()
ssh_client.drop_connection("synthetic-test-host")
try:
	ssh_client.collect(target(dead_port), client_key)
	check("closed port reported", False)
except ssh_client.SSHPollError as exc:
	check("closed port reported", exc.kind in ("refused", "timeout"), exc.kind)

ssh_client.drop_connection("synthetic-test-host")
sock.close(); sock2.close()

# 6. ingest: real report through process_* in a rolled-back transaction, alerts captured
sent = []
import alfaedge_pulse.tasks.poller as poller

orig = (poller.dispatch_alert, poller.dispatch_recovery, frappe.db.commit)
poller.dispatch_alert = lambda *a, **k: sent.append(("alert", a))
poller.dispatch_recovery = lambda *a, **k: sent.append(("recovery", a))
frappe.db.commit = lambda: None
try:
	host = frappe.db.get_value("Monitored Host", {}, "name")
	if not host:
		check("ingest (needs one Monitored Host on the site)", False, "none exist")
	else:
		before = frappe.db.get_value("Monitored Host", host, ["last_seen", "is_online"], as_dict=True)
		r1 = ingest.process_status(host, **report["status"])
		r2 = ingest.process_resources(host, **report["resources"])
		after = frappe.db.get_value("Monitored Host", host, ["last_seen", "is_online"], as_dict=True)
		check("process_status ok", r1["status"] == "ok" and not r1["warnings"], r1)
		check("process_resources ok", r2["status"] == "ok" and not r2["warnings"], r2)
		check("last_seen/is_online updated by status only", after.is_online == 1 and after.last_seen != before.last_seen)
finally:
	poller.dispatch_alert, poller.dispatch_recovery, frappe.db.commit = orig
	frappe.db.rollback()

width = max(len(n) for n, _, _ in results)
for n, ok, d in results:
	print(("PASS " if ok else "FAIL ") + n.ljust(width), "" if ok else f"-> {d}")
print("captured alert dispatches:", len(sent))
print("ALL PASS" if all(ok for _, ok, _ in results) else "SOME FAILED")
