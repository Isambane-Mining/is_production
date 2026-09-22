import json
import frappe
from frappe import _
from frappe.utils import flt, now_datetime


def get_context(context):
    if frappe.session.user == "Guest":
        frappe.local.flags.redirect_location = "/login"
        raise frappe.Redirect

    validate_supplier_user()

    context.no_cache = 1
    context.title = "Survey Portal"

    context.locations = frappe.get_all(
        "Location",
        fields=["name"],
        order_by="name asc",
        limit_page_length=500,
    )

    context.draft_name = frappe.form_dict.get("draft")
    context.draft = None
    context.rows = []
    context.shift_date_value = ""
    context.survey_datetime_value = ""

    if context.draft_name:
        context.draft = get_supplier_draft(context.draft_name)

        context.rows = [
            {
                "mat_type": row.mat_type or "",
                "material_type_ref": row.mat_type_ref or "",
                "from_area": row.from_area or "",
                "to_area": row.to_area or "",
                "hauling_dist_range": row.hauling_dist_range or "",
                "handling_method": row.handling_method or "",
                "bcm": flt(row.bcm),
                "rd": flt(row.rd),
                "metric_tonnes": flt(row.metric_tonnes),
            }
            for row in (context.draft.surveyed_values or [])
        ]

        if context.draft.last_production_shift_start_date:
            context.shift_date_value = str(context.draft.last_production_shift_start_date)

        if context.draft.survey_datetime:
            context.survey_datetime_value = context.draft.survey_datetime.strftime("%Y-%m-%dT%H:%M")

    context.rows_json = frappe.as_json(context.rows)

    return context


def get_supplier_draft(draft_name):
    survey = frappe.get_doc("Survey", draft_name)

    if survey.docstatus != 0:
        frappe.throw(_("Submitted surveys cannot be edited from the portal."))

    if survey.owner != frappe.session.user and "System Manager" not in frappe.get_roles(frappe.session.user):
        frappe.throw(_("Not permitted"), frappe.PermissionError)

    return survey


@frappe.whitelist(allow_guest=False)
def save_survey_draft(
    draft_name=None,
    last_production_shift_start_date=None,
    location=None,
    sub_site_description=None,
    survey_datetime=None,
    survey_report_notes=None,
    surveyed_values=None,
):
    validate_supplier_user()

    rows = normalize_surveyed_values(surveyed_values)
    totals = calculate_totals_from_rows(rows)

    if draft_name:
        draft = get_supplier_draft(draft_name)
        if draft.docstatus != 0:
            frappe.throw(_("Submitted surveys cannot be edited."))
    else:
        draft = frappe.new_doc("Survey")

    draft.last_production_shift_start_date = last_production_shift_start_date
    draft.location = location
    draft.sub_site_description = sub_site_description
    draft.survey_datetime = survey_datetime or now_datetime()
    draft.survey_report_notes = survey_report_notes

    draft.set("surveyed_values", [])
    for row in rows:
        draft.append("surveyed_values", row)

    draft.total_surveyed_bcm = totals["total_surveyed_bcm"]
    draft.total_ts_bcm = totals["total_ts_bcm"]
    draft.total_dozing_bcm = totals["total_dozing_bcm"]
    draft.total_surveyed_coal_tons = totals["total_surveyed_coal_tons"]

    draft.save(ignore_permissions=True)
    frappe.db.commit()

    return {
        "ok": True,
        "draft_name": draft.name,
        "totals": totals,
    }


@frappe.whitelist(allow_guest=False)
def send_survey_to_erp(draft_name):
    validate_supplier_user()

    survey = get_supplier_draft(draft_name)

    if survey.docstatus != 0:
        frappe.throw(_("This survey is already submitted."))

    if not survey.surveyed_values:
        frappe.throw(_("Add at least one Surveyed Values row before submitting."))

    survey.submit()
    frappe.db.commit()

    return {
        "ok": True,
        "survey_name": survey.name,
    }


def normalize_surveyed_values(surveyed_values):
    if not surveyed_values:
        return []

    if isinstance(surveyed_values, str):
        raw_rows = json.loads(surveyed_values)
    else:
        raw_rows = surveyed_values

    clean_rows = []

    for raw in raw_rows or []:
        mat_type = str(
            raw.get("mat_type") or ""
        ).strip()

        material_type_ref = str(
            raw.get("material_type_ref") or ""
        ).strip()

        from_area = str(
            raw.get("from_area") or ""
        ).strip()

        to_area = str(
            raw.get("to_area") or ""
        ).strip()

        hauling_dist_range = str(
            raw.get("hauling_dist_range") or ""
        ).strip()

        handling_method = str(
            raw.get("handling_method") or ""
        ).strip()

        bcm_raw = raw.get("bcm")
        rd_raw = raw.get("rd")

        has_any_value = any([
            mat_type,
            material_type_ref,
            from_area,
            to_area,
            hauling_dist_range,
            handling_method,
            str(bcm_raw or "").strip(),
            str(rd_raw or "").strip(),
        ])

        if not has_any_value:
            continue

        # ----------------------------------------------------
        # MANDATORY PORTAL FIELDS
        # ----------------------------------------------------

        if not mat_type:
            frappe.throw(
                _("Each Surveyed Values row needs a Material Type.")
            )

        if not material_type_ref:
            frappe.throw(
                _("Each Surveyed Values row needs a Material Type Ref.")
            )

        if not from_area:
            frappe.throw(
                _("Each Surveyed Values row needs a FROM AREA.")
            )

        if not to_area:
            frappe.throw(
                _("Each Surveyed Values row needs a TO AREA.")
            )

        if not hauling_dist_range:
            frappe.throw(
                _("Each Surveyed Values row needs a HAULING DIST(m).")
            )

        if not handling_method:
            frappe.throw(
                _("Each Surveyed Values row needs a Handling Method.")
            )

        if bcm_raw in (None, ""):
            frappe.throw(
                _("Each Surveyed Values row needs a BCM value.")
            )

        if rd_raw in (None, ""):
            frappe.throw(
                _("Each Surveyed Values row needs an RD value.")
            )

        # ----------------------------------------------------
        # EXISTING CALCULATION - UNCHANGED
        # ----------------------------------------------------

        bcm = flt(bcm_raw)
        rd = flt(rd_raw)

        metric_tonnes = round(
            bcm * rd,
            1,
        )

        clean_rows.append({
            "mat_type": mat_type,
            "mat_type_ref": material_type_ref,
            "from_area": from_area,
            "to_area": to_area,
            "hauling_dist_range": hauling_dist_range,
            "handling_method": handling_method,
            "bcm": bcm,
            "rd": rd,
            "metric_tonnes": metric_tonnes,
        })

    return clean_rows


def calculate_totals_from_rows(rows):
    total_surveyed_bcm = 0
    total_ts_bcm = 0
    total_dozing_bcm = 0
    total_surveyed_coal_tons = 0

    for row in rows:
        bcm = flt(row.get("bcm"))
        mt = flt(row.get("metric_tonnes"))

        total_surveyed_bcm += bcm

        if row.get("handling_method") == "Truck and Shovel":
            total_ts_bcm += bcm
        elif row.get("handling_method") == "Dozing":
            total_dozing_bcm += bcm

        if row.get("mat_type") == "Coal":
            total_surveyed_coal_tons += mt

    return {
        "total_surveyed_bcm": total_surveyed_bcm,
        "total_ts_bcm": total_ts_bcm,
        "total_dozing_bcm": total_dozing_bcm,
        "total_surveyed_coal_tons": total_surveyed_coal_tons,
    }


def get_plan_and_hourly_ref(shift_date, location):
    if not shift_date or not location:
        return None, None

    plans = frappe.get_all(
        "Monthly Production Planning",
        fields=["name"],
        filters=[
            ["location", "=", location],
            ["prod_month_start_date", "<=", shift_date],
            ["prod_month_end_date", ">=", shift_date],
        ],
        order_by="prod_month_start_date asc",
        limit_page_length=1,
    )

    if not plans:
        return None, None

    plan_name = plans[0].name
    hourly_ref = None

    mpp = frappe.get_doc("Monthly Production Planning", plan_name)
    for row in (mpp.month_prod_days or []):
        if str(row.shift_start_date) == str(shift_date):
            hourly_ref = row.hourly_production_reference or ""
            break

    return plan_name, hourly_ref


def validate_supplier_user():
    if frappe.session.user == "Guest":
        frappe.throw(_("Login required"), frappe.PermissionError)

    roles = set(frappe.get_roles(frappe.session.user))
    allowed_roles = {"External Surveyor", "System Manager"}

    if not roles.intersection(allowed_roles):
        frappe.throw(_("Not permitted"), frappe.PermissionError)