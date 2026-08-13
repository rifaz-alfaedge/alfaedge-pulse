// Copyright (c) 2026, AlfaEdge and contributors
// For license information, please see license.txt

frappe.ui.form.on("Monitored Host", {
	regenerate_agent_key(frm) {
		if (frm.is_new()) {
			frappe.msgprint(__("Save the document first."));
			return;
		}
		frappe.confirm(
			__("This immediately invalidates the previous key/secret — the agent on this host must be updated with the new pair or its pushes will start failing. Continue?"),
			() => {
				frappe.call({
					method: "proxmox_monitor.host_health.doctype.monitored_host.monitored_host.regenerate_agent_key",
					args: { monitored_host: frm.doc.name },
					freeze: true,
					freeze_message: __("Generating..."),
					callback(r) {
						if (!r.message) return;
						frm.reload_doc();
						const { api_key, api_secret } = r.message;
						frappe.msgprint({
							title: __("Agent Key Generated"),
							indicator: "green",
							message: `
								<p>${__("Shown once — copy both into the agent's local config now. They cannot be retrieved again after closing this dialog.")}</p>
								<p><b>${__("API Key")}:</b> <code>${frappe.utils.escape_html(api_key)}</code></p>
								<p><b>${__("API Secret")}:</b> <code>${frappe.utils.escape_html(api_secret)}</code></p>
							`,
						});
					},
				});
			}
		);
	},
});
