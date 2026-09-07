// Copyright (c) 2026, AlfaEdge and contributors
// For license information, please see license.txt

frappe.ui.form.on("AI Insights Settings", {
	generate_proxmox_report_now() {
		run_report_now("alfaedge_pulse.ai_insights.proxmox_report.run_now");
	},

	generate_host_health_report_now() {
		run_report_now("alfaedge_pulse.ai_insights.host_health_report.run_now");
	},
});

// Shared by both buttons above. The actual work runs as a background job
// (see run_now() in proxmox_report.py/host_health_report.py — an LLM call
// can take longer than a web request should block for), so this call
// returns almost immediately with just an "it's started" acknowledgement,
// not the finished report.
function run_report_now(method) {
	frappe.call({
		method,
		callback(r) {
			if (r.message && r.message.ok) {
				frappe.show_alert({ message: r.message.message, indicator: "green" }, 7);
			} else {
				frappe.msgprint({
					title: __("Report Generation Failed"),
					indicator: "red",
					message: (r.message && r.message.error) || __("Unknown error"),
				});
			}
		},
	});
}
