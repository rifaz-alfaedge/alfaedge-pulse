// Copyright (c) 2026, AlfaEdge and contributors
// For license information, please see license.txt

frappe.ui.form.on("Bifrost Settings", {
	sync_now(frm) {
		frappe.dom.freeze(__("Syncing with Bifrost..."));
		frappe.call({
			method: "proxmox_monitor.llm_usage_monitor.doctype.bifrost_settings.bifrost_settings.sync_now",
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
