app_name = "alfaedge_pulse"
app_title = "alfaEdge Pulse"
app_publisher = "AlfaEdge"
app_description = "Standalone real-time monitoring dashboard for Proxmox VE hosts, VMs, CTs, and Proxmox Backup Server"
app_email = "rifazmohammed@gmail.com"
app_license = "mit"


# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "alfaedge_pulse",
# 		"logo": "/assets/alfaedge_pulse/logo.png",
# 		"title": "alfaEdge Pulse",
# 		"route": "/alfaedge_pulse",
# 		"has_permission": "alfaedge_pulse.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/alfaedge_pulse/css/alfaedge_pulse.css"
# app_include_js = "/assets/alfaedge_pulse/js/alfaedge_pulse.js"

# include js, css files in header of web template
# web_include_css = "/assets/alfaedge_pulse/css/alfaedge_pulse.css"
# web_include_js = "/assets/alfaedge_pulse/js/alfaedge_pulse.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "alfaedge_pulse/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "alfaedge_pulse/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "alfaedge_pulse.utils.jinja_methods",
# 	"filters": "alfaedge_pulse.utils.jinja_filters"
# }

# Installation
# ------------

after_install = "alfaedge_pulse.install.after_install"

# Starts (or confirms) the background poll loops after every migrate, so
# they come back up on their own after a bench update/restart without a
# manual step.
after_migrate = [
	"alfaedge_pulse.tasks.poller.ensure_poller_running",
	"alfaedge_pulse.tasks.uptime_kuma_poller.ensure_uptime_poller_running",
]

# Uninstallation
# ------------

# before_uninstall = "alfaedge_pulse.uninstall.before_uninstall"
# after_uninstall = "alfaedge_pulse.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "alfaedge_pulse.utils.before_app_install"
# after_app_install = "alfaedge_pulse.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "alfaedge_pulse.utils.before_app_uninstall"
# after_app_uninstall = "alfaedge_pulse.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "alfaedge_pulse.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "alfaedge_pulse.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

permission_query_conditions = {
	"Alert Subscription": "alfaedge_pulse.proxmox_fleet_monitor.doctype.alert_subscription.alert_subscription.get_permission_query_conditions",
}

has_permission = {
	"Alert Subscription": "alfaedge_pulse.proxmox_fleet_monitor.doctype.alert_subscription.alert_subscription.has_permission",
}

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

# The actual sub-minute polling (Proxmox and Uptime both) happens inside
# background jobs that loop for up to MAX_LOOP_SECONDS at a time (see
# alfaedge_pulse.tasks.poller and .tasks.uptime_kuma_poller) rather than
# here — Frappe's scheduler can't go below one-minute granularity. These
# two cron entries are watchdogs: every minute (the fastest Frappe's
# scheduler allows) each checks whether its own loop's heartbeat is still
# fresh, and (re)starts it if not — both after a crash/restart, and after
# each bounded loop invocation's normal, expected exit.
scheduler_events = {
	"cron": {
		"* * * * *": [
			"alfaedge_pulse.tasks.poller.ensure_poller_running",
			"alfaedge_pulse.tasks.uptime_kuma_poller.ensure_uptime_poller_running",
			# A plain, self-throttled function (not a bounded loop) — checks
			# its own Settings doctype's interval and no-ops most ticks. See
			# its module docstring for why a fixed */N cron key isn't used
			# instead: it would make the interval un-editable from Desk
			# without a code change. Minutes-granularity is fine for this
			# one (unlike polling, nothing needs a sub-minute Bifrost sync).
			"alfaedge_pulse.tasks.bifrost_sync.sync_bifrost_logs",
			# Host Health is push-based (an agent posts to it), not polled —
			# nothing here to (re)start. This is purely a staleness check
			# (Host Unreachable / Scheduler Stalled) — see its own module
			# docstring for why once-a-minute is already enough, no bounded
			# loop needed.
			"alfaedge_pulse.tasks.host_health_watchdog.check_host_heartbeats",
		],
		# Host Health's own alert types (Service Down, Worker Degraded,
		# Failed Job Threshold, Long Running Job, Scheduler Stalled, Host
		# Unreachable) don't send immediately — see alerts/dispatch.py's
		# DIGEST_ONLY_ALERT_TYPES — they're batched into these three
		# fixed-time summaries instead. The hour boundaries here must match
		# alerts/digest.py's MIDDAY_HOUR/EVENING_HOUR constants.
		"0 7 * * *": ["alfaedge_pulse.alerts.digest.send_health_digest_morning"],
		"0 13 * * *": ["alfaedge_pulse.alerts.digest.send_health_digest_midday"],
		"0 20 * * *": ["alfaedge_pulse.alerts.digest.send_health_digest_evening"],
	},
	"daily": [
		# Caps Uptime Check Log's growth — see the function's own docstring
		# for why this exists (LLM Usage Log already hit this exact problem
		# once). Daily is plenty; this isn't latency-sensitive like polling.
		"alfaedge_pulse.tasks.uptime_kuma_poller.purge_old_check_logs",
		# Same reasoning, applied to Resource Metric Log/Resource Disk
		# Sample — see the function's own docstring.
		"alfaedge_pulse.tasks.resource_monitor.purge_old_resource_logs",
	],
}

# Testing
# -------

# before_tests = "alfaedge_pulse.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "alfaedge_pulse.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "alfaedge_pulse.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "alfaedge_pulse.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["alfaedge_pulse.utils.before_request"]
# after_request = ["alfaedge_pulse.utils.after_request"]

# Job Events
# ----------
# before_job = ["alfaedge_pulse.utils.before_job"]
# after_job = ["alfaedge_pulse.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"alfaedge_pulse.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

