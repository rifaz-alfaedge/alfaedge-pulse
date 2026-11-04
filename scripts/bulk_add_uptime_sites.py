"""Add many Uptime sites at once from a text/CSV file.

Each line becomes a site on EVERY enabled Uptime Kuma instance, exactly like
the dashboard's "+ Add site" button. By default the script only previews the
file; it adds sites only when PULSE_ADD_CONFIRM=yes.

    cd /home/ubuntu/frappe-bench
    export PULSE_SITES_FILE=/home/ubuntu/sites.csv
    bench --site alfaedge execute 'exec(open("/home/ubuntu/bulk_add_uptime_sites.py").read(), {"__name__": "t"})'
    PULSE_ADD_CONFIRM=yes bench --site alfaedge execute 'exec(open("/home/ubuntu/bulk_add_uptime_sites.py").read(), {"__name__": "t"})'

File format, one site per line (a header line starting with "name" and lines
starting with # are ignored):

    name, target [, type] [, interval_seconds]

    Example Shop, https://shop.example.com
    Example API,  https://api.example.com/health,  , 30
    Database,     db.example.com:3306
    Gateway,      203.0.113.7,  ping
    https://www.example.com               <- a lone target: the name is its host

  target  https://... or http://...  -> a web check
          host:port                  -> a TCP check
          host or IP                 -> a ping check
  type    HTTP(s), TCP or Ping; leave empty to follow the target
  interval  check interval in seconds (1 to 86400); empty = the fleet setting,
            Uptime Monitor Settings > Heartbeat Interval (15 on production)

Kuma only allows about 20 logins a minute, and every add logs in once per
instance, so the script paces itself (about 3 seconds per instance per site)
and retries once after a rate-limit message. 100 sites on 2 instances takes
roughly 10 minutes. Sites whose name already exists are skipped, so it is safe
to run again after a failure.
"""

import csv
import io
import os
import re
import time
from urllib.parse import urlparse

import frappe

MAX_ROWS = 500
MIN_INTERVAL, MAX_INTERVAL, FALLBACK_INTERVAL = 1, 86400, 60  # Kuma itself refuses an interval it does not allow
SECONDS_PER_LOGIN = 3.2  # Kuma allows 20 logins a minute
RATE_LIMIT_WAIT = 65
HOST_RE = re.compile(r"^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")
TYPES = {"http": "HTTP(s)", "http(s)": "HTTP(s)", "https": "HTTP(s)", "web": "HTTP(s)", "tcp": "TCP", "port": "TCP", "ping": "Ping", "icmp": "Ping"}


def _is_host(value):
	if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", value):
		return all(0 <= int(p) <= 255 for p in value.split("."))
	return bool(HOST_RE.match(value))


def fleet_interval():
	"""The fleet-wide heartbeat interval (Uptime Monitor Settings). New sites use it so
	they match every other site and are not changed by the settings sync."""
	return int(frappe.db.get_single_value("Uptime Monitor Settings", "heartbeat_interval_seconds") or 0) or FALLBACK_INTERVAL


def parse_line(fields, default_interval=FALLBACK_INTERVAL):
	"""One row -> (row dict, None) or (None, why it was rejected)."""
	fields = [f.strip() for f in fields]
	if len(fields) >= 2 and fields[0] and not fields[1]:
		return None, f"'{fields[0]}' has no target"
	while fields and fields[-1] == "":
		fields.pop()
	if not fields:
		return None, "empty"
	if len(fields) == 1:
		name, target, kind, interval = "", fields[0], "", ""
	else:
		name, target, kind, interval = (fields + ["", "", ""])[:4]
	if not target:
		return None, "no target"

	if kind:
		monitor_type = TYPES.get(kind.lower())
		if not monitor_type:
			return None, f"unknown type '{kind}' (use HTTP(s), TCP or Ping)"
	elif re.match(r"^https?://", target, re.I):
		monitor_type = "HTTP(s)"
	elif ":" in target and not target.lower().startswith(("http:", "https:")):
		monitor_type = "TCP"
	else:
		monitor_type = "Ping"

	row = {"monitor_type": monitor_type, "url": None, "hostname": None, "port": None}
	if monitor_type == "HTTP(s)":
		if not re.match(r"^https?://", target, re.I):
			target = "https://" + target
		parsed = urlparse(target)
		if not parsed.hostname or not _is_host(parsed.hostname):
			return None, f"'{target}' is not a valid web address"
		row["url"] = target
		host = parsed.hostname
	elif monitor_type == "TCP":
		host, _, port = target.rpartition(":")
		if not host or not port.isdigit() or not 1 <= int(port) <= 65535 or not _is_host(host):
			return None, f"'{target}' is not host:port"
		row["hostname"], row["port"] = host, int(port)
	else:
		host = target
		if not _is_host(host):
			return None, f"'{target}' is not a valid host name or IP"
		row["hostname"] = host

	if interval:
		if not interval.isdigit() or not MIN_INTERVAL <= int(interval) <= MAX_INTERVAL:
			return None, f"interval must be {MIN_INTERVAL} to {MAX_INTERVAL} seconds"
		row["check_interval_seconds"] = int(interval)
	else:
		row["check_interval_seconds"] = default_interval
	row["site_name"] = name or host
	return row, None


def parse_file(text, default_interval=FALLBACK_INTERVAL):
	"""All rows of the file: a list of (line number, row or None, problem or None)."""
	out = []
	for number, fields in enumerate(csv.reader(io.StringIO(text)), start=1):
		if not fields or not "".join(fields).strip() or fields[0].strip().startswith("#"):
			continue
		if number == 1 and fields[0].strip().lower() in ("name", "site", "site_name"):
			continue
		row, problem = parse_line(fields, default_interval)
		out.append((number, row, problem))
	return out


def plan(text, existing=None, default_interval=None):
	"""Sort the file's rows into: to add, already there, repeated, rejected."""
	existing = {n.lower() for n in (existing if existing is not None else frappe.get_all("Uptime Site", pluck="site_name"))}
	seen, add, skipped, rejected = set(), [], [], []
	for number, row, problem in parse_file(text, default_interval if default_interval is not None else fleet_interval()):
		if problem:
			rejected.append((number, problem))
		elif row["site_name"].lower() in existing:
			skipped.append((number, row["site_name"], "already exists"))
		elif row["site_name"].lower() in seen:
			skipped.append((number, row["site_name"], "repeated in the file"))
		else:
			seen.add(row["site_name"].lower())
			add.append((number, row))
	return add, skipped, rejected


def _add_one(row):
	from alfaedge_pulse.uptime_monitor.api import add_site

	return add_site(
		site_name=row["site_name"], monitor_type=row["monitor_type"], url=row["url"], hostname=row["hostname"],
		port=row["port"], check_interval_seconds=row["check_interval_seconds"], is_active=1,
	)


def bulk_add(text, dry_run=True, say=print, pause=time.sleep, add=_add_one) -> dict:
	instances = frappe.get_all("Uptime Kuma Instance", filters={"enabled": 1}, pluck="name")
	to_add, skipped, rejected = plan(text)
	say(f"Enabled Kuma instances: {', '.join(instances) or 'NONE'} | default check interval: {fleet_interval()}s (from Uptime Monitor Settings)")
	say(f"To add: {len(to_add)} | skipped: {len(skipped)} | rejected: {len(rejected)}")
	for number, row in to_add:
		target = row["url"] or (f"{row['hostname']}:{row['port']}" if row["port"] else row["hostname"])
		say(f"  line {number:>3}  ADD   {row['site_name']}  [{row['monitor_type']}]  {target}  every {row['check_interval_seconds']}s")
	for number, name, why in skipped:
		say(f"  line {number:>3}  SKIP  {name}  ({why})")
	for number, why in rejected:
		say(f"  line {number:>3}  BAD   {why}")
	result = {"added": [], "failed": [], "skipped": skipped, "rejected": rejected}
	if len(to_add) > MAX_ROWS:
		say(f"Too many rows ({len(to_add)}); the limit is {MAX_ROWS} per run. Split the file.")
		return result
	if dry_run:
		say("Nothing was added. Run again with PULSE_ADD_CONFIRM=yes to add the lines marked ADD.")
		return result
	if not instances:
		say("No enabled Uptime Kuma instance, so there is nowhere to add sites. Add one and enable it first.")
		return result

	delay = SECONDS_PER_LOGIN * len(instances)
	for position, (number, row) in enumerate(to_add, start=1):
		for attempt in (1, 2):
			try:
				outcome = add(row)
				result["added"].append(row["site_name"])
				partial = f" (some instances failed: {'; '.join(outcome['errors'])})" if outcome.get("errors") else ""
				say(f"[{position}/{len(to_add)}] added {row['site_name']}{partial}")
				break
			except Exception as exc:
				message = str(exc)
				if attempt == 1 and ("frequent" in message.lower() or "rate" in message.lower()):
					say(f"[{position}/{len(to_add)}] Kuma is rate-limiting logins; waiting {RATE_LIMIT_WAIT}s and trying {row['site_name']} again")
					pause(RATE_LIMIT_WAIT)
					continue
				result["failed"].append((row["site_name"], message))
				say(f"[{position}/{len(to_add)}] FAILED {row['site_name']}: {message}")
				break
		pause(delay)
	say(f"Done. Added {len(result['added'])}, failed {len(result['failed'])}, skipped {len(skipped)}, rejected {len(rejected)}.")
	if result["failed"]:
		say("Run the same file again to retry the failed ones; sites already added are skipped.")
	return result


if __name__ == "t":  # `bench execute` runs this file with that name; tests import it under another
	path = os.environ.get("PULSE_SITES_FILE", "")
	if not path or not os.path.isfile(path):
		print("Set PULSE_SITES_FILE to the path of your file (see the top of this script).")
	else:
		bulk_add(open(path, encoding="utf-8-sig").read(), dry_run=os.environ.get("PULSE_ADD_CONFIRM") != "yes")
