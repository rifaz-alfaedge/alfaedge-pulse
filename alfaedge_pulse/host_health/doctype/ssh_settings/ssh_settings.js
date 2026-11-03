// Copyright (c) 2026, AlfaEdge and contributors
// For license information, please see license.txt

const SSH = "alfaedge_pulse.host_health.doctype.ssh_settings.ssh_settings";

frappe.ui.form.on("SSH Settings", {
	generate_key(frm) {
		frappe.confirm(
			__("This replaces the current key. Hosts keep working only after the new public key is added to Warpgate (or authorized_keys). Continue?"),
			() => {
				frappe.call({
					method: `${SSH}.generate_key_pair`,
					freeze: true,
					callback() {
						frm.reload_doc();
					},
				});
			}
		);
	},

	import_key(frm) {
		const d = new frappe.ui.Dialog({
			title: __("Import Private Key"),
			fields: [
				{ fieldname: "private_key", fieldtype: "Code", label: __("Private key (PEM / OpenSSH)"), reqd: 1 },
				{ fieldname: "passphrase", fieldtype: "Password", label: __("Passphrase (if any)") },
			],
			primary_action_label: __("Import"),
			primary_action(values) {
				frappe.call({
					method: `${SSH}.import_private_key`,
					args: values,
					freeze: true,
					callback() {
						d.hide();
						frm.reload_doc();
					},
				});
			},
		});
		d.show();
	},

	reset_host_key(frm) {
		frappe.confirm(__("Forget the pinned Warpgate host key? The next connection will pin whatever key it sees."), () => {
			frappe.call({ method: `${SSH}.reset_pinned_key`, callback: () => frm.reload_doc() });
		});
	},

	test_connection(frm) {
		if (!frm.doc.test_host) {
			frappe.msgprint(__("Pick a host to test first."));
			return;
		}
		frappe.call({
			method: `${SSH}.test_connection`,
			args: { monitored_host: frm.doc.test_host },
			freeze: true,
			freeze_message: __("Connecting..."),
			callback(r) {
				const m = r.message || {};
				if (m.ok) {
					frappe.msgprint({
						title: __("Connected"),
						indicator: "green",
						message: __("Services: {0}, benches: {1}, sites: {2}, disks: {3}", [m.services, m.benches, m.sites, m.disks]) +
							(m.errors && m.errors.length ? "<br>" + frappe.utils.escape_html(m.errors.join("; ")) : ""),
					});
				} else {
					frappe.msgprint({ title: __("Failed ({0})", [m.kind]), indicator: "red", message: frappe.utils.escape_html(m.message || "") });
				}
			},
		});
	},
});
