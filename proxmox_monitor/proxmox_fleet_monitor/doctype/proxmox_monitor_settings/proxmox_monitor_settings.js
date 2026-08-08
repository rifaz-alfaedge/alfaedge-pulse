// Copyright (c) 2026, AlfaEdge and contributors
// For license information, please see license.txt

frappe.ui.form.on("Proxmox Monitor Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Send Test Alert"), () => {
			frappe.call({
				method: "proxmox_monitor.alerts.dispatch.send_test_alert",
				freeze: true,
				freeze_message: __("Sending test alerts..."),
				callback: (r) => {
					const results = r.message || {};
					const channels = Object.keys(results);
					if (!channels.length) {
						frappe.msgprint(__("No alert channels are both enabled and fully configured."));
						return;
					}
					const lines = channels.map(
						(c) => `${c}: ${results[c] ? "✅ Sent" : "❌ Failed (check Error Log)"}`
					);
					frappe.msgprint({
						title: __("Test Alert Results"),
						message: lines.join("<br>"),
						indicator: channels.every((c) => results[c]) ? "green" : "orange",
					});
				},
			});
		});
	},
});
