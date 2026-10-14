app_name = "is_production"
app_title = "Production"
app_publisher = "Isambane Mining (Pty) Ltd"
app_description = "Isambane Mining Frappe App for Production Records"
app_email = "eben@isambane.co.za"
app_license = "mit"
required_apps = ["frappe/erpnext", "shridarpatil/frappe_whatsapp"]
source_link = "http://github.com/buff0k/is_production"
app_logo_url = "/assets/is_production/images/is-logo.png"
app_home = "/desk/production"
add_to_apps_screen = [
    {
        "name": app_name,
        "logo": "/assets/is_production/images/is-logo.png",
        "title": app_title,
        "route": app_home,
        "has_permission": "is_production.production.utils.check_app_permission",
    }
]
fixtures = [
    {"dt": "Role", "filters": [["name", "in", ["Production Manager", "Production User", "Production Supervisor", "Production Foreman", "Engineering Supervisor", "Control Clerk", "External Surveryor", "Drill Supervisor"]]]},
    {"dt": "Custom DocPerm", "filters": [["role", "in", ["Production Manager", "Production User", "Production Supervisor", "Production Foreman", "Engineering Supervisor", "Control Clerk", "External Surveryor", "Drill Supervisor"]]]},
]

# Asset Categories (Dozer, ADT, Excavator) are not a fixture: they need
# company-specific accounts. They are created against the site's default
# company by is_production.setup.asset_categories instead.
after_install = "is_production.setup.asset_categories.ensure_asset_categories"
after_migrate = "is_production.setup.asset_categories.ensure_asset_categories"

override_whitelisted_methods = {
    # Override the PDF‐body renderer
    "frappe.utils.pdf.pdf_body_html": "is_production.utils.pdf.pdf_body_html"
}

# ------------------------------------------------------------------------
# Include external JS/CSS in Desk <head>
# ------------------------------------------------------------------------

# Load Sortable.js from CDN across all Desk pages
# production_dependencies.bundle.js (three.js, SortableJS) is loaded with frappe.require by the
# pages and forms that use it, not on every desk page
app_include_js = [
    "/assets/is_production/js/hourly_production_ui.js"
]

# ------------------------------------------------------------------------
# Include doctype-specific JS
# ------------------------------------------------------------------------

doctype_js = {
    "Monthly Production Planning": "production/doctype/monthly_production_planning/monthly_production_planning.js"
}

app_include_css = [
    "/assets/is_production/css/dashboards.css"
]

page_js = {
    "production-dashboard": "public/js/production_dashboard.js",
}

scheduler_events = {
    "hourly": [
        "is_production.production.doctype.monthly_production_planning.monthly_production_planning.update_all_active_mpp_mtd",
    ],

    "cron": {
        # Current-period summaries: stagger after backups and existing reports.
        "22 * * * *": [
            "is_production.production.controllers.production_summary_scheduler.create_hourly_summaries",
        ],
        "37 6 * * *": [
            "is_production.production.controllers.production_summary_scheduler.create_daily_summaries",
        ],
        # Historical recovery: one bounded batch per type, no auto-continuations.
        "37 2 * * *": [
            "is_production.production.controllers.production_summary_scheduler.recover_hourly_summaries",
        ],
        "43 2 * * *": [
            "is_production.production.controllers.production_summary_scheduler.recover_shift_summaries",
        ],
        "49 2 * * *": [
            "is_production.production.controllers.production_summary_scheduler.recover_daily_summaries",
        ],
        "27 6,18 * * *": [
            "is_production.production.controllers.production_summary_scheduler.create_shift_summaries",
        ],
        # DAILY 05:55 - CEO Dashboard 1 PDF snapshot email
        "55 5 * * *": [
            "is_production.production.controllers.notifications.send_ceo_dashboard_daily_emails",
        ],

        # Monday 04:00 - create new week
        "0 4 * * MON": [
            "is_production.production.doctype.production_efficiency.production_efficiency.create_weekly_records"
        ],

        # Daily 06:00 - update current week
        "0 6 * * *": [
            "is_production.production.doctype.production_efficiency.production_efficiency.update_weekly_records"
        ],

        # SUNDAY 14:30 - extra update to bring in production (requested)
        "30 14 * * SUN": [
            "is_production.production.doctype.production_efficiency.production_efficiency.update_weekly_records"
        ],

        # SUNDAY 15:00 - close previous week (requested)
        "0 15 * * SUN": [
            "is_production.production.doctype.production_efficiency.production_efficiency.close_off_weekly_records"
        ],

        # SUNDAY 15:05 - send weekly Production Efficiency LINK emails (requested)
        "5 15 * * SUN": [
            "is_production.production.controllers.notifications.send_production_efficiency_weekly_emails",
        ],
    }
}

standard_portal_menu_items = [
	{
		"title": "Capture Survey",
		"route": "/survey_portal",
		"reference_doctype": "Survey",
		"role": "External Surveyor",
	},
	{
		"title": "My Surveys",
		"route": "/survey_portal_list",
		"reference_doctype": "Survey",
		"role": "External Surveyor",
	}
]

# BEGIN DEFINE MONTHLY PRODUCTION AUTO SYNC

_define_monthly_sync_handler = (
    "is_production.production.doctype."
    "define_monthly_production.auto_sync."
    "sync_monthly_production_plan"
)

_define_monthly_remove_handler = (
    "is_production.production.doctype."
    "define_monthly_production.auto_sync."
    "remove_monthly_production_plan"
)

if "doc_events" not in globals():
    doc_events = {}

_mpp_doc_events = doc_events.setdefault(
    "Monthly Production Planning",
    {},
)


def _add_mpp_doc_event(event, handler):
    existing = _mpp_doc_events.get(event)

    if not existing:
        _mpp_doc_events[event] = handler
        return

    if isinstance(existing, (list, tuple)):
        values = list(existing)

        if handler not in values:
            values.append(handler)

        _mpp_doc_events[event] = values
        return

    if existing != handler:
        _mpp_doc_events[event] = [
            existing,
            handler,
        ]


_add_mpp_doc_event(
    "after_insert",
    _define_monthly_sync_handler,
)

_add_mpp_doc_event(
    "on_update",
    _define_monthly_sync_handler,
)

_add_mpp_doc_event(
    "on_cancel",
    _define_monthly_remove_handler,
)

_add_mpp_doc_event(
    "on_trash",
    _define_monthly_remove_handler,
)

# END DEFINE MONTHLY PRODUCTION AUTO SYNC
