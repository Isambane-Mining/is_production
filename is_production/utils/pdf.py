# apps/is_production/is_production/utils/pdf.py

import frappe
from frappe.utils.pdf import pdf_body_html as _pdf_body_html

def pdf_body_html(jenv, template, print_format, args):
    """
    Wrap Frappe's pdf_body_html to
    1) pull the stored MtD values from the linked Monthly Production Planning into args['doc'],
    2) then render the PDF as normal.

    Read-only on purpose: recalculating (and saving) the MPP here raced the scheduler
    and Hourly Production on_update writers (MariaDB 1020). Those keep the MTD values current.
    """
    doc = args.get("doc")
    if doc and getattr(doc, "month_prod_planning", None):
        from is_production.production.doctype.hourly_production.hourly_production import MPP_MTD_FIELDS

        mpp = frappe.db.get_value(
            "Monthly Production Planning", doc.month_prod_planning, MPP_MTD_FIELDS, as_dict=True
        )
        if mpp:
            for field in MPP_MTD_FIELDS:
                # overwrite the doc's attribute so Jinja will pick it up
                setattr(doc, field, mpp.get(field))

    return _pdf_body_html(jenv, template, print_format, args)
