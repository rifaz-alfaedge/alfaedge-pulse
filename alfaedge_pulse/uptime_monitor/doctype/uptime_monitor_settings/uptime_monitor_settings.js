// Copyright (c) 2026, AlfaEdge and contributors
// For license information, please see license.txt

frappe.ui.form.on("Uptime Monitor Settings", {
	sync_check_interval_now(frm) {
		frappe.call({
			method: "alfaedge_pulse.uptime_monitor.api.sync_check_interval_now",
			freeze: true,
			freeze_message: __("Starting sync..."),
			callback(r) {
				if (r.message && r.message.ok) {
					frappe.show_alert({ message: r.message.message, indicator: "green" });
				}
			},
		});
	},
});
