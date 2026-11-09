// Copyright (c) 2026, AlfaEdge and contributors
// For license information, please see license.txt

const OPENROUTER = "alfaedge_pulse.openrouter_monitor.doctype.openrouter_settings.openrouter_settings";

function runOpenRouterAction(frm, method, busyText, failTitle) {
	frappe.dom.freeze(__(busyText));
	frappe.call({
		method: `${OPENROUTER}.${method}`,
		callback(r) {
			frappe.dom.unfreeze();
			if (r.message && r.message.ok) {
				frappe.show_alert({ message: r.message.message, indicator: "green" });
				frm.reload_doc();
			} else {
				frappe.msgprint({
					title: __(failTitle),
					indicator: "red",
					message: (r.message && r.message.error) || __("Unknown error"),
				});
			}
		},
		error() {
			frappe.dom.unfreeze();
		},
	});
}

frappe.ui.form.on("OpenRouter Settings", {
	sync_usage_now(frm) {
		runOpenRouterAction(frm, "sync_usage_now", "Starting the usage sync...", "Sync Failed");
	},
	check_balance_now(frm) {
		runOpenRouterAction(frm, "check_balance_now", "Starting the balance check...", "Check Failed");
	},
});
