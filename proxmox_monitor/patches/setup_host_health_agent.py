# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Backfills the Host Health agent user/role and default expected services
on sites that had proxmox_monitor installed *before* v3.0.0 (Host Health)
shipped. install.after_install only runs on a fresh `install-app` — a site
that already had this app installed just picks up new doctypes via
`bench migrate` and never re-runs it, so the shared agent user never gets
created there, breaking every "Generate / Regenerate Agent Key" click with
a "Could not find Agent User" error. Both callees are already idempotent
(see their own docstrings in install.py), so this is safe to run on a site
that's already correctly set up too.
"""

from proxmox_monitor.install import _seed_default_expected_services, _setup_host_health_agent


def execute():
	_setup_host_health_agent()
	_seed_default_expected_services()
