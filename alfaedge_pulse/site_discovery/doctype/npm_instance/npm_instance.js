// Copyright (c) 2026, AlfaEdge and contributors
// For license information, please see license.txt

frappe.ui.form.on("NPM Instance", {
	test_connection(frm) {
		const run = () =>
			frappe.call({
				method: "alfaedge_pulse.site_discovery.doctype.npm_instance.npm_instance.test_connection",
				args: { name: frm.doc.name },
				freeze: true,
				freeze_message: __("Connecting to NPM..."),
				callback(r) {
					const m = r.message || {};
					frappe.msgprint({
						title: m.ok ? __("Connection OK") : __("Connection Failed"),
						indicator: m.ok ? "green" : "red",
						message: frappe.utils.escape_html(m.ok ? m.message : m.error || __("Unknown error")),
					});
				},
			});
		if (frm.is_dirty() || frm.is_new()) frm.save().then(run);
		else run();
	},
});
