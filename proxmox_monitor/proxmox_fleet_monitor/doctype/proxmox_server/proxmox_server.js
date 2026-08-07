// Copyright (c) 2026, AlfaEdge and contributors
// For license information, please see license.txt

frappe.ui.form.on("Proxmox Server", {
	refresh(frm) {
		// The "sync_now" field is a Button field (see proxmox_server.json);
		// Frappe fires a `<fieldname>` event on click, which we wire up here
		// instead of in the JSON so we can show progress/result feedback.
		frm.set_df_property("sync_now", "read_only", frm.is_new());
	},

	sync_now(frm) {
		if (frm.is_new()) {
			frappe.msgprint(__("Save the server before syncing."));
			return;
		}
		frappe.dom.freeze(__("Contacting {0}...", [frm.doc.hostname]));
		frappe.call({
			method: "proxmox_monitor.proxmox_fleet_monitor.doctype.proxmox_server.proxmox_server.sync_now",
			args: { server_name: frm.doc.name },
			callback(r) {
				frappe.dom.unfreeze();
				if (r.message && r.message.ok) {
					frappe.show_alert({ message: r.message.message, indicator: "green" });
					frm.reload_doc();
				} else {
					frappe.msgprint({
						title: __("Sync Failed"),
						indicator: "red",
						message: (r.message && r.message.error) || __("Unknown error"),
					});
				}
			},
		});
	},
});
