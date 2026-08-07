# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

"""Context for the /alfaedge-pulse www page — the React SPA's entry point.

Requires an authenticated Frappe session (redirects Guests to /login) since
this dashboard exposes live infrastructure data; there is no separate
login flow of its own, it just rides on the normal Frappe session cookie.
"""

import json

import frappe

no_cache = 1

ASSET_BASE = "/assets/proxmox_monitor/alfaedge-pulse"
MANIFEST_PATH = frappe.get_app_path("proxmox_monitor", "public", "alfaedge-pulse", ".vite", "manifest.json")


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/alfaedge-pulse"
		raise frappe.Redirect

	context.csrf_token = frappe.sessions.get_csrf_token()
	context.site_name = frappe.local.site
	context.js_path, context.css_paths = _resolve_asset_paths()


def _resolve_asset_paths() -> tuple[str, list[str]]:
	"""Look up this build's actual (content-hashed) JS/CSS filenames.

	Vite hashes filenames so each `npm run build` produces a new URL —
	necessary because nginx caches everything under /assets for a full
	year with no revalidation (see config/nginx.conf), so a fixed
	filename would mean deploys silently keep serving stale JS/CSS to
	anyone who already loaded the page. We read the manifest fresh on
	every request rather than caching it in-process: this file only
	changes when someone runs a new frontend build, which is rare enough
	that the extra file read is a non-issue, and it means a deploy takes
	effect immediately without needing a worker restart.
	"""
	with open(MANIFEST_PATH) as f:
		manifest = json.load(f)

	entry = manifest["index.html"]
	js_path = f"{ASSET_BASE}/{entry['file']}"
	css_paths = [f"{ASSET_BASE}/{css}" for css in entry.get("css", [])]
	return js_path, css_paths
