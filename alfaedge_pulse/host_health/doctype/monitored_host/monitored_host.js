// Copyright (c) 2026, AlfaEdge and contributors
// For license information, please see license.txt

frappe.ui.form.on("Monitored Host", {
	regenerate_agent_key(frm) {
		const generate = () => {
			frappe.confirm(
				__("This immediately invalidates the previous key/secret — the agent on this host must be updated with the new pair or its pushes will start failing. Continue?"),
				() => {
					frappe.call({
						method: "alfaedge_pulse.host_health.doctype.monitored_host.monitored_host.regenerate_agent_key",
						args: { monitored_host: frm.doc.name },
						freeze: true,
						freeze_message: __("Generating..."),
						callback(r) {
							if (!r.message) return;
							frm.reload_doc();
							show_generated_key(r.message.api_key, r.message.api_secret);
						},
					});
				}
			);
		};

		// The key/secret pair is written straight onto this document, which
		// needs a real docname to attach to — save first (standard
		// validation, e.g. Proxmox Guest being required, still applies)
		// rather than making the user do it as a separate step beforehand.
		// frm.save()'s own promise resolves even after a failed/validation-
		// blocked save (it swallows the rejection internally to show the
		// error dialog) — recheck the form's state before proceeding so a
		// blocked save doesn't still try to generate a key for a document
		// that was never actually persisted.
		if (frm.is_new() || frm.is_dirty()) {
			frm.save().then(() => {
				if (!frm.is_new() && !frm.is_dirty()) generate();
			});
		} else {
			generate();
		}
	},
});

function show_generated_key(api_key, api_secret) {
	const values = { key: api_key, secret: api_secret };
	const row = (field, label, value) => `
		<div style="display:flex; align-items:center; gap:8px; margin-bottom:10px;">
			<div style="min-width:100px;"><b>${__(label)}:</b></div>
			<code style="flex:1; word-break:break-all;">${frappe.utils.escape_html(value)}</code>
			<button type="button" class="btn btn-xs btn-default copy-agent-credential" data-field="${field}">
				${frappe.utils.icon("es-line-copy", "sm")} ${__("Copy")}
			</button>
		</div>`;

	const dialog = new frappe.ui.Dialog({
		title: __("Agent Key Generated"),
		fields: [
			{
				fieldtype: "HTML",
				options: `
					<p>${__("Shown once — copy both into the agent's local config now. They cannot be retrieved again after closing this dialog.")}</p>
					${row("key", "API Key", api_key)}
					${row("secret", "API Secret", api_secret)}
				`,
			},
		],
		primary_action_label: __("Close"),
		primary_action() {
			dialog.hide();
		},
	});

	// Values are read back from the `values` closure, not the DOM, so
	// there's no round trip through the HTML-escaped attribute text.
	dialog.$wrapper.on("click", ".copy-agent-credential", (e) => {
		const field = $(e.currentTarget).attr("data-field");
		frappe.utils.copy_to_clipboard(values[field]);
	});

	dialog.show();
}
