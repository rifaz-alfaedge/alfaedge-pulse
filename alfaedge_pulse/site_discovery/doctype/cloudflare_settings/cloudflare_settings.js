// Copyright (c) 2026, AlfaEdge and contributors
// For license information, please see license.txt

const CF_PATH = "alfaedge_pulse.site_discovery.doctype.cloudflare_settings.cloudflare_settings";

function cfCall(frm, method, freezeMsg) {
	const run = () =>
		frappe.call({
			method: `${CF_PATH}.${method}`,
			freeze: true,
			freeze_message: freezeMsg,
			callback(r) {
				const m = r.message || {};
				frappe.msgprint({
					title: m.ok ? __("Done") : __("Failed"),
					indicator: m.ok ? "green" : "red",
					message: frappe.utils.escape_html(m.ok ? m.message : m.error || __("Unknown error")),
				});
				frm.reload_doc();
			},
		});
	if (frm.is_dirty()) frm.save().then(run);
	else run();
}

frappe.ui.form.on("Cloudflare Settings", {
	test_connection(frm) {
		cfCall(frm, "test_connection", __("Connecting to Cloudflare..."));
	},
	sync_now(frm) {
		cfCall(frm, "sync_now", __("Starting site sync..."));
	},
});
