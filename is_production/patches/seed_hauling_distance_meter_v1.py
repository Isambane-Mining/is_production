import frappe


DOCTYPE = "Hauling Distance Meter"


def execute():
    """
    Seed the standard Hauling Distance Meter master data.

    Canonical records:
    - 500 cumulative ranges: 0-10 through 0-5000, every 10 m
    - 9 band ranges: 500-1000 through 4500-5000, every 500 m

    Safe to execute repeatedly:
    - missing records are inserted
    - existing canonical records are updated
    - unrelated/additional records are not deleted
    """

    rows = []

    # 500 cumulative ranges:
    # 0-10, 0-20, ... 0-5000
    for to_distance in range(10, 5001, 10):
        rows.append(
            {
                "name": f"0-{to_distance}",
                "hauling_distance": f"0-{to_distance}",
                "from_distance": 0,
                "to_distance": to_distance,
                "range_type": "Cumulative",
                "is_active": 1,
            }
        )

    # 9 fixed bands:
    # 500-1000, 1000-1500, ... 4500-5000
    for from_distance in range(500, 4501, 500):
        to_distance = from_distance + 500

        rows.append(
            {
                "name": f"{from_distance}-{to_distance}",
                "hauling_distance": f"{from_distance}-{to_distance}",
                "from_distance": from_distance,
                "to_distance": to_distance,
                "range_type": "Band",
                "is_active": 1,
            }
        )

    if len(rows) != 509:
        frappe.throw(
            f"Expected 509 Hauling Distance Meter seed rows, got {len(rows)}."
        )

    for row in rows:
        name = row["name"]

        values = {
            "hauling_distance": row["hauling_distance"],
            "from_distance": row["from_distance"],
            "to_distance": row["to_distance"],
            "range_type": row["range_type"],
            "is_active": row["is_active"],
        }

        if frappe.db.exists(DOCTYPE, name):
            frappe.db.set_value(
                DOCTYPE,
                name,
                values,
                update_modified=False,
            )
            continue

        doc = frappe.get_doc(
            {
                "doctype": DOCTYPE,
                **row,
            }
        )

        doc.insert(ignore_permissions=True)
