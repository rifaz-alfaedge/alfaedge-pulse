// Copyright (c) 2026, AlfaEdge and contributors
// For license information, please see license.txt

frappe.ui.form.on("Proxmox Server", {
	refresh(frm) {
		// The "sync_now"/"backfill_resource_history" fields are Button
		// fields (see proxmox_server.json); Frappe fires a `<fieldname>`
		// event on click, which we wire up here instead of in the JSON so
		// we can show progress/result feedback.
		frm.set_df_property("sync_now", "read_only", frm.is_new());
		frm.set_df_property("backfill_resource_history", "read_only", frm.is_new());

		// Delete with archive — disabled servers only, Proxmox Monitor
		// Manager only (enforced server-side too). Runs in the background;
		// see proxmox_fleet_monitor/server_deletion.py.
		if (!frm.is_new() && !frm.doc.enabled && frappe.user.has_role("Proxmox Monitor Manager")) {
			frm.add_custom_button(__("Delete Server…"), () => {
				frappe.prompt(
					{
						fieldname: "confirm_name",
						fieldtype: "Data",
						label: __("Type {0} to confirm", [frm.doc.server_name]),
						reqd: 1,
						description: __(
							"Deletes this server and everything linked to it (VMs/CTs, datastores, backups, alerts, metric history, Host Health agents, sites, NPM). An archive is saved first under Proxmox Server Archive.",
						),
					},
					(values) => {
						frappe.call({
							method: "alfaedge_pulse.proxmox_fleet_monitor.server_deletion.request_server_deletion",
							args: { server: frm.doc.name, confirm_name: values.confirm_name },
							freeze: true,
							callback(r) {
								if (!r.message) return;
								frappe.msgprint({
									title: __("Deletion started"),
									indicator: "orange",
									message: __("{0} You can follow it in {1}.", [
										r.message.message,
										`<a href="/app/proxmox-server-archive/${encodeURIComponent(r.message.archive)}">Proxmox Server Archive</a>`,
									]),
								});
							},
						});
					},
					__("Delete {0}", [frm.doc.server_name]),
					__("Delete"),
				);
			}).addClass("btn-danger");
		}
	},

	backfill_resource_history(frm) {
		if (frm.is_new()) {
			frappe.msgprint(__("Save the server before backfilling."));
			return;
		}
		frappe.dom.freeze(__("Pulling RRD history from {0}...", [frm.doc.hostname]));
		frappe.call({
			method: "alfaedge_pulse.proxmox_fleet_monitor.doctype.proxmox_server.proxmox_server.backfill_resource_history",
			args: { server_name: frm.doc.name },
			callback(r) {
				frappe.dom.unfreeze();
				if (r.message && r.message.ok) {
					frappe.show_alert({ message: r.message.message, indicator: "green" });
				} else {
					frappe.msgprint({
						title: __("Backfill Failed"),
						indicator: "red",
						message: (r.message && r.message.error) || __("Unknown error"),
					});
				}
			},
		});
	},

	sync_now(frm) {
		if (frm.is_new()) {
			frappe.msgprint(__("Save the server before syncing."));
			return;
		}
		frappe.dom.freeze(__("Contacting {0}...", [frm.doc.hostname]));
		frappe.call({
			method: "alfaedge_pulse.proxmox_fleet_monitor.doctype.proxmox_server.proxmox_server.sync_now",
			args: { server_name: frm.doc.name },
			callback(r) {
				frappe.dom.unfreeze();
				if (r.message && r.message.ok) {
					frappe.show_alert({ message: r.message.message, indicator: "green" });
					frm.reload_doc();
				} else {
					frappe.msgprint({
						title: __("Sync Failed"),
						indicator: "red",
						message: (r.message && r.message.error) || __("Unknown error"),
					});
				}
			},
		});
	},
});
