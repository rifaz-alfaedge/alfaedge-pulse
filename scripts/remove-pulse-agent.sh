#!/usr/bin/env bash
# Removes the old pulse_agent push agent from THIS guest. Run it on each guest
# that used to run it. Without --yes it only prints what it would remove.
set -euo pipefail

APPLY=0
[ "${1:-}" = "--yes" ] && APPLY=1

run() {
	if [ "$APPLY" -eq 1 ]; then
		echo "+ $*"
		"$@"
	else
		echo "would run: $*"
	fi
}

units=$(systemctl list-unit-files --no-legend 'host-health-agent*' 'resource-agent*' 2>/dev/null | awk '{print $1}' || true)
dirs=""
for d in /opt/host-health-agent /etc/host-health-agent /opt/resource-agent /etc/resource-agent; do
	[ -e "$d" ] && dirs="$dirs $d"
done

if [ -z "$units" ] && [ -z "$dirs" ]; then
	echo "No pulse_agent units or folders found on this guest. Nothing to do."
	exit 0
fi

for u in $units; do
	run sudo systemctl disable --now "$u" || true
	run sudo rm -f "/etc/systemd/system/$u"
done
[ -n "$units" ] && run sudo systemctl daemon-reload
for d in $dirs; do
	run sudo rm -rf "$d"
done

if [ "$APPLY" -eq 0 ]; then
	echo
	echo "Nothing was changed. Run again with --yes to apply."
fi
echo
echo "Not done by this script: if the pulse_agent Frappe app is installed in a bench here, remove it with"
echo "  bench --site <site> uninstall-app pulse_agent && bench remove-app pulse_agent"
