// Copyright (c) 2026, AlfaEdge and contributors
// For license information, please see license.txt

frappe.ui.form.on("ClickHouse Log Settings", {
	test_connection(frm) {
		const run = () => {
			frappe.dom.freeze(__("Connecting to ClickHouse..."));
			frappe.call({
				method: "alfaedge_pulse.log_monitor.doctype.clickhouse_log_settings.clickhouse_log_settings.test_connection",
				callback(r) {
					frappe.dom.unfreeze();
					if (r.message && r.message.ok) {
						frappe.msgprint({ title: __("Connection OK"), indicator: "green", message: r.message.message });
					} else {
						frappe.msgprint({
							title: __("Connection Failed"),
							indicator: "red",
							message: frappe.utils.escape_html((r.message && r.message.error) || __("Unknown error")),
						});
					}
				},
				error() {
					frappe.dom.unfreeze();
				},
			});
		};
		// Test what's saved — save first so unsaved edits are what gets tested.
		if (frm.is_dirty()) {
			frm.save().then(run);
		} else {
			run();
		}
	},
});
