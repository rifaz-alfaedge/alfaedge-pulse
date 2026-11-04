// Copyright (c) 2026, AlfaEdge and contributors
// For license information, please see license.txt

const SSH = "alfaedge_pulse.host_health.doctype.ssh_settings.ssh_settings";

frappe.ui.form.on("SSH Settings", {
	refresh(frm) {
		frm.set_intro("");
		frappe.call({
			method: `${SSH}.get_polling_state`,
			callback(r) {
				if (r.message && !r.message.allowed) {
					frm.set_intro(
						__("This server will not poll any host: <b>pulse_ssh_polling</b> is not set in its site_config.json. Set it on the server that should poll (not on copies of production), then restart."),
						"orange"
					);
				}
			},
		});
	},

	add_hosts(frm) {
		let candidates = [];
		const d = new frappe.ui.Dialog({
			title: __("Add Hosts from Proxmox Guests"),
			fields: [
				{ fieldname: "role", fieldtype: "Select", label: __("Server role"), options: "Staging\nDevelopment\nProduction\nBackup", default: "Staging" },
				{ fieldname: "pattern", fieldtype: "Select", label: __("Warpgate target name"), options: "title\nshort\nfull", default: "title",
					description: __("title: altaza-staging.alfaedge.in becomes Altaza-Staging. short: altaza-staging. full: the whole guest name.") },
				{ fieldname: "only_running", fieldtype: "Check", label: __("Only running guests"), default: 1 },
				{ fieldname: "preview_button", fieldtype: "Button", label: __("Preview") },
				{ fieldname: "preview", fieldtype: "HTML" },
			],
			primary_action_label: __("Create (switched off)"),
			primary_action(values) {
				if (!candidates.length) {
					frappe.msgprint(__("Preview first."));
					return;
				}
				frappe.call({
					method: `${SSH}.create_hosts_from_guests`,
					args: { guests: candidates.map((c) => c.guest), role: values.role, only_running: values.only_running ? 1 : 0, pattern: values.pattern },
					freeze: true,
					callback(r) {
						d.hide();
						const m = r.message || {};
						frappe.msgprint(__("Created {0} host(s), all switched off. Add each target in Warpgate, then enable the host.", [m.created]));
					},
				});
			},
		});
		d.fields_dict.preview_button.input.onclick = () => {
			const v = d.get_values();
			frappe.call({
				method: `${SSH}.preview_hosts_from_guests`,
				args: { role: v.role, only_running: v.only_running ? 1 : 0, pattern: v.pattern },
				callback(r) {
					candidates = r.message || [];
					const rows = candidates.map((c) => `<tr><td>${frappe.utils.escape_html(c.guest_name)}</td><td>${frappe.utils.escape_html(c.target)}</td></tr>`).join("");
					d.fields_dict.preview.$wrapper.html(
						candidates.length
							? `<table class="table table-bordered"><thead><tr><th>${__("Guest")}</th><th>${__("Warpgate target")}</th></tr></thead><tbody>${rows}</tbody></table>`
							: `<p class="text-muted">${__("No guests without a Monitored Host.")}</p>`
					);
				},
			});
		};
		d.show();
	},

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
