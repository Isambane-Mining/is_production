import frappe

from frappe.custom.doctype.custom_field.custom_field import (
    create_custom_fields,
)
from frappe.custom.doctype.property_setter.property_setter import (
    make_property_setter,
)


DT = "Surveyed Values"


def execute():
    # ---------------------------------------------------------
    # FINAL SURVEY FIELDS
    #
    # Do NOT create the old hauling_dist_m Float field.
    # The final field is hauling_dist_range (Data).
    # ---------------------------------------------------------

    custom_fields = {
        DT: [
            {
                "fieldname": "from_area",
                "label": "FROM AREA",
                "fieldtype": "Data",
                "insert_after": "handling_method",
                "reqd": 1,
                "in_list_view": 1,
                "hidden": 0,
                "columns": 0,
                "allow_on_submit": 0,
            },
            {
                "fieldname": "to_area",
                "label": "TO AREA",
                "fieldtype": "Data",
                "insert_after": "from_area",
                "reqd": 1,
                "in_list_view": 1,
                "hidden": 0,
                "columns": 0,
                "allow_on_submit": 0,
            },
            {
                "fieldname": "hauling_dist_range",
                "label": "HAULING DIST(m)",
                "fieldtype": "Data",
                "insert_after": "to_area",
                "reqd": 1,
                "in_list_view": 1,
                "hidden": 0,
                "columns": 2,
                "allow_on_submit": 0,
            },
        ]
    }

    create_custom_fields(
        custom_fields,
        update=True,
    )

    # ---------------------------------------------------------
    # MANDATORY STANDARD FIELDS
    #
    # Exact LAB Property Setters:
    # mat_type
    # mat_type_ref
    # bcm
    # ---------------------------------------------------------

    for fieldname in (
        "mat_type",
        "mat_type_ref",
        "bcm",
    ):
        make_property_setter(
            DT,
            fieldname,
            "reqd",
            1,
            "Check",
        )

    # ---------------------------------------------------------
    # LEGACY SAFETY
    #
    # LAB has an old hidden Float field from development.
    # LIVE should not get it.
    #
    # If another database already happens to contain it,
    # hide it rather than deleting any database column/data.
    # ---------------------------------------------------------

    legacy_name = frappe.db.get_value(
        "Custom Field",
        {
            "dt": DT,
            "fieldname": "hauling_dist_m",
        },
        "name",
    )

    if legacy_name:
        frappe.db.set_value(
            "Custom Field",
            legacy_name,
            {
                "reqd": 0,
                "hidden": 1,
                "in_list_view": 0,
            },
            update_modified=False,
        )

    frappe.clear_cache(
        doctype=DT
    )

    frappe.clear_cache(
        doctype="Survey"
    )
