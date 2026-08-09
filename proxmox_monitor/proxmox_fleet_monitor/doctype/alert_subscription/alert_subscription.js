// Copyright (c) 2026, AlfaEdge and contributors
// For license information, please see license.txt

frappe.ui.form.on("Alert Subscription", {
	onload(frm) {
		if (frm.is_new() && !frm.doc.user) {
			frm.set_value("user", frappe.session.user);
		}
	},
	refresh(frm) {
		// Only ever offer guests/datastores that live on the currently
		// selected server.
		frm.set_query("instance", () => ({
			filters: { server: frm.doc.server || "" },
		}));

		if (!frm.is_new()) {
			frm.add_custom_button(__("Send Test Alert"), () => {
				frappe.call({
					method: "proxmox_monitor.alerts.dispatch.send_subscription_test_alert",
					args: { name: frm.doc.name },
					freeze: true,
					freeze_message: __("Sending test alert..."),
					callback: (r) => {
						const results = r.message || {};
						const channels = Object.keys(results);
						if (!channels.length) {
							frappe.msgprint(__("No channels are enabled on this subscription."));
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
		}
	},
	server(frm) {
		// A previously chosen instance may no longer belong to the new
		// server — clear rather than leave a stale/mismatched selection.
		if (frm.doc.instance) {
			frm.set_value("instance", "");
		}
	},
	instance_type(frm) {
		if (frm.doc.instance) {
			frm.set_value("instance", "");
		}
	},
});
