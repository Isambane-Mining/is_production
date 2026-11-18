# Copyright (c) 2026, Isambane Mining (Pty) Ltd
# Hourly Dashboard – Hourly Excavator Production
#


import frappe
from datetime import timedelta
from frappe.utils import getdate, now_datetime


# =========================================================
# OPERATIONAL DAY (06:00 -> 05:59)
# =========================================================
@frappe.whitelist()
def get_operational_day():
    now = now_datetime()
    return now.date() - timedelta(days=1) if now.hour < 6 else now.date()


SITE_HEADER_COLOURS = {
    "Klipfontein": "#EBF9FF",
    "Gwab": "#f7d8ff",
    "Kriel Rehabilitation": "#E6D3B1",
    "Koppie": "#feff8d",
    "Uitgevallen": "#ffd37f",
    "Bankfontein": "#e3e3e3",
}

SLOT_LABELS = [
    "06-07", "07-08", "08-09", "09-10", "10-11", "11-12", "12-13",
    "13-14", "14-15", "15-16", "16-17", "17-18",
    "18-19", "19-20", "20-21", "21-22", "22-23", "23-24",
    "24-01", "01-02", "02-03", "03-04", "04-05", "05-06"
]

def get_hour_slot_number(hour_slot):
    """Return dashboard slot 1-24 for padded or unpadded Hourly Production slots."""
    if not hour_slot:
        return None

    try:
        start_text = str(hour_slot).split("-", 1)[0]
        start_hour = int(start_text.split(":", 1)[0])
    except (ValueError, IndexError):
        return None

    if 6 <= start_hour <= 23:
        return start_hour - 5

    if 0 <= start_hour <= 5:
        return start_hour + 19

    return None

SITE_ORDER = [
    "Klipfontein",
    "Uitgevallen",
    "Gwab",
    "Koppie",
    "Kriel Rehabilitation",
    "Bankfontein",
]


def execute(filters=None):
    filters = filters or {}

    columns = get_columns()

    prod_date = get_selected_production_day(
        filters.get("production_date")
    )
    active_sites = get_active_planning_sites(prod_date)

    excavators_by_site = get_all_excavators()
    hourly_data = get_all_hourly_data(prod_date)

    data = []
    ordered_sites = [site for site in SITE_ORDER if site in active_sites]
    ordered_sites.extend(sorted(set(active_sites) - set(SITE_ORDER)))
    for site_order, site in enumerate(ordered_sites):

        excavators = excavators_by_site.get(site, [])
        site_data = hourly_data.get(site, {})
        header_colour = SITE_HEADER_COLOURS.get(site, "#FFFFFF")

        # Preserve the old dashboard behaviour: a site with a valid plan
        # should still render, even if there are no excavators for that site.
        if not excavators:
            data.append(build_data_row(
                site=site,
                site_order=site_order,
                prod_date=prod_date,
                header_colour=header_colour,
                excavator="",
                slot_values={},
                is_empty_site=1
            ))
            continue

        for excavator in excavators:
            data.append(build_data_row(
                site=site,
                site_order=site_order,
                prod_date=prod_date,
                header_colour=header_colour,
                excavator=excavator,
                slot_values=site_data.get(excavator, {}),
                is_empty_site=0
            ))

    return columns, data


def get_selected_production_day(production_date=None):
    return getdate(production_date) if production_date else get_operational_day()


def get_active_planning_sites(prod_date):
    """Map each covered site to its latest modified non-cancelled plan.

    Coverage is inclusive; name breaks modification timestamp ties.
    Draft plans remain applicable, matching the existing dashboard behaviour.
    """
    rows = frappe.get_all(
        "Monthly Production Planning",
        filters={
            "prod_month_start_date": ["<=", prod_date],
            "prod_month_end_date": [">=", prod_date],
            "docstatus": ["<", 2],
        },
        fields=["name", "location", "prod_month_end_date", "modified"],
        order_by="modified desc, name desc",
    )

    plans_by_site = {}
    for row in rows:
        if row.location:
            plans_by_site.setdefault(row.location, row)
    return plans_by_site


def get_columns():
    columns = [
        {
            "fieldname": "site_order",
            "label": "Site Order",
            "fieldtype": "Int",
            "width": 80,
            "hidden": 1
        },
        {
            "fieldname": "site",
            "label": "Site",
            "fieldtype": "Data",
            "width": 180
        },
        {
            "fieldname": "production_day",
            "label": "Production Day",
            "fieldtype": "Date",
            "width": 120
        },
        {
            "fieldname": "header_colour",
            "label": "Header Colour",
            "fieldtype": "Data",
            "width": 120,
            "hidden": 1
        },
        {
            "fieldname": "is_empty_site",
            "label": "Empty Site",
            "fieldtype": "Check",
            "width": 80,
            "hidden": 1
        },
        {
            "fieldname": "excavator",
            "label": "Excavator",
            "fieldtype": "Data",
            "width": 160
        },
    ]

    for idx, label in enumerate(SLOT_LABELS, start=1):
        columns.append({
            "fieldname": f"slot_{idx:02d}",
            "label": label,
            "fieldtype": "Int",
            "width": 80
        })

    return columns


def build_data_row(
    site,
    site_order,
    prod_date,
    header_colour,
    excavator,
    slot_values,
    is_empty_site=0
):
    row = {
        "site_order": site_order,
        "site": site,
        "production_day": prod_date,
        "header_colour": header_colour,
        "is_empty_site": is_empty_site,
        "excavator": excavator,
    }

    for slot in range(1, 25):
        row[f"slot_{slot:02d}"] = int(slot_values.get(str(slot), 0) or 0)

    return row


def get_all_excavators():
    rows = frappe.get_all(
        "Asset",
        filters={
            "asset_category": "Excavator",
            "docstatus": 1
        },
        fields=["name", "location"]
    )

    excavators = {}

    for row in rows:
        if row.location:
            excavators.setdefault(row.location, []).append(row.name)

    # Stable ordering improves both report readability and dashboard rendering.
    for site in excavators:
        excavators[site].sort()

    return excavators


def get_all_hourly_data(prod_date):
    """Load one operational production day: 06:00 through 05:59 next calendar day."""
    prod_date = getdate(prod_date)
    next_date = prod_date + timedelta(days=1)

    rows = frappe.db.sql("""
        SELECT
            hp.prod_date,
            hp.location AS site,
            tl.asset_name_shoval AS excavator,
            hp.hour_slot AS hour_slot,
            SUM(tl.bcms) AS bcm
        FROM `tabHourly Production` hp
        JOIN `tabTruck Loads` tl ON tl.parent = hp.name
        WHERE hp.prod_date IN (%s, %s)
          AND tl.asset_name_shoval IS NOT NULL
        GROUP BY
            hp.prod_date,
            hp.location,
            tl.asset_name_shoval,
            hp.hour_slot
    """, (prod_date, next_date), as_dict=True)

    data = {}

    for row in rows:
        slot = get_hour_slot_number(row.hour_slot)
        if not slot:
            continue

        start_hour = int(str(row.hour_slot).split("-", 1)[0].split(":", 1)[0])

        # Operational production day is 06:00 -> 05:59 next calendar day.
        operational_date = (
            getdate(row.prod_date) - timedelta(days=1)
            if start_hour < 6
            else getdate(row.prod_date)
        )

        if operational_date != prod_date:
            continue

        data.setdefault(row.site, {}).setdefault(row.excavator, {})[str(slot)] = int(row.bcm or 0)

    return data
