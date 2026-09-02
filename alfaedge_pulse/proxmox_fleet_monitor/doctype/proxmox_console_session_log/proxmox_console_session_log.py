# Copyright (c) 2026, AlfaEdge and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class ProxmoxConsoleSessionLog(Document):
	"""Audit trail of every embedded VNC/terminal console session opened
	from the dashboard.

	One row per successful ``console_relay.api.open_console`` call — written
	before the session_id is even handed to the browser, so this is "console
	was opened", not "console connection completed successfully". Guest-only
	by construction: every row's ``guest`` came from a real ``Proxmox Guest``
	document, since that's the only input open_console ever accepts.
	"""
