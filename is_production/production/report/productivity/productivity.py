# Copyright (c) 2025, Isambane Mining (Pty) Ltd
# For license information, please see license.txt

import frappe
from frappe import _

def _execute_original(filters=None):
    filters = frappe._dict(filters or {})
    columns, data, grand_totals = get_columns(), *get_data(filters)
    return columns, data, None, None, get_report_summary(grand_totals)


def get_columns():
    return [
        {"label": _("Label"), "fieldname": "label", "fieldtype": "Data", "width": 220, "group": 1},
        {"label": _("Working Hours"), "fieldname": "working_hours", "fieldtype": "Float", "width": 120},
        {"label": _("Output (BCM)"), "fieldname": "output", "fieldtype": "Data", "width": 150},
        {"label": _("Productivity (BCM/Hr)"), "fieldname": "productivity", "fieldtype": "Float", "width": 160},
    ]


def normalize_category(cat: str) -> str:
    if not cat:
        return ""
    cat = cat.strip().lower()
    if "excavator" in cat:
        return "Excavator"
    if "dozer" in cat or "bulldozer" in cat:
        return "Dozer"
    if "adt" in cat or "truck" in cat or "rigid" in cat:
        return "ADT"
    return cat.title()


def get_shift_field(table_name: str):
    """Check if shift column exists in a table"""
    cols = frappe.db.get_table_columns(table_name)
    if "shift" in cols:
        return "shift"
    if "shift_type" in cols:
        return "shift_type"
    return None


def get_data(filters):
    if not (filters.start_date and filters.end_date and filters.site):
        return [], {"excavator_prods": [], "dozer_prods": []}

    values = {
        "start_date": filters["start_date"],
        "end_date": filters["end_date"],
        "site": filters["site"],
        "machine_type": filters.get("machine_type"),
        "shift": filters.get("shift"),
    }

    # Detect which shift column is available
    hp_shift_col = get_shift_field("Hourly Production")
    pu_shift_col = get_shift_field("Pre-Use Hours")

    shift_condition_hp = f" AND hp.{hp_shift_col} = %(shift)s" if filters.get("shift") and hp_shift_col else ""
    shift_condition_pu = f" AND pu.{pu_shift_col} = %(shift)s" if filters.get("shift") and pu_shift_col else ""
    machine_condition = " AND pa.asset_category = %(machine_type)s" if filters.get("machine_type") else ""

    # 🚛 Truck Loads (ADT + Excavator)
    truck_rows = frappe.db.sql(f"""
        SELECT
            tl.asset_name_shoval AS excavator,
            tl.asset_name_truck AS adt,
            SUM(tl.bcms) AS bcm_output,
            tl.mat_type
        FROM `tabHourly Production` hp
        JOIN `tabTruck Loads` tl ON tl.parent = hp.name
        WHERE hp.prod_date BETWEEN %(start_date)s AND %(end_date)s
          AND hp.location = %(site)s
          {shift_condition_hp}
        GROUP BY tl.asset_name_shoval, tl.asset_name_truck, tl.mat_type
    """, values, as_dict=True)

    # 🛠️ Dozer Production
    dozer_rows = frappe.db.sql(f"""
        SELECT
            dp.asset_name AS dozer,
            SUM(dp.bcm_hour) AS bcm_output,
            dp.dozer_geo_mat_layer AS mat_type
        FROM `tabHourly Production` hp
        JOIN `tabDozer Production` dp ON dp.parent = hp.name
        WHERE hp.prod_date BETWEEN %(start_date)s AND %(end_date)s
          AND hp.location = %(site)s
          {shift_condition_hp}
        GROUP BY dp.asset_name, dp.dozer_geo_mat_layer
    """, values, as_dict=True)

    # ⏱️ Pre-Use Hours
    preuse_rows = frappe.db.sql(f"""
        SELECT
            pa.asset_name,
            pa.asset_category,
            SUM(
                CASE
                    WHEN COALESCE(pa.working_hours, 0) > 0
                    THEN pa.working_hours
                    ELSE 0
                END
            ) AS working_hours
        FROM `tabPre-Use Hours` pu
        JOIN `tabPre-use Assets` pa ON pa.parent = pu.name
        WHERE pu.shift_date BETWEEN %(start_date)s AND %(end_date)s
          AND pu.location = %(site)s
          {shift_condition_pu}
          {machine_condition}
        GROUP BY pa.asset_name, pa.asset_category
    """, values, as_dict=True)

    # 🔄 Normalize Pre-Use Hours
    hours_map = {}
    for r in preuse_rows:
        cat = normalize_category(r.asset_category)
        hours_map[(cat, (r.asset_name or "").strip())] = r.working_hours or 0

    grouped = {"Excavator": {}, "Dozer": {}, "ADT": {}}

    # ➕ Process Trucks (Excavator + ADT)
    for r in truck_rows:
        output_val = r.bcm_output or 0
        # ❌ No coal ton conversion anymore — keep as BCMs only
        if r.adt:
            adt_name = r.adt.strip()
            grouped["ADT"].setdefault(adt_name, {"hours": 0, "output": 0})
            grouped["ADT"][adt_name]["hours"] = hours_map.get(("ADT", adt_name), grouped["ADT"][adt_name]["hours"])
            grouped["ADT"][adt_name]["output"] += output_val
        if r.excavator:
            exc_name = r.excavator.strip()
            grouped["Excavator"].setdefault(exc_name, {"hours": 0, "output": 0})
            grouped["Excavator"][exc_name]["hours"] = hours_map.get(("Excavator", exc_name), grouped["Excavator"][exc_name]["hours"])
            grouped["Excavator"][exc_name]["output"] += output_val

    # ➕ Process Dozers
    for r in dozer_rows:
        output_val = r.bcm_output or 0
        # ❌ No ton-to-BCM conversion for coal
        if r.dozer:
            dz_name = r.dozer.strip()
            grouped["Dozer"].setdefault(dz_name, {"hours": 0, "output": 0})
            grouped["Dozer"][dz_name]["hours"] = hours_map.get(("Dozer", dz_name), grouped["Dozer"][dz_name]["hours"])
            grouped["Dozer"][dz_name]["output"] += output_val

    # Ensure Pre-Use-only machines appear
    for (cat, machine), hrs in hours_map.items():
        if cat in grouped:
            grouped[cat].setdefault(machine, {"hours": hrs, "output": 0})
            if grouped[cat][machine]["hours"] == 0:
                grouped[cat][machine]["hours"] = hrs

    # 📊 Build results
    results = []
    excavator_prods = []
    dozer_prods = []

    for cat in ["Excavator", "ADT", "Dozer"]:
        total_hours = sum(m["hours"] for m in grouped[cat].values())
        total_output = sum(m["output"] for m in grouped[cat].values())

        machine_valid_prods = []
        for machine, info in grouped[cat].items():
            productivity = round(info["output"] / info["hours"], 2) if info["hours"] > 0 and info["output"] > 0 else 0
            if productivity > 0:
                machine_valid_prods.append(productivity)
                if cat == "Excavator":
                    excavator_prods.append(productivity)
                elif cat == "Dozer":
                    dozer_prods.append(productivity)

            results.append({
                "label": machine,
                "working_hours": info["hours"],
                "output": f"{info['output']:,.0f}",
                "productivity": productivity,
                "indent": 1,
                "style": "background-color:#f8d7da;" if info["hours"] <= 0 or info["output"] == 0 else ""
            })

        cat_prod = round(sum(machine_valid_prods) / len(machine_valid_prods), 2) if machine_valid_prods else 0

        results.insert(len(results) - len(grouped[cat]), {
            "label": cat,
            "working_hours": total_hours,
            "output": f"{total_output:,.0f}",
            "productivity": cat_prod,
            "indent": 0,
            "style": "background-color:#f8d7da;" if total_hours <= 0 or total_output == 0 else ""
        })

    grand_total = {
        "excavator_prods": excavator_prods,
        "dozer_prods": dozer_prods
    }
    return results, grand_total


def get_report_summary(grand_total):
    excavator_prods = grand_total.get("excavator_prods", [])
    ts_prod = round(sum(excavator_prods) / len(excavator_prods), 2) if excavator_prods else 0

    dozer_prods = grand_total.get("dozer_prods", [])
    dozer_prod = round(sum(dozer_prods) / len(dozer_prods), 2) if dozer_prods else 0

    return [
        {"label": _("Truck + Shovel Productivity (BCM/hr)"), "value": f"{ts_prod:.0f}", "indicator": "Blue"},
        {"label": _("Dozing Productivity (BCM/hr)"), "value": f"{dozer_prod:.0f}", "indicator": "Green"},
    ]


# ============================================================
# KOSI_PRODUCTIVITY_MACHINE_MATERIAL_V1
# Machine per Material view
#
# - Keeps the original Productivity execute() untouched
#   as _execute_original().
# - Default Machine Totals continues using the original logic.
# - Machine per Material splits each machine by material.
# - Working Hours remain the existing machine working hours.
# - Material output rows add back to the original machine output.
# - Hauling Distance (M) intentionally remains blank until
#   the Survey hauling-distance field is added.
# ============================================================

import frappe


_PRODUCTIVITY_CATEGORY_NAMES = {
    "excavator": "Excavator",
    "adt": "ADT",
    "dozer": "Dozer",
}


def _productivity_float(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _productivity_add_material_columns(columns):
    columns = [
        dict(column)
        if hasattr(column, "get")
        else column
        for column in (columns or [])
    ]

    existing = {
        str(column.get("fieldname") or "")
        for column in columns
        if hasattr(column, "get")
    }

    additions = []

    if "material" not in existing:
        additions.append({
            "label": "Material",
            "fieldname": "material",
            "fieldtype": "Data",
            "width": 140,
        })

    if "hauling_distance_m" not in existing:
        additions.append({
            "label": "Hauling Distance (M)",
            "fieldname": "hauling_distance_m",
            "fieldtype": "Float",
            "precision": 0,
            "width": 155,
        })

    if not additions:
        return columns

    insert_at = len(columns)

    for index, column in enumerate(columns):
        if (
            hasattr(column, "get")
            and column.get("fieldname") == "productivity"
        ):
            insert_at = index + 1
            break

    columns[insert_at:insert_at] = additions

    return columns


def _productivity_repack_result(
    original_result,
    columns,
    data,
):
    if isinstance(original_result, tuple):
        return tuple(
            [columns, data]
            + list(original_result[2:])
        )

    if isinstance(original_result, list):
        return [
            columns,
            data,
            *list(original_result[2:]),
        ]

    return columns, data


def _productivity_material_conditions(filters):
    filters = frappe._dict(filters or {})

    from_date = (
        filters.get("from_date")
        or filters.get("start_date")
    )

    to_date = (
        filters.get("to_date")
        or filters.get("end_date")
    )

    site = (
        filters.get("site")
        or filters.get("location")
    )

    shift = filters.get("shift")

    if not from_date or not to_date:
        return None, None

    values = {
        "from_date": from_date,
        "to_date": to_date,
    }

    conditions = [
        "hp.prod_date BETWEEN %(from_date)s AND %(to_date)s",
        "hp.docstatus < 2",
    ]

    if site:
        conditions.append(
            "hp.location = %(site)s"
        )
        values["site"] = site

    if shift:
        conditions.append(
            "hp.shift = %(shift)s"
        )
        values["shift"] = shift

    return " AND ".join(conditions), values


def _productivity_material_output_map(filters):
    """
    Build Machine + Material information using Hourly Production.

    IMPORTANT
    ---------
    Hourly Production does NOT become the final Working Hours source.

    It is used only to determine what percentage of the machine's
    activity belongs to each material.

    Final displayed Working Hours are still distributed from the
    machine's existing Pre-Use Working Hours.

    Example:

        Pre-Use Hours = 12.0

        Hour 1:
            Hards = 180 BCM
            Softs = 60 BCM

        Activity allocation for Hour 1:
            Hards = 0.75 activity hour
            Softs = 0.25 activity hour

    After all hourly records are analysed, the activity-hour
    proportions are scaled so that the final material hours add
    back exactly to the machine's Pre-Use Working Hours.
    """

    result = {
        "Excavator": {},
        "ADT": {},
        "Dozer": {},
    }

    conditions, values = (
        _productivity_material_conditions(filters)
    )

    if not conditions:
        return result

    # ========================================================
    # TRUCK + SHOVEL
    #
    # Keep the Hourly Production document name because it is
    # our one-hour activity bucket.
    # ========================================================

    truck_rows = frappe.db.sql(
        f"""
        SELECT
            hp.name AS hour_key,

            tl.asset_name_shoval AS excavator,
            tl.asset_name_truck AS adt,

            COALESCE(
                NULLIF(TRIM(tl.mat_type), ''),
                'Unassigned'
            ) AS material,

            SUM(
                COALESCE(tl.bcms, 0)
            ) AS raw_output

        FROM `tabHourly Production` hp

        INNER JOIN `tabTruck Loads` tl
            ON tl.parent = hp.name

        WHERE
            {conditions}

        GROUP BY
            hp.name,
            tl.asset_name_shoval,
            tl.asset_name_truck,
            COALESCE(
                NULLIF(TRIM(tl.mat_type), ''),
                'Unassigned'
            )
        """,
        values,
        as_dict=True,
    )

    # ========================================================
    # DOZER
    # ========================================================

    dozer_rows = frappe.db.sql(
        f"""
        SELECT
            hp.name AS hour_key,

            dp.asset_name AS dozer,

            COALESCE(
                NULLIF(
                    TRIM(dp.dozer_geo_mat_layer),
                    ''
                ),
                'Unassigned'
            ) AS material,

            SUM(
                COALESCE(dp.bcm_hour, 0)
            ) AS raw_output

        FROM `tabHourly Production` hp

        INNER JOIN `tabDozer Production` dp
            ON dp.parent = hp.name

        WHERE
            {conditions}

        GROUP BY
            hp.name,
            dp.asset_name,
            COALESCE(
                NULLIF(
                    TRIM(dp.dozer_geo_mat_layer),
                    ''
                ),
                'Unassigned'
            )
        """,
        values,
        as_dict=True,
    )

    # ========================================================
    # INTERNAL STRUCTURE
    #
    # result[category][machine] = {
    #     "materials": {
    #         "Hards": {
    #             "raw_output": 1000,
    #             "activity_hours": 5.5
    #         }
    #     },
    #     "hours": {
    #         "Hourly Production Name": {
    #             "Hards": 180,
    #             "Softs": 60
    #         }
    #     }
    # }
    # ========================================================

    working = {
        "Excavator": {},
        "ADT": {},
        "Dozer": {},
    }

    def add_hourly(
        category,
        machine,
        hour_key,
        material,
        raw_output,
    ):
        machine = str(
            machine or ""
        ).strip()

        hour_key = str(
            hour_key or ""
        ).strip()

        material = (
            str(material or "").strip()
            or "Unassigned"
        )

        bcm = _productivity_float(
            raw_output
        )

        if not machine or not hour_key:
            return

        machine_data = (
            working[category]
            .setdefault(
                machine,
                {
                    "materials": {},
                    "hours": {},
                },
            )
        )

        material_data = (
            machine_data["materials"]
            .setdefault(
                material,
                {
                    "raw_output": 0.0,
                    "activity_hours": 0.0,
                },
            )
        )

        material_data["raw_output"] += bcm

        hour_data = (
            machine_data["hours"]
            .setdefault(
                hour_key,
                {},
            )
        )

        hour_data[material] = (
            hour_data.get(
                material,
                0.0,
            )
            + bcm
        )

    # ========================================================
    # EXCAVATOR + ADT HOURLY ACTIVITY
    # ========================================================

    for row in truck_rows or []:
        add_hourly(
            "Excavator",
            row.get("excavator"),
            row.get("hour_key"),
            row.get("material"),
            row.get("raw_output"),
        )

        add_hourly(
            "ADT",
            row.get("adt"),
            row.get("hour_key"),
            row.get("material"),
            row.get("raw_output"),
        )

    # ========================================================
    # DOZER HOURLY ACTIVITY
    # ========================================================

    for row in dozer_rows or []:
        add_hourly(
            "Dozer",
            row.get("dozer"),
            row.get("hour_key"),
            row.get("material"),
            row.get("raw_output"),
        )

    # ========================================================
    # CONVERT EVERY HOURLY BUCKET TO ACTIVITY HOURS
    #
    # One active hourly record = 1.0 activity hour.
    #
    # If only Hards:
    #     Hards = 1.0
    #
    # If Hards 180 + Softs 60:
    #     Hards = 0.75
    #     Softs = 0.25
    #
    # This is only the allocation weight.
    # Final hours still come from Pre-Use.
    # ========================================================

    for category, machines in working.items():

        for machine, machine_data in machines.items():

            materials = machine_data[
                "materials"
            ]

            hour_map = machine_data[
                "hours"
            ]

            for hour_key, hour_materials in hour_map.items():

                positive_values = {
                    material: max(
                        _productivity_float(bcm),
                        0.0,
                    )
                    for material, bcm
                    in hour_materials.items()
                }

                total_bcm = sum(
                    positive_values.values()
                )

                if total_bcm <= 0:
                    continue

                for material, bcm in positive_values.items():

                    if bcm <= 0:
                        continue

                    share = bcm / total_bcm

                    materials[
                        material
                    ][
                        "activity_hours"
                    ] += share

            result[category][machine] = [
                {
                    "material": material,
                    "raw_output": round(
                        _productivity_float(
                            values.get(
                                "raw_output"
                            )
                        ),
                        3,
                    ),
                    "activity_hours": round(
                        _productivity_float(
                            values.get(
                                "activity_hours"
                            )
                        ),
                        6,
                    ),
                }
                for material, values
                in sorted(
                    materials.items(),
                    key=lambda item: item[0],
                )
                if _productivity_float(
                    values.get("raw_output")
                ) > 0
            ]

    return result

def _productivity_normal_rows(data):
    result = []

    for row in data or []:
        if not hasattr(row, "get"):
            result.append(row)
            continue

        new_row = dict(row)

        new_row.setdefault(
            "material",
            "",
        )

        new_row.setdefault(
            "hauling_distance_m",
            "",
        )

        result.append(new_row)

    return result


def _productivity_machine_material_rows(
    data,
    filters,
):
    """
    Produce one report row per Machine + Material.

    FINAL HOURS RULE
    ----------------

    1. Existing machine Working Hours come from Pre-Use.
    2. Hourly Production determines the activity proportion
       for each material.
    3. Pre-Use Hours are distributed using those proportions.
    4. Material hours always add back to the original machine
       Pre-Use Working Hours.

    Material Productivity:

        Material BCM / Allocated Pre-Use Material Hours

    Hauling Distance remains blank until Survey supplies it.
    """

    material_map = (
        _productivity_material_output_map(
            filters
        )
    )

    result = []

    current_category = None

    for row in data or []:

        if not hasattr(row, "get"):
            result.append(row)
            continue

        base_row = dict(row)

        label = str(
            base_row.get("label") or ""
        ).strip()

        category = (
            _PRODUCTIVITY_CATEGORY_NAMES.get(
                label.lower()
            )
        )

        # ====================================================
        # CATEGORY TOTAL
        # ====================================================

        if category:

            current_category = category

            base_row["material"] = ""
            base_row[
                "hauling_distance_m"
            ] = ""

            result.append(
                base_row
            )

            continue

        # ====================================================
        # NON-MACHINE / SAFETY
        # ====================================================

        if (
            not current_category
            or not label
        ):

            base_row["material"] = ""
            base_row[
                "hauling_distance_m"
            ] = ""

            result.append(
                base_row
            )

            continue

        # ====================================================
        # MATERIAL DATA FOR MACHINE
        # ====================================================

        material_parts = (
            material_map
            .get(
                current_category,
                {},
            )
            .get(
                label,
                [],
            )
        )

        # No material production found.
        # Keep original machine row.
        if not material_parts:

            base_row["material"] = ""
            base_row[
                "hauling_distance_m"
            ] = ""

            result.append(
                base_row
            )

            continue

        # ====================================================
        # AUTHORITATIVE PRE-USE HOURS
        # ====================================================

        machine_preuse_hours = (
            _productivity_float(
                base_row.get(
                    "working_hours"
                )
            )
        )

        total_activity_hours = sum(
            _productivity_float(
                part.get(
                    "activity_hours"
                )
            )
            for part in material_parts
        )

        total_material_output = sum(
            _productivity_float(
                part.get(
                    "raw_output"
                )
            )
            for part in material_parts
        )

        # ====================================================
        # ALLOCATE PRE-USE HOURS
        # ====================================================

        allocated_hours_so_far = 0.0

        for index, part in enumerate(
            material_parts
        ):

            new_row = dict(
                base_row
            )

            material_output = round(
                _productivity_float(
                    part.get(
                        "raw_output"
                    )
                ),
                3,
            )

            activity_hours = (
                _productivity_float(
                    part.get(
                        "activity_hours"
                    )
                )
            )

            # ------------------------------------------------
            # Preferred:
            # Hourly activity distribution
            # ------------------------------------------------

            if (
                machine_preuse_hours > 0
                and total_activity_hours > 0
            ):

                if (
                    index
                    == len(material_parts) - 1
                ):
                    material_hours = round(
                        machine_preuse_hours
                        - allocated_hours_so_far,
                        3,
                    )

                else:
                    material_hours = round(
                        machine_preuse_hours
                        * (
                            activity_hours
                            / total_activity_hours
                        ),
                        3,
                    )

                    allocated_hours_so_far += (
                        material_hours
                    )

            # ------------------------------------------------
            # Fallback:
            # If hourly activity cannot be determined,
            # use material output ratio while still ensuring
            # final total = Pre-Use Hours.
            # ------------------------------------------------

            elif (
                machine_preuse_hours > 0
                and total_material_output > 0
            ):

                if (
                    index
                    == len(material_parts) - 1
                ):
                    material_hours = round(
                        machine_preuse_hours
                        - allocated_hours_so_far,
                        3,
                    )

                else:
                    material_hours = round(
                        machine_preuse_hours
                        * (
                            material_output
                            / total_material_output
                        ),
                        3,
                    )

                    allocated_hours_so_far += (
                        material_hours
                    )

            else:
                material_hours = 0.0

            # Protect tiny negative rounding residue.
            if (
                material_hours < 0
                and abs(material_hours) < 0.01
            ):
                material_hours = 0.0

            # =================================================
            # MATERIAL PRODUCTIVITY
            # =================================================

            material_productivity = (
                round(
                    material_output
                    / material_hours,
                    3,
                )
                if material_hours > 0
                else 0.0
            )

            new_row[
                "working_hours"
            ] = material_hours

            new_row[
                "output"
            ] = material_output

            new_row[
                "productivity"
            ] = material_productivity

            new_row[
                "material"
            ] = (
                part.get("material")
                or "Unassigned"
            )

            # Survey integration will come later.
            new_row[
                "hauling_distance_m"
            ] = ""

            result.append(
                new_row
            )

    return result



# ============================================================
# KOSI_PRODUCTIVITY_ACTUAL_BCM_HELPERS_V1
# ============================================================

def _productivity_number(value):
    try:
        return float(
            str(
                value or 0
            ).replace(",", "")
        )
    except (
        TypeError,
        ValueError,
    ):
        return 0.0


def _productivity_hp_category_totals(
    site,
    start_date,
    end_date,
):
    """
    Return Truck + Shovel and Dozing tally BCMs.

    This intentionally uses Hourly Production parent totals,
    matching Monthly Production Planning MTD logic.
    """

    start_date = frappe.utils.getdate(
        start_date
    )

    end_date = frappe.utils.getdate(
        end_date
    )

    if start_date > end_date:
        return {
            "ts": 0.0,
            "dozer": 0.0,
        }

    rows = frappe.db.sql(
        """
        SELECT
            COALESCE(
                SUM(
                    COALESCE(
                        hp.total_ts_bcm,
                        0
                    )
                ),
                0
            ) AS ts,

            COALESCE(
                SUM(
                    COALESCE(
                        hp.total_dozing_bcm,
                        0
                    )
                ),
                0
            ) AS dozer

        FROM `tabHourly Production` hp

        WHERE
            hp.location = %(site)s
            AND hp.prod_date
                BETWEEN %(start_date)s
                    AND %(end_date)s
            AND hp.docstatus < 2
        """,
        {
            "site": site,
            "start_date": start_date,
            "end_date": end_date,
        },
        as_dict=True,
    )

    row = (
        rows[0]
        if rows
        else {}
    )

    return {
        "ts":
            _productivity_number(
                row.get("ts")
            ),

        "dozer":
            _productivity_number(
                row.get("dozer")
            ),
    }


def _productivity_latest_survey(
    site,
    plan_name,
    plan_start,
    end_date,
):
    """
    Latest cumulative Survey up to the selected production date.

    Survey stores cumulative:
        total_ts_bcm
        total_dozing_bcm

    The first query uses Monthly Production Planning reference.
    A site/date fallback supports older Survey records where
    monthly_production_plan_ref may be blank.
    """

    end_date = frappe.utils.getdate(
        end_date
    )

    plan_start = frappe.utils.getdate(
        plan_start
    )

    rows = frappe.db.sql(
        """
        SELECT
            s.name,
            s.last_production_shift_start_date,
            s.total_ts_bcm,
            s.total_dozing_bcm,
            s.survey_datetime

        FROM `tabSurvey` s

        WHERE
            s.location = %(site)s
            AND s.docstatus < 2
            AND s.monthly_production_plan_ref
                = %(plan_name)s
            AND s.last_production_shift_start_date
                IS NOT NULL
            AND s.last_production_shift_start_date
                BETWEEN %(plan_start)s
                    AND %(end_date)s

        ORDER BY
            s.last_production_shift_start_date DESC,
            s.survey_datetime DESC,
            s.creation DESC

        LIMIT 1
        """,
        {
            "site": site,
            "plan_name": plan_name,
            "plan_start": plan_start,
            "end_date": end_date,
        },
        as_dict=True,
    )

    if rows:
        return rows[0]

    # Historical fallback.
    rows = frappe.db.sql(
        """
        SELECT
            s.name,
            s.last_production_shift_start_date,
            s.total_ts_bcm,
            s.total_dozing_bcm,
            s.survey_datetime

        FROM `tabSurvey` s

        WHERE
            s.location = %(site)s
            AND s.docstatus < 2
            AND s.last_production_shift_start_date
                IS NOT NULL
            AND s.last_production_shift_start_date
                BETWEEN %(plan_start)s
                    AND %(end_date)s

        ORDER BY
            s.last_production_shift_start_date DESC,
            s.survey_datetime DESC,
            s.creation DESC

        LIMIT 1
        """,
        {
            "site": site,
            "plan_start": plan_start,
            "end_date": end_date,
        },
        as_dict=True,
    )

    return (
        rows[0]
        if rows
        else None
    )


def _productivity_actual_to_date(
    site,
    plan_name,
    plan_start,
    boundary_date,
):
    """
    Actual BCM through one production date.

    Existing ERP rule:

        Actual =
            latest cumulative Survey
            + Hourly Production after Survey

    If no Survey exists yet:
        Actual = Hourly Production Tallies
    """

    boundary_date = frappe.utils.getdate(
        boundary_date
    )

    plan_start = frappe.utils.getdate(
        plan_start
    )

    if boundary_date < plan_start:
        return {
            "ts": 0.0,
            "dozer": 0.0,
        }

    survey = (
        _productivity_latest_survey(
            site,
            plan_name,
            plan_start,
            boundary_date,
        )
    )

    if not survey:

        return (
            _productivity_hp_category_totals(
                site,
                plan_start,
                boundary_date,
            )
        )

    survey_date = frappe.utils.getdate(
        survey.get(
            "last_production_shift_start_date"
        )
    )

    after_survey_start = (
        frappe.utils.add_days(
            survey_date,
            1,
        )
    )

    after_survey = {
        "ts": 0.0,
        "dozer": 0.0,
    }

    if (
        after_survey_start
        <= boundary_date
    ):
        after_survey = (
            _productivity_hp_category_totals(
                site,
                after_survey_start,
                boundary_date,
            )
        )

    return {
        "ts":
            _productivity_number(
                survey.get(
                    "total_ts_bcm"
                )
            )
            + after_survey["ts"],

        "dozer":
            _productivity_number(
                survey.get(
                    "total_dozing_bcm"
                )
            )
            + after_survey["dozer"],
    }


def _productivity_actual_range_totals(
    filters,
):
    """
    Actual BCM between selected Start Date and End Date.

    Supports a range crossing more than one Monthly
    Production Planning period by calculating each
    overlapping plan separately.
    """

    filters = frappe._dict(
        filters or {}
    )

    site = str(
        filters.get("site")
        or ""
    ).strip()

    start_date = frappe.utils.getdate(
        filters.get("start_date")
    )

    end_date = frappe.utils.getdate(
        filters.get("end_date")
    )

    if (
        not site
        or not start_date
        or not end_date
    ):
        return {
            "ts": 0.0,
            "dozer": 0.0,
            "plans": [],
        }

    plans = frappe.get_all(
        "Monthly Production Planning",
        filters={
            "location": site,
            "prod_month_start_date": [
                "<=",
                end_date,
            ],
            "prod_month_end_date": [
                ">=",
                start_date,
            ],
            "docstatus": [
                "<",
                2,
            ],
        },
        fields=[
            "name",
            "prod_month_start_date",
            "prod_month_end_date",
        ],
        order_by=(
            "prod_month_start_date asc"
        ),
        limit_page_length=0,
    )

    # If no MPP exists, Actual = Tallies.
    if not plans:

        tallies = (
            _productivity_hp_category_totals(
                site,
                start_date,
                end_date,
            )
        )

        return {
            "ts": tallies["ts"],
            "dozer": tallies["dozer"],
            "plans": [],
        }

    total_ts = 0.0
    total_dozer = 0.0

    used_plans = []

    for plan in plans:

        plan_start = frappe.utils.getdate(
            plan.get(
                "prod_month_start_date"
            )
        )

        plan_end = frappe.utils.getdate(
            plan.get(
                "prod_month_end_date"
            )
        )

        range_start = max(
            start_date,
            plan_start,
        )

        range_end = min(
            end_date,
            plan_end,
        )

        if range_start > range_end:
            continue

        end_actual = (
            _productivity_actual_to_date(
                site,
                plan.get("name"),
                plan_start,
                range_end,
            )
        )

        before_start = (
            frappe.utils.add_days(
                range_start,
                -1,
            )
        )

        start_actual = (
            _productivity_actual_to_date(
                site,
                plan.get("name"),
                plan_start,
                before_start,
            )
        )

        total_ts += (
            end_actual["ts"]
            - start_actual["ts"]
        )

        total_dozer += (
            end_actual["dozer"]
            - start_actual["dozer"]
        )

        used_plans.append(
            plan.get("name")
        )

    return {
        "ts": total_ts,
        "dozer": total_dozer,
        "plans": used_plans,
    }


def _productivity_actual_adjustment(
    filters,
):
    """
    Build TS and Dozer adjustment factors.

    The factors are applied proportionally to Machine +
    Material tally rows so machine totals reconcile back
    to the site Actual BCM.
    """

    filters = frappe._dict(
        filters or {}
    )

    site = str(
        filters.get("site")
        or ""
    ).strip()

    start_date = filters.get(
        "start_date"
    )

    end_date = filters.get(
        "end_date"
    )

    tallies = (
        _productivity_hp_category_totals(
            site,
            start_date,
            end_date,
        )
    )

    actual = (
        _productivity_actual_range_totals(
            filters
        )
    )

    ts_factor = (
        actual["ts"]
        / tallies["ts"]
        if tallies["ts"]
        else 1.0
    )

    dozer_factor = (
        actual["dozer"]
        / tallies["dozer"]
        if tallies["dozer"]
        else 1.0
    )

    return {
        "tally_ts":
            tallies["ts"],

        "actual_ts":
            actual["ts"],

        "ts_factor":
            ts_factor,

        "tally_dozer":
            tallies["dozer"],

        "actual_dozer":
            actual["dozer"],

        "dozer_factor":
            dozer_factor,

        "plans":
            actual.get(
                "plans"
            )
            or [],
    }



# ============================================================
# KOSI_PRODUCTIVITY_ASSET_MASTER_CATEGORY_FILTER_V2
#
# PRODUCTIVITY VALID MACHINE CATEGORIES:
#
#     Excavator
#     ADT
#     Dozer
#
# Every machine in the original Productivity result is checked
# against tabAsset before Material rows are generated.
#
# Example:
#
#     Report section = ADT
#     Machine         = IS0123
#     Asset category  = Service Truck
#
# Result:
#     Machine excluded.
#     Its hours excluded from ADT total.
#     Its BCM excluded from ADT total.
#
# This prevents Service Truck / Grader / Bowser / etc. from
# entering Productivity through historical Pre-Use data.
# ============================================================


def _productivity_master_category(value):

    key = str(
        value or ""
    ).strip().lower()

    aliases = {
        "excavator":
            "Excavator",

        "excavators":
            "Excavator",

        "excavator's":
            "Excavator",

        "adt":
            "ADT",

        "adts":
            "ADT",

        "adt's":
            "ADT",

        "articulated dump truck":
            "ADT",

        "articulated dump trucks":
            "ADT",

        "dozer":
            "Dozer",

        "dozers":
            "Dozer",

        "dozer's":
            "Dozer",
    }

    return aliases.get(
        key
    )


def _productivity_master_number(value):

    try:

        return float(
            str(
                value or 0
            ).replace(",", "")
        )

    except (
        TypeError,
        ValueError,
    ):

        return 0.0


def _productivity_filter_asset_master_categories(
    data,
):
    """
    Validate original Productivity rows against Asset master.

    The original report contains category headings such as:

        Excavator
          EX01
          IS0330

        ADT
          ADT01
          IS0601

        Dozer
          IS0335

    We check every child machine against Asset.asset_category.

    A machine is retained only when its master category matches
    the section it is displayed under.
    """

    rows = [
        dict(row)
        if hasattr(
            row,
            "get",
        )
        else row

        for row in (
            data or []
        )
    ]

    valid_categories = (
        "Excavator",
        "ADT",
        "Dozer",
    )

    # --------------------------------------------------------
    # FIND MACHINE NAMES FROM ORIGINAL RESULT
    # --------------------------------------------------------

    machine_names = set()

    current_category = None

    for row in rows:

        if not hasattr(
            row,
            "get",
        ):
            continue

        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()

        indent = int(
            row.get(
                "indent"
            )
            or 0
        )

        if (
            label
            in valid_categories
            and indent == 0
        ):

            current_category = label
            continue

        if (
            current_category
            and label
            and indent > 0
        ):

            machine_names.add(
                label
            )

    # --------------------------------------------------------
    # LOAD ASSET MASTER CATEGORIES ONCE
    # --------------------------------------------------------

    asset_categories = {}

    if machine_names:

        assets = frappe.get_all(
            "Asset",
            filters={
                "name": [
                    "in",
                    sorted(
                        machine_names
                    ),
                ],
            },
            fields=[
                "name",
                "asset_category",
            ],
            limit_page_length=0,
        )

        for asset in assets:

            asset_categories[
                str(
                    asset.name
                ).strip()
            ] = (
                _productivity_master_category(
                    asset.asset_category
                )
            )

    # --------------------------------------------------------
    # PROCESS ONE CATEGORY AT A TIME
    # --------------------------------------------------------

    result = []

    index = 0

    while index < len(rows):

        row = rows[
            index
        ]

        if not hasattr(
            row,
            "get",
        ):

            result.append(
                row
            )

            index += 1
            continue

        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()

        indent = int(
            row.get(
                "indent"
            )
            or 0
        )

        # Not a category heading.
        if not (
            label
            in valid_categories
            and indent == 0
        ):

            result.append(
                row
            )

            index += 1
            continue

        category = label

        category_row = dict(
            row
        )

        section_rows = []

        next_index = (
            index + 1
        )

        while (
            next_index
            < len(rows)
        ):

            candidate = rows[
                next_index
            ]

            if not hasattr(
                candidate,
                "get",
            ):

                next_index += 1
                continue

            candidate_label = str(
                candidate.get(
                    "label"
                )
                or ""
            ).strip()

            candidate_indent = int(
                candidate.get(
                    "indent"
                )
                or 0
            )

            if (
                candidate_label
                in valid_categories
                and candidate_indent == 0
            ):
                break

            section_rows.append(
                dict(
                    candidate
                )
            )

            next_index += 1

        # ----------------------------------------------------
        # KEEP MACHINES WHOSE ASSET CATEGORY MATCHES SECTION
        # ----------------------------------------------------

        valid_machine_rows = []

        for machine_row in section_rows:

            machine = str(
                machine_row.get(
                    "label"
                )
                or ""
            ).strip()

            machine_indent = int(
                machine_row.get(
                    "indent"
                )
                or 0
            )

            if (
                not machine
                or machine_indent <= 0
            ):
                continue

            master_category = (
                asset_categories.get(
                    machine
                )
            )

            if (
                master_category
                != category
            ):
                continue

            valid_machine_rows.append(
                machine_row
            )

        # ----------------------------------------------------
        # REBUILD CATEGORY TOTALS FROM VALID MACHINES ONLY
        # ----------------------------------------------------

        category_hours = sum(
            _productivity_master_number(
                machine_row.get(
                    "working_hours"
                )
            )
            for machine_row
            in valid_machine_rows
        )

        category_output = sum(
            _productivity_master_number(
                machine_row.get(
                    "output"
                )
            )
            for machine_row
            in valid_machine_rows
        )

        machine_productivities = [
            _productivity_master_number(
                machine_row.get(
                    "productivity"
                )
            )
            for machine_row
            in valid_machine_rows
        ]

        category_productivity = (
            sum(
                machine_productivities
            )
            / len(
                machine_productivities
            )

            if machine_productivities

            else 0.0
        )

        category_row[
            "working_hours"
        ] = round(
            category_hours,
            3,
        )

        category_row[
            "output"
        ] = round(
            category_output,
            3,
        )

        category_row[
            "productivity"
        ] = round(
            category_productivity,
            3,
        )

        result.append(
            category_row
        )

        result.extend(
            valid_machine_rows
        )

        index = next_index

    return result



def _productivity_execute_before_monthly_plans(filters=None):
    filters = frappe._dict(filters or {})

    # ========================================================
    # ORIGINAL PRODUCTIVITY
    # ========================================================
    #
    # This remains the authoritative source for:
    #
    # - Working Hours from Pre-Use
    # - Machine totals
    # - Category totals
    # - Report summaries
    #
    # ========================================================

    original_result = _execute_original(
        filters
    )

    if not original_result:
        return original_result

    columns = (
        original_result[0]
        if len(original_result) > 0
        else []
    )

    data = (
        original_result[1]
        if len(original_result) > 1
        else []
    )


    # ========================================================
    # KOSI_PRODUCTIVITY_APPLY_ASSET_MASTER_CATEGORY_FILTER_V2
    # ========================================================

    data = (
        _productivity_filter_asset_master_categories(
            data
        )
    )

    # ========================================================
    # ADD MATERIAL + HAUL DISTANCE COLUMNS
    # ========================================================

    columns = (
        _productivity_add_material_columns(
            columns
        )
    )

    # ========================================================
    # ALWAYS MACHINE + MATERIAL
    #
    # There is NO Machine Totals / Machine per Material
    # dropdown anymore.
    #
    # Working Hours:
    #     Total comes from Pre-Use.
    #
    # Material split:
    #     Determined from Hourly Production activity.
    #
    # Material rows must add back to the machine's original
    # Pre-Use Working Hours.
    #
    # Output:
    #     Actual BCM loaded by Machine + Material.
    #
    # Hauling Distance:
    #     Blank until Survey integration is added.
    # ========================================================

    # ========================================================
    # KOSI_PRODUCTIVITY_TALLIES_LABEL_V1
    #
    # Default / Tallies mode:
    #     Output (BCM) -> Tallies BCMs
    #
    # Actual BCM mode later changes this same column to:
    #     Adjusted BCM
    # ========================================================

    for column in columns or []:

        if (
            hasattr(column, "get")
            and column.get("fieldname") == "output"
        ):
            column["label"] = "Tallies BCMs"

    # ========================================================
    # KOSI_PRODUCTIVITY_ZERO_DECIMAL_COLUMNS_V1
    #
    # Display / export numeric report columns at 0 decimals.
    # Calculations below still retain full precision.
    # ========================================================

    for column in columns or []:

        if (
            hasattr(column, "get")
            and column.get("fieldname")
            in (
                "working_hours",
                "output",
                "productivity",
                "hauling_distance_m",
            )
        ):
            column["precision"] = 0

    data = (
        _productivity_machine_material_rows(
            data,
            filters,
        )
    )

    # ========================================================
    # KOSI_PRODUCTIVITY_FLAT_MATERIAL_ROWS_V1
    #
    # Frappe automatically enables Tree View whenever any
    # returned row contains an "indent" key.
    #
    # Machine + Material is now a flat report because the same
    # machine can legitimately appear more than once:
    #
    #     IS0330 | Coal
    #     IS0330 | Hards
    #     IS0330 | Softs
    #
    # Therefore REMOVE the indent key completely.
    #
    # Important:
    #     indent = 0 is NOT enough.
    #     The key itself must not exist in the returned row.
    # ========================================================

    flat_data = []

    for row in data or []:

        if hasattr(row, "get"):

            new_row = dict(row)

            new_row.pop(
                "indent",
                None,
            )

            # Keep category rows identifiable for any future
            # formatting, but do not make them tree parents.
            label = str(
                new_row.get("label") or ""
            ).strip()

            new_row["is_category_total"] = (
                1
                if label in (
                    "Excavator",
                    "ADT",
                    "Dozer",
                )
                else 0
            )

            flat_data.append(
                new_row
            )

        else:
            flat_data.append(
                row
            )

    data = flat_data

    # ========================================================
    # KOSI_PRODUCTIVITY_ASSET_FILTER_V1
    #
    # Supported Assets ONLY:
    #     Excavator
    #     Dozer
    #     ADT
    #
    # If an Asset is selected:
    # - keep only that machine
    # - keep all of its Material rows
    # - rebuild its category total
    # - update the report KPI for the selected machine
    # ========================================================

    selected_asset = str(
        filters.get("asset") or ""
    ).strip()

    selected_category = ""

    selected_asset_hours = 0.0
    selected_asset_output = 0.0
    selected_asset_productivity = 0.0

    if selected_asset:

        selected_category = str(
            frappe.db.get_value(
                "Asset",
                selected_asset,
                "asset_category",
            )
            or ""
        ).strip()

        supported_categories = (
            "Excavator",
            "Dozer",
            "ADT",
        )

        selected_machine_type = str(
            filters.get("machine_type")
            or ""
        ).strip()

        # Asset must be one of the supported production
        # categories.
        if (
            selected_category
            not in supported_categories
        ):
            data = []

        # If Machine Type is selected as well, the Asset
        # must belong to the same category.
        elif (
            selected_machine_type
            and selected_machine_type
            != selected_category
        ):
            data = []

        else:

            asset_rows = []

            for row in flat_data:

                if not hasattr(
                    row,
                    "get",
                ):
                    continue

                if (
                    str(
                        row.get("label")
                        or ""
                    ).strip()
                    != selected_asset
                ):
                    continue

                if (
                    int(
                        row.get(
                            "is_category_total"
                        )
                        or 0
                    )
                    == 1
                ):
                    continue

                asset_rows.append(
                    dict(row)
                )

            if asset_rows:

                selected_asset_hours = sum(
                    _productivity_float(
                        row.get(
                            "working_hours"
                        )
                    )
                    for row in asset_rows
                )

                selected_asset_output = sum(
                    _productivity_float(
                        row.get(
                            "output"
                        )
                    )
                    for row in asset_rows
                )

                selected_asset_productivity = (
                    selected_asset_output
                    / selected_asset_hours
                    if selected_asset_hours > 0
                    else 0.0
                )

                category_row = {
                    "label":
                        selected_category,

                    "working_hours":
                        round(
                            selected_asset_hours,
                            3,
                        ),

                    "output":
                        round(
                            selected_asset_output,
                            3,
                        ),

                    "productivity":
                        round(
                            selected_asset_productivity,
                            3,
                        ),

                    "style": "",

                    "material": "",

                    "hauling_distance_m": "",

                    "is_category_total": 1,
                }

                data = (
                    [category_row]
                    + asset_rows
                )

            else:
                data = []

    # ========================================================
    # KOSI_PRODUCTIVITY_ACTUAL_BCM_MODE_V1
    #
    # BCM Basis:
    #
    # Tallies BCMs
    #     Keep current report exactly as-is.
    #
    # Actual BCMs
    #     Show Tallies BCM + Adjusted BCM.
    #
    # Adjusted BCM is Survey-corrected Actual BCM,
    # proportionally allocated to machine/material tally rows.
    #
    # TS correction applies to:
    #     Excavator
    #     ADT
    #
    # Dozer correction applies to:
    #     Dozer
    # ========================================================

    bcm_basis = str(
        filters.get("bcm_basis")
        or "Tallies BCMs"
    ).strip()

    actual_adjustment = None

    if bcm_basis == "Actual BCMs":

        actual_adjustment = (
            _productivity_actual_adjustment(
                filters
            )
        )

        # ----------------------------------------------------
        # COLUMNS
        #
        # Tallies mode:
        # Output (BCM)
        #
        # Actual mode:
        # Tallies BCM | Adjusted BCM
        # ----------------------------------------------------

        has_tallies_column = any(
            (
                hasattr(column, "get")
                and column.get(
                    "fieldname"
                )
                == "tallies_bcm"
            )
            for column in columns
        )

        output_index = None

        for index, column in enumerate(
            columns
        ):

            if not hasattr(
                column,
                "get",
            ):
                continue

            if (
                column.get(
                    "fieldname"
                )
                == "output"
            ):
                output_index = index

                column[
                    "label"
                ] = "Adjusted BCM"

                column[
                    "precision"
                ] = 0

                break

        if (
            output_index is not None
            and not has_tallies_column
        ):

            columns.insert(
                output_index,
                {
                    "label":
                        "Tallies BCM",

                    "fieldname":
                        "tallies_bcm",

                    "fieldtype":
                        "Float",

                    "precision":
                        0,

                    "width":
                        115,
                },
            )

        adjusted_data = []

        current_category = ""

        for original_row in data or []:

            if not hasattr(
                original_row,
                "get",
            ):
                adjusted_data.append(
                    original_row
                )
                continue

            row = dict(
                original_row
            )

            label = str(
                row.get(
                    "label"
                )
                or ""
            ).strip()

            if int(
                row.get(
                    "is_category_total"
                )
                or 0
            ) == 1:

                if label in (
                    "Excavator",
                    "ADT",
                    "Dozer",
                ):
                    current_category = label

            factor = 1.0

            if current_category in (
                "Excavator",
                "ADT",
            ):

                factor = (
                    actual_adjustment[
                        "ts_factor"
                    ]
                )

            elif (
                current_category
                == "Dozer"
            ):

                factor = (
                    actual_adjustment[
                        "dozer_factor"
                    ]
                )

            tally_bcm = (
                _productivity_number(
                    row.get(
                        "output"
                    )
                )
            )

            adjusted_bcm = (
                tally_bcm
                * factor
            )

            working_hours = (
                _productivity_number(
                    row.get(
                        "working_hours"
                    )
                )
            )

            row[
                "tallies_bcm"
            ] = round(
                tally_bcm,
                3,
            )

            row[
                "adjusted_bcm"
            ] = round(
                adjusted_bcm,
                3,
            )

            # Existing machine-total and formatter logic uses
            # output, so Actual mode makes output = Adjusted BCM.
            row[
                "output"
            ] = round(
                adjusted_bcm,
                3,
            )

            row[
                "productivity"
            ] = (
                round(
                    adjusted_bcm
                    / working_hours,
                    3,
                )
                if working_hours > 0
                else 0.0
            )

            adjusted_data.append(
                row
            )

        data = adjusted_data

        # If one Asset was selected, recalculate the values
        # later used by the existing KPI summary patch.
        if selected_asset:

            asset_material_rows = [
                row
                for row in data
                if (
                    hasattr(
                        row,
                        "get",
                    )
                    and int(
                        row.get(
                            "is_category_total"
                        )
                        or 0
                    )
                    != 1
                )
            ]

            selected_asset_hours = sum(
                _productivity_number(
                    row.get(
                        "working_hours"
                    )
                )
                for row in asset_material_rows
            )

            selected_asset_output = sum(
                _productivity_number(
                    row.get(
                        "output"
                    )
                )
                for row in asset_material_rows
            )

            selected_asset_productivity = (
                selected_asset_output
                / selected_asset_hours
                if selected_asset_hours > 0
                else 0.0
            )

    # ========================================================
    # KOSI_PRODUCTIVITY_MACHINE_TOTAL_ROWS_V1
    #
    # FULL REPORT ONLY
    #
    # Keep:
    #     Excavator category total
    #     ADT category total
    #     Dozer category total
    #
    # Then add a total row for each individual machine:
    #
    #     IS0334 TOTAL
    #     IS0334 Coal
    #     IS0334 Hards
    #
    # Machine Total:
    #
    #     Hours = SUM(material hours)
    #     BCM   = SUM(material BCM)
    #     Productivity = Total BCM / Total Hours
    #
    # When a specific Asset filter is selected we do NOT add
    # another machine total because the existing top total row
    # already represents that selected asset.
    # ========================================================

    if not selected_asset:

        rows_with_machine_totals = []

        current_machine = None
        current_machine_rows = []

        def flush_machine_total():

            nonlocal current_machine
            nonlocal current_machine_rows

            if (
                not current_machine
                or not current_machine_rows
            ):
                current_machine = None
                current_machine_rows = []
                return

            total_hours = sum(
                _productivity_float(
                    row.get(
                        "working_hours"
                    )
                )
                for row in current_machine_rows
            )

            # KOSI_PRODUCTIVITY_MACHINE_TOTAL_ACTUAL_COLUMNS_V1

            total_output = sum(
                _productivity_number(
                    row.get(
                        "output"
                    )
                )
                for row in current_machine_rows
            )

            total_tallies = sum(
                _productivity_number(
                    row.get(
                        "tallies_bcm",
                        row.get(
                            "output"
                        ),
                    )
                )
                for row in current_machine_rows
            )

            total_productivity = (
                total_output / total_hours
                if total_hours > 0
                else 0.0
            )

            total_row = dict(
                current_machine_rows[0]
            )

            total_row[
                "label"
            ] = current_machine

            total_row[
                "working_hours"
            ] = round(
                total_hours,
                3,
            )

            total_row[
                "output"
            ] = round(
                total_output,
                3,
            )

            if (
                bcm_basis
                == "Actual BCMs"
            ):

                total_row[
                    "tallies_bcm"
                ] = round(
                    total_tallies,
                    3,
                )

                total_row[
                    "adjusted_bcm"
                ] = round(
                    total_output,
                    3,
                )

            total_row[
                "productivity"
            ] = round(
                total_productivity,
                3,
            )

            total_row[
                "material"
            ] = ""

            total_row[
                "hauling_distance_m"
            ] = ""

            total_row[
                "is_category_total"
            ] = 0

            total_row[
                "is_machine_total"
            ] = 1

            total_row[
                "style"
            ] = ""

            rows_with_machine_totals.append(
                total_row
            )

            for machine_row in current_machine_rows:

                machine_row = dict(
                    machine_row
                )

                machine_row[
                    "is_machine_total"
                ] = 0

                rows_with_machine_totals.append(
                    machine_row
                )

            current_machine = None
            current_machine_rows = []

        for row in data or []:

            if not hasattr(
                row,
                "get",
            ):
                continue

            row = dict(row)

            # --------------------------------------------
            # CATEGORY TOTAL
            # --------------------------------------------

            if int(
                row.get(
                    "is_category_total"
                )
                or 0
            ) == 1:

                flush_machine_total()

                row[
                    "is_machine_total"
                ] = 0

                rows_with_machine_totals.append(
                    row
                )

                continue

            label = str(
                row.get(
                    "label"
                )
                or ""
            ).strip()

            if not label:
                continue

            # --------------------------------------------
            # NEW MACHINE
            # --------------------------------------------

            if (
                current_machine is not None
                and label != current_machine
            ):
                flush_machine_total()

            if current_machine is None:
                current_machine = label

            current_machine_rows.append(
                row
            )

        flush_machine_total()

        data = rows_with_machine_totals

    # ========================================================
    # KOSI_PRODUCTIVITY_FINAL_BCM_COLUMN_VISIBILITY_V1
    #
    # Tallies BCMs mode:
    #     Show Tallies BCMs only.
    #
    # Actual BCMs mode:
    #     Hide Tallies BCMs completely.
    #     Show Adjusted BCM only.
    #
    # tallies_bcm may remain inside row dictionaries for
    # reconciliation, but it must not be a visible column.
    # ========================================================

    bcm_basis = str(
        filters.get("bcm_basis")
        or "Tallies BCMs"
    ).strip()

    if bcm_basis == "Actual BCMs":

        columns = [
            column
            for column in columns
            if not (
                hasattr(column, "get")
                and column.get("fieldname")
                == "tallies_bcm"
            )
        ]

        for column in columns:

            if not hasattr(
                column,
                "get",
            ):
                continue

            if (
                column.get("fieldname")
                == "output"
            ):
                column["label"] = "Adjusted BCM"
                column["precision"] = 0

    else:

        # Tallies mode must show the normal output column
        # as Tallies BCMs.

        columns = [
            column
            for column in columns
            if not (
                hasattr(column, "get")
                and column.get("fieldname")
                == "tallies_bcm"
            )
        ]

        for column in columns:

            if not hasattr(
                column,
                "get",
            ):
                continue

            if (
                column.get("fieldname")
                == "output"
            ):
                column["label"] = "Tallies BCMs"
                column["precision"] = 0


    # ========================================================
    # KOSI_PRODUCTIVITY_HOURS_MATERIAL_SUMMARY_V1
    #
    # SUMMARY FILTER
    #
    # Summary Per Machine
    #     Keep current report exactly as-is.
    #
    # Hours and Material
    #     Remove individual machine rows and combine:
    #
    #         Working Hours
    #         Tallies / Adjusted BCM
    #         Productivity
    #
    #     by Material within each machine category.
    #
    # Important:
    # Excavator and ADT are NOT merged into one category because
    # both use the same Truck Loads production source and doing
    # that would double-count Truck & Shovel BCM.
    # ========================================================

    summary_view = str(
        filters.get("summary_view")
        or "Summary Per Machine"
    ).strip()

    if summary_view == "Hours and Material":

        category_names = (
            "Excavator",
            "ADT",
            "Dozer",
        )

        # Preserve the order in which categories/materials
        # already appear in the report.
        category_order = []

        grouped = {}

        current_category = ""

        for source_row in data or []:

            if not hasattr(
                source_row,
                "get",
            ):
                continue

            row = dict(
                source_row
            )

            label = str(
                row.get("label")
                or ""
            ).strip()

            # ------------------------------------------------
            # CATEGORY HEADER / TOTAL
            # ------------------------------------------------

            if (
                int(
                    row.get(
                        "is_category_total"
                    )
                    or 0
                )
                == 1
                and label in category_names
            ):

                current_category = label

                if (
                    current_category
                    not in grouped
                ):
                    grouped[
                        current_category
                    ] = {}

                    category_order.append(
                        current_category
                    )

                continue

            # ------------------------------------------------
            # SKIP MACHINE TOTAL ROWS
            #
            # They duplicate the material detail values.
            # ------------------------------------------------

            if (
                int(
                    row.get(
                        "is_machine_total"
                    )
                    or 0
                )
                == 1
            ):
                continue

            material = str(
                row.get("material")
                or ""
            ).strip()

            if (
                not current_category
                or not material
            ):
                continue

            if (
                material
                not in grouped[
                    current_category
                ]
            ):

                grouped[
                    current_category
                ][
                    material
                ] = {
                    "hours": 0.0,
                    "output": 0.0,
                    "tallies": 0.0,
                }

            bucket = grouped[
                current_category
            ][
                material
            ]

            bucket["hours"] += (
                _productivity_number(
                    row.get(
                        "working_hours"
                    )
                )
            )

            # "output" is whichever BCM basis the user selected:
            #
            # Tallies BCMs  -> Tallies
            # Actual BCMs   -> Adjusted BCM
            bucket["output"] += (
                _productivity_number(
                    row.get(
                        "output"
                    )
                )
            )

            # Keep internal Tallies available for reconciliation
            # even when Actual BCMs is selected.
            bucket["tallies"] += (
                _productivity_number(
                    row.get(
                        "tallies_bcm",
                        row.get(
                            "output"
                        ),
                    )
                )
            )

        # ====================================================
        # KOSI_PRODUCTIVITY_UNALLOCATED_MATERIAL_HOURS_V1
        #
        # Hours and Material must reconcile to the FULL
        # category working hours.
        #
        # Some machines can have valid Pre-Use hours but no
        # production material / BCM.
        #
        # Example:
        #   Dozer total hours      = 2,467
        #   Material-linked hours = 1,830
        #   Unallocated hours       =   637
        #
        # Add the difference as a blank-material row so:
        #
        #   material hours + blank hours = category hours
        #
        # BCM remains 0 for these unmatched hours.
        # ====================================================

        original_category_totals = {}

        for original_row in data or []:

            if not hasattr(
                original_row,
                "get",
            ):
                continue

            original_label = str(
                original_row.get("label")
                or ""
            ).strip()

            if (
                int(
                    original_row.get(
                        "is_category_total"
                    )
                    or 0
                )
                == 1
                and original_label
                in (
                    "Excavator",
                    "ADT",
                    "Dozer",
                )
            ):
                original_category_totals[
                    original_label
                ] = {
                    "hours":
                        _productivity_number(
                            original_row.get(
                                "working_hours"
                            )
                        ),

                    "output":
                        _productivity_number(
                            original_row.get(
                                "output"
                            )
                        ),
                }

        summary_data = []

        for category in category_order:

            materials = grouped.get(
                category,
                {},
            )

            if not materials:
                continue

            # Add valid working hours that have no material.
            original_total = (
                original_category_totals.get(
                    category,
                    {}
                )
            )

            material_hours_before = sum(
                values["hours"]
                for values
                in materials.values()
            )

            full_category_hours = (
                _productivity_number(
                    original_total.get(
                        "hours"
                    )
                )
            )

            unallocated_hours = max(
                0.0,
                full_category_hours
                - material_hours_before,
            )

            if unallocated_hours > 0.0005:

                blank_key = ""

                if blank_key not in materials:

                    materials[
                        blank_key
                    ] = {
                        "hours": 0.0,
                        "output": 0.0,
                        "tallies": 0.0,
                    }

                materials[
                    blank_key
                ][
                    "hours"
                ] += unallocated_hours

            category_hours = sum(
                values["hours"]
                for values
                in materials.values()
            )

            category_output = sum(
                values["output"]
                for values
                in materials.values()
            )

            category_tallies = sum(
                values["tallies"]
                for values
                in materials.values()
            )

            category_productivity = (
                category_output
                / category_hours
                if category_hours > 0
                else 0.0
            )

            # =================================================
            # CATEGORY TOTAL
            # =================================================

            category_row = {
                "label":
                    category,

                "working_hours":
                    round(
                        category_hours,
                        3,
                    ),

                "output":
                    round(
                        category_output,
                        3,
                    ),

                "productivity":
                    round(
                        category_productivity,
                        3,
                    ),

                "material":
                    "",

                "hauling_distance_m":
                    "",

                "is_category_total":
                    1,

                "is_machine_total":
                    0,

                "is_material_summary":
                    0,

                "style":
                    "",
            }

            if (
                str(
                    filters.get(
                        "bcm_basis"
                    )
                    or "Tallies BCMs"
                ).strip()
                == "Actual BCMs"
            ):

                category_row[
                    "tallies_bcm"
                ] = round(
                    category_tallies,
                    3,
                )

            summary_data.append(
                category_row
            )

            # =================================================
            # MATERIAL TOTALS
            # =================================================

            for material, values in materials.items():

                hours = (
                    values["hours"]
                )

                output = (
                    values["output"]
                )

                productivity = (
                    output / hours
                    if hours > 0
                    else 0.0
                )

                material_row = {
                    "label":
                        material,

                    "working_hours":
                        round(
                            hours,
                            3,
                        ),

                    "output":
                        round(
                            output,
                            3,
                        ),

                    "productivity":
                        round(
                            productivity,
                            3,
                        ),

                    "material":
                        material,

                    "hauling_distance_m":
                        "",

                    "is_category_total":
                        0,

                    # Use existing machine-total formatting
                    # so these summary rows display bold.
                    "is_machine_total":
                        1,

                    "is_material_summary":
                        1,

                    "style":
                        "",
                }

                if (
                    str(
                        filters.get(
                            "bcm_basis"
                        )
                        or "Tallies BCMs"
                    ).strip()
                    == "Actual BCMs"
                ):

                    material_row[
                        "tallies_bcm"
                    ] = round(
                        values[
                            "tallies"
                        ],
                        3,
                    )

                summary_data.append(
                    material_row
                )

        # ====================================================
        # KOSI_PRODUCTIVITY_TOTAL_FLEET_V1
        #
        # TOTAL FLEET = EXCAVATOR + DOZER ONLY
        #
        # ADT is intentionally excluded:
        # Excavator and ADT represent the same Truck + Shovel
        # production BCM and adding ADT would double-count BCM.
        #
        # Hours:
        #     Excavator Hours + Dozer Hours
        #
        # BCM:
        #     Excavator BCM + Dozer BCM
        #
        # Productivity:
        #     Total BCM / Total Hours
        #
        # Material / Hauling Distance:
        #     Blank
        #
        # Total Fleet displays only when BOTH Excavator and
        # Dozer category totals are present.
        #
        # It is intentionally NOT marked as category total,
        # therefore it remains normal / unbold.
        # ====================================================

        fleet_category_rows = {
            str(
                row.get("label")
                or ""
            ).strip(): row
            for row in summary_data
            if (
                hasattr(row, "get")
                and int(
                    row.get(
                        "is_category_total"
                    )
                    or 0
                )
                == 1
                and str(
                    row.get("label")
                    or ""
                ).strip()
                in (
                    "Excavator",
                    "Dozer",
                )
            )
        }

        if (
            "Excavator"
            in fleet_category_rows
            and "Dozer"
            in fleet_category_rows
        ):

            excavator_row = (
                fleet_category_rows[
                    "Excavator"
                ]
            )

            dozer_row = (
                fleet_category_rows[
                    "Dozer"
                ]
            )

            fleet_hours = (
                _productivity_number(
                    excavator_row.get(
                        "working_hours"
                    )
                )
                +
                _productivity_number(
                    dozer_row.get(
                        "working_hours"
                    )
                )
            )

            fleet_output = (
                _productivity_number(
                    excavator_row.get(
                        "output"
                    )
                )
                +
                _productivity_number(
                    dozer_row.get(
                        "output"
                    )
                )
            )

            fleet_productivity = (
                fleet_output
                / fleet_hours
                if fleet_hours > 0
                else 0.0
            )

            fleet_row = {
                "label":
                    "Total Fleet",

                "working_hours":
                    round(
                        fleet_hours,
                        3,
                    ),

                "output":
                    round(
                        fleet_output,
                        3,
                    ),

                "productivity":
                    round(
                        fleet_productivity,
                        3,
                    ),

                "material":
                    "",

                "hauling_distance_m":
                    "",

                # Keep Total Fleet NORMAL / UNBOLD.
                "is_category_total":
                    0,

                "is_machine_total":
                    0,

                "is_material_summary":
                    0,

                "is_total_fleet":
                    1,

                "style":
                    "",
            }

            # Keep the internal Tallies value available
            # when Actual BCM mode is selected.
            bcm_basis_value = str(
                filters.get(
                    "bcm_basis"
                )
                or "Tallies BCMs"
            ).strip()

            if (
                bcm_basis_value
                == "Actual BCMs"
            ):

                fleet_tallies = (
                    _productivity_number(
                        excavator_row.get(
                            "tallies_bcm"
                        )
                    )
                    +
                    _productivity_number(
                        dozer_row.get(
                            "tallies_bcm"
                        )
                    )
                )

                fleet_row[
                    "tallies_bcm"
                ] = round(
                    fleet_tallies,
                    3,
                )

            summary_data.append(
                fleet_row
            )

        data = summary_data


    # ========================================================
    # KOSI_PRODUCTIVITY_TOTAL_FLEET_ALL_VIEWS_V1
    #
    # Ensure Total Fleet exists in BOTH:
    #
    #   Summary Per Machine
    #   Hours and Material
    #
    # Total Fleet =
    #   Excavator Hours + Dozer Hours
    #   Excavator BCM   + Dozer BCM
    #
    # ADT intentionally excluded because ADT and Excavator
    # carry the same Truck + Shovel production BCM.
    #
    # If Hours and Material already created Total Fleet,
    # do NOT create a duplicate.
    # ========================================================

    existing_total_fleet = any(
        (
            hasattr(row, "get")
            and str(
                row.get("label")
                or ""
            ).strip()
            == "Total Fleet"
        )
        for row in (
            data or []
        )
    )

    if not existing_total_fleet:

        category_rows = {}

        for row in data or []:

            if not hasattr(
                row,
                "get",
            ):
                continue

            label = str(
                row.get("label")
                or ""
            ).strip()

            if (
                label
                in (
                    "Excavator",
                    "Dozer",
                )
                and int(
                    row.get(
                        "is_category_total"
                    )
                    or 0
                )
                == 1
            ):
                category_rows[
                    label
                ] = row

        # Total Fleet only makes sense when BOTH
        # Excavator and Dozer are available in the report.
        if (
            "Excavator"
            in category_rows
            and "Dozer"
            in category_rows
        ):

            excavator_row = (
                category_rows[
                    "Excavator"
                ]
            )

            dozer_row = (
                category_rows[
                    "Dozer"
                ]
            )

            total_fleet_hours = (
                _productivity_number(
                    excavator_row.get(
                        "working_hours"
                    )
                )
                +
                _productivity_number(
                    dozer_row.get(
                        "working_hours"
                    )
                )
            )

            total_fleet_bcm = (
                _productivity_number(
                    excavator_row.get(
                        "output"
                    )
                )
                +
                _productivity_number(
                    dozer_row.get(
                        "output"
                    )
                )
            )

            total_fleet_productivity = (
                total_fleet_bcm
                / total_fleet_hours
                if total_fleet_hours > 0
                else 0.0
            )

            total_fleet_row = {
                "label":
                    "Total Fleet",

                "working_hours":
                    round(
                        total_fleet_hours,
                        3,
                    ),

                "output":
                    round(
                        total_fleet_bcm,
                        3,
                    ),

                "productivity":
                    round(
                        total_fleet_productivity,
                        3,
                    ),

                "material":
                    "",

                "hauling_distance_m":
                    "",

                "is_category_total":
                    0,

                "is_machine_total":
                    0,

                "is_material_summary":
                    0,

                # Frontend uses this flag to make
                # Total Fleet bold.
                "is_total_fleet":
                    1,

                "style":
                    "",
            }

            # Preserve internal Tallies reconciliation
            # when user selected Actual BCMs.
            bcm_basis_value = str(
                filters.get(
                    "bcm_basis"
                )
                or "Tallies BCMs"
            ).strip()

            if (
                bcm_basis_value
                == "Actual BCMs"
            ):

                total_fleet_tallies = (
                    _productivity_number(
                        excavator_row.get(
                            "tallies_bcm"
                        )
                    )
                    +
                    _productivity_number(
                        dozer_row.get(
                            "tallies_bcm"
                        )
                    )
                )

                total_fleet_row[
                    "tallies_bcm"
                ] = round(
                    total_fleet_tallies,
                    3,
                )

            data.append(
                total_fleet_row
            )


    result = _productivity_repack_result(
        original_result,
        columns,
        data,
    )

    # ========================================================
    # UPDATE SUMMARY KPI WHEN A SPECIFIC ASSET IS SELECTED
    # ========================================================

    if (
        selected_asset
        and selected_category
        and len(result) > 4
    ):

        result_list = list(
            result
        )

        summary = [
            dict(item)
            if hasattr(item, "get")
            else item
            for item in (
                result_list[4]
                or []
            )
        ]

        for item in summary:

            if not hasattr(
                item,
                "get",
            ):
                continue

            label = str(
                item.get("label")
                or ""
            )

            if (
                "Truck + Shovel Productivity"
                in label
            ):
                if (
                    selected_category
                    in (
                        "Excavator",
                        "ADT",
                    )
                ):
                    item["value"] = str(
                        round(
                            selected_asset_productivity
                        )
                    )
                else:
                    item["value"] = "0"

            elif (
                "Dozing Productivity"
                in label
            ):
                if (
                    selected_category
                    == "Dozer"
                ):
                    item["value"] = str(
                        round(
                            selected_asset_productivity
                        )
                    )
                else:
                    item["value"] = "0"

        result_list[4] = summary

        if isinstance(
            result,
            tuple,
        ):
            result = tuple(
                result_list
            )
        else:
            result = result_list


    # ========================================================
    # KOSI_PRODUCTIVITY_ACTUAL_SUMMARY_V1
    # ========================================================

    if (
        bcm_basis == "Actual BCMs"
        and not selected_asset
        and len(result) > 4
    ):

        result_list = list(
            result
        )

        summary = [
            dict(item)
            if hasattr(item, "get")
            else item
            for item in (
                result_list[4]
                or []
            )
        ]

        ts_productivity = 0.0
        dozer_productivity = 0.0

        # Prefer Excavator for TS productivity.
        # If only ADT is filtered, use ADT category.
        excavator_row = next(
            (
                row
                for row in data
                if (
                    hasattr(
                        row,
                        "get",
                    )
                    and row.get(
                        "label"
                    )
                    == "Excavator"
                )
            ),
            None,
        )

        adt_row = next(
            (
                row
                for row in data
                if (
                    hasattr(
                        row,
                        "get",
                    )
                    and row.get(
                        "label"
                    )
                    == "ADT"
                )
            ),
            None,
        )

        dozer_row = next(
            (
                row
                for row in data
                if (
                    hasattr(
                        row,
                        "get",
                    )
                    and row.get(
                        "label"
                    )
                    == "Dozer"
                )
            ),
            None,
        )

        ts_row = (
            excavator_row
            or adt_row
        )

        if ts_row:
            ts_productivity = (
                _productivity_number(
                    ts_row.get(
                        "productivity"
                    )
                )
            )

        if dozer_row:
            dozer_productivity = (
                _productivity_number(
                    dozer_row.get(
                        "productivity"
                    )
                )
            )

        for item in summary:

            if not hasattr(
                item,
                "get",
            ):
                continue

            label = str(
                item.get(
                    "label"
                )
                or ""
            )

            if (
                "Truck + Shovel Productivity"
                in label
            ):

                item[
                    "value"
                ] = str(
                    round(
                        ts_productivity
                    )
                )

            elif (
                "Dozing Productivity"
                in label
            ):

                item[
                    "value"
                ] = str(
                    round(
                        dozer_productivity
                    )
                )

        result_list[4] = summary

        if isinstance(
            result,
            tuple,
        ):
            result = tuple(
                result_list
            )
        else:
            result = result_list

    return result

# END KOSI_PRODUCTIVITY_MACHINE_MATERIAL_V1



# ============================================================
# KOSI_PRODUCTIVITY_MULTI_MONTHLY_PLANS_V1
#
# MULTIPLE MONTHLY PRODUCTION PLANNING REPORT
#
# Normal mode:
#
#     No Monthly Production selected
#         -> existing Productivity runs unchanged
#
# Monthly mode:
#
#     One or more Monthly Production Planning records selected
#         -> each plan runs through the EXISTING Productivity
#            calculation using that plan's exact production dates
#
# Example:
#
#     2026-07-...-Klipfontein
#         Excavator
#         ADT
#         Dozer
#         Total Fleet
#
#     2026-08-...-Klipfontein
#         Excavator
#         ADT
#         Dozer
#         Total Fleet
#
# ============================================================


def _productivity_selected_monthly_plans(
    value,
):

    if not value:
        return []


    if isinstance(
        value,
        (
            list,
            tuple,
            set,
        ),
    ):

        out = []

        for item in value:

            if isinstance(
                item,
                dict,
            ):

                name = (
                    item.get("value")
                    or item.get("name")
                )

            else:

                name = item


            name = str(
                name or ""
            ).strip()


            if (
                name
                and name not in out
            ):

                out.append(
                    name
                )


        return out


    if isinstance(
        value,
        str,
    ):

        raw = (
            value.strip()
        )


        if not raw:
            return []


        # ----------------------------------------------
        # MultiSelectList can arrive as JSON.
        # ----------------------------------------------

        try:

            import json

            parsed = json.loads(
                raw
            )

            if isinstance(
                parsed,
                list,
            ):

                return (
                    _productivity_selected_monthly_plans(
                        parsed
                    )
                )

        except Exception:

            pass


        import re

        return [
            part.strip()

            for part in re.split(
                r"[\n,]+",
                raw,
            )

            if part.strip()
        ]


    return []


def _productivity_monthly_plan_records(
    names,
):

    if not names:
        return []


    rows = frappe.get_all(
        "Monthly Production Planning",

        filters={
            "name": [
                "in",
                names,
            ],

            "docstatus": [
                "<",
                2,
            ],
        },

        fields=[
            "name",
            "location",
            "prod_month_start_date",
            "prod_month_end_date",
        ],

        order_by=(
            "prod_month_start_date asc, "
            "prod_month_end_date asc, "
            "name asc"
        ),

        limit_page_length=0,
    )


    found = {
        row.name
        for row in rows
    }


    missing = [
        name
        for name in names
        if name not in found
    ]


    if missing:

        frappe.throw(
            "Monthly Production Planning record(s) "
            "not found: "
            + ", ".join(
                missing
            )
        )


    return rows


def _productivity_monthly_repack(
    original_result,
    columns,
    data,
    clear_summary=False,
):

    if isinstance(
        original_result,
        tuple,
    ):

        parts = list(
            original_result
        )


    elif isinstance(
        original_result,
        list,
    ):

        parts = list(
            original_result
        )


    else:

        return (
            columns,
            data,
        )


    while len(
        parts
    ) < 2:

        parts.append(
            None
        )


    parts[0] = columns
    parts[1] = data


    # Standard Script Report:
    #
    # 0 columns
    # 1 data
    # 2 message
    # 3 chart
    # 4 report_summary
    #
    # A combined multi-month report should not display a
    # single month's KPI summary at the top.
    if (
        clear_summary
        and len(parts) > 4
    ):

        parts[4] = []


    if isinstance(
        original_result,
        tuple,
    ):

        return tuple(
            parts
        )


    return parts


def execute(filters=None):

    filters = frappe._dict(
        filters or {}
    )


    selected_names = (
        _productivity_selected_monthly_plans(
            filters.get(
                "monthly_production_plans"
            )
        )
    )


    # ========================================================
    # NORMAL PRODUCTIVITY
    # ========================================================

    if not selected_names:

        return (
            _productivity_execute_before_monthly_plans(
                filters
            )
        )


    # ========================================================
    # MONTHLY PRODUCTION MODE
    # ========================================================

    plans = (
        _productivity_monthly_plan_records(
            selected_names
        )
    )


    if not plans:

        return (
            _productivity_execute_before_monthly_plans(
                filters
            )
        )


    locations = sorted({
        str(
            plan.location
            or ""
        ).strip()

        for plan in plans

        if str(
            plan.location
            or ""
        ).strip()
    })


    if len(
        locations
    ) > 1:

        frappe.throw(
            "Please select Monthly Production plans "
            "for one Site only."
        )


    selected_site = str(
        filters.get(
            "site"
        )
        or ""
    ).strip()


    if (
        selected_site
        and locations
        and selected_site
        != locations[0]
    ):

        frappe.throw(
            "The selected Monthly Production plan "
            "does not belong to Site "
            f"{selected_site}."
        )


    combined_rows = []

    template_result = None
    output_columns = None


    for plan in plans:

        start_date = (
            str(
                plan.prod_month_start_date
            )
        )

        end_date = (
            str(
                plan.prod_month_end_date
            )
        )


        child_filters = frappe._dict(
            dict(
                filters
            )
        )


        child_filters.pop(
            "monthly_production_plans",
            None,
        )


        child_filters[
            "start_date"
        ] = start_date


        child_filters[
            "end_date"
        ] = end_date


        child_filters[
            "site"
        ] = plan.location


        result = (
            _productivity_execute_before_monthly_plans(
                child_filters
            )
        )


        if not result:
            continue


        if template_result is None:

            template_result = result


        if output_columns is None:

            output_columns = (
                result[0]
                if len(result) > 0
                else []
            )


        rows = (
            result[1]
            if len(result) > 1
            else []
        )

        rows = _productivity_force_month_total_fleet(
            rows,
            child_filters,
        )


        # ----------------------------------------------------
        # MONTH HEADER
        # ----------------------------------------------------

        # KOSI_PRODUCTIVITY_MONTHLY_HEADING_DATE_RANGE_V1

        monthly_start_display = (
            frappe.utils.formatdate(
                start_date,
                "dd-MM-yyyy",
            )
        )

        monthly_end_display = (
            frappe.utils.formatdate(
                end_date,
                "dd-MM-yyyy",
            )
        )

        combined_rows.append({
            "label":
                (
                    "MONTHLY PRODUCTION: "
                    f"{monthly_start_display}"
                    " to "
                    f"{monthly_end_display}"
                ),

            "working_hours":
                "",

            "output":
                "",

            "tallies_bcm":
                "",

            "adjusted_bcm":
                "",

            "productivity":
                "",

            "material":
                "",

            "hauling_distance_m":
                "",

            "monthly_production_plan":
                plan.name,

            "monthly_start_date":
                start_date,

            "monthly_end_date":
                end_date,

            "is_monthly_plan_header":
                1,

            "is_category_total":
                0,

            "is_total_fleet":
                0,
        })


        for row in (
            rows or []
        ):

            if hasattr(
                row,
                "get",
            ):

                new_row = dict(
                    row
                )


                new_row[
                    "monthly_production_plan"
                ] = (
                    plan.name
                )


                new_row[
                    "monthly_start_date"
                ] = (
                    start_date
                )


                new_row[
                    "monthly_end_date"
                ] = (
                    end_date
                )


                combined_rows.append(
                    new_row
                )

            else:

                combined_rows.append(
                    row
                )


    if template_result is None:

        return (
            _productivity_execute_before_monthly_plans(
                filters
            )
        )


    return (
        _productivity_monthly_repack(
            template_result,
            output_columns or [],
            combined_rows,

            clear_summary=(
                len(plans) > 1
            ),
        )
    )




# KOSI_PRODUCTIVITY_MONTHLY_HEADER_TOTAL_FLEET_FIX_V1
def _productivity_force_month_total_fleet(rows, filters=None):

    rows = list(rows or [])

    if not rows:
        return rows


    def _n(value):

        if value in (None, ""):
            return 0.0

        try:
            return float(str(value).replace(",", ""))
        except Exception:
            return 0.0


    for row in rows:
        if hasattr(row, "get"):
            if str(row.get("label") or "").strip() == "Total Fleet":
                return rows


    excavator = None
    dozer = None

    for row in rows:
        if not hasattr(row, "get"):
            continue

        label = str(row.get("label") or "").strip()

        if label == "Excavator" and excavator is None:
            excavator = row

        elif label == "Dozer" and dozer is None:
            dozer = row


    if excavator is None and dozer is None:
        return rows


    total_hours = (
        _n(excavator.get("working_hours") if excavator else 0)
        + _n(dozer.get("working_hours") if dozer else 0)
    )

    total_output = (
        _n(excavator.get("output") if excavator else 0)
        + _n(dozer.get("output") if dozer else 0)
    )

    total_tallies = (
        _n(excavator.get("tallies_bcm") if excavator else 0)
        + _n(dozer.get("tallies_bcm") if dozer else 0)
    )

    total_adjusted = (
        _n(excavator.get("adjusted_bcm") if excavator else 0)
        + _n(dozer.get("adjusted_bcm") if dozer else 0)
    )

    bcm_basis = str(
        (filters or {}).get("bcm_basis") or ""
    ).strip()

    if bcm_basis == "Actual BCMs":
        bcm_for_productivity = total_adjusted
    else:
        bcm_for_productivity = total_tallies or total_output

    productivity = round(
        bcm_for_productivity / total_hours,
        3,
    ) if total_hours > 0 else 0

    rows.append({
        "label": "Total Fleet",
        "working_hours": round(total_hours, 3),
        "output": round(total_output, 3),
        "tallies_bcm": round(total_tallies, 3),
        "adjusted_bcm": round(total_adjusted, 3),
        "productivity": productivity,
        "material": "",
        "hauling_distance_m": 0,
        "style": "",
        "is_category_total": 0,
        "is_machine_total": 0,
        "is_total_fleet": 1,
    })

    return rows


# END KOSI_PRODUCTIVITY_MULTI_MONTHLY_PLANS_V1



# ============================================================
# KOSI_PRODUCTIVITY_FINAL_FIXED_COLUMNS_V1
#
# FINAL PRODUCTIVITY COLUMN LAYOUT
#
# This wrapper runs AFTER every other Productivity feature.
#
# It forces the actual column definitions returned to Frappe:
#
# Label                  360
# Working Hours          150
# BCM                    150
# Productivity           175
# Material               150
# Hauling Distance       180
#
# This is more reliable than CSS because Frappe DataTable
# generates its widths from the report column definitions.
# ============================================================


_productivity_execute_before_final_fixed_columns = execute


def _productivity_apply_final_column_widths(
    columns,
):

    width_map = {
        "label":
            520,

        "working_hours":
            150,

        "output":
            150,

        "tallies_bcm":
            150,

        "adjusted_bcm":
            150,

        "productivity":
            175,

        "material":
            150,

        "hauling_distance_m":
            180,
    }


    label_map = {
        "label":
            "Label",

        "working_hours":
            "Working Hours",

        "productivity":
            "Productivity (BCM/Hr)",

        "material":
            "Material",

        "hauling_distance_m":
            "Hauling Distance (M)",
    }


    result = []


    for column in (
        columns or []
    ):

        if not hasattr(
            column,
            "get",
        ):

            result.append(
                column
            )

            continue


        column = dict(
            column
        )


        fieldname = str(
            column.get(
                "fieldname"
            )
            or ""
        ).strip()


        if fieldname in width_map:

            column[
                "width"
            ] = width_map[
                fieldname
            ]


        if fieldname in label_map:

            column[
                "label"
            ] = label_map[
                fieldname
            ]


        # BCM title remains dynamic.
        #
        # Tallies mode:
        #     Tallies BCMs
        #
        # Actual mode:
        #     Adjusted BCM
        #
        # Only width is fixed.
        result.append(
            column
        )


    return result


def execute(
    filters=None,
):

    original_result = (
        _productivity_execute_before_final_fixed_columns(
            filters
        )
    )


    if not original_result:

        return original_result


    if isinstance(
        original_result,
        tuple,
    ):

        result = list(
            original_result
        )

        return_tuple = True

    elif isinstance(
        original_result,
        list,
    ):

        result = list(
            original_result
        )

        return_tuple = False

    else:

        return original_result


    if result:

        result[0] = (
            _productivity_apply_final_column_widths(
                result[0]
            )
        )


    if return_tuple:

        return tuple(
            result
        )


    return result


# END KOSI_PRODUCTIVITY_FINAL_FIXED_COLUMNS_V1



# ============================================================
# KOSI_PRODUCTIVITY_FROM_TO_AREA_COLUMNS_V1
#
# Add two display columns directly after Material:
#
#     From Area
#     To Area
#
# Current source data does not yet contain a confirmed
# From/To pair for every Productivity row, therefore values
# remain blank until the source mapping is confirmed.
#
# No calculation is changed.
# ============================================================


_productivity_execute_before_area_columns = execute


def _productivity_add_area_columns(
    columns,
):

    columns = [
        dict(column)
        if hasattr(
            column,
            "get",
        )
        else column

        for column in (
            columns or []
        )
    ]


    existing = {
        str(
            column.get(
                "fieldname"
            )
            or ""
        ).strip()

        for column in columns

        if hasattr(
            column,
            "get",
        )
    }


    # Remove any duplicate if an earlier experiment exists.
    columns = [
        column
        for column in columns
        if not (
            hasattr(
                column,
                "get",
            )
            and str(
                column.get(
                    "fieldname"
                )
                or ""
            ).strip()
            in (
                "from_area",
                "to_area",
            )
        )
    ]


    from_column = {
        "label":
            "From Area",

        "fieldname":
            "from_area",

        "fieldtype":
            "Data",

        "width":
            150,
    }


    to_column = {
        "label":
            "To Area",

        "fieldname":
            "to_area",

        "fieldtype":
            "Data",

        "width":
            150,
    }


    result = []

    inserted = False


    for column in columns:

        result.append(
            column
        )


        if (
            hasattr(
                column,
                "get",
            )
            and str(
                column.get(
                    "fieldname"
                )
                or ""
            ).strip()
            == "material"
        ):

            result.append(
                from_column
            )

            result.append(
                to_column
            )

            inserted = True


    if not inserted:

        result.append(
            from_column
        )

        result.append(
            to_column
        )


    return result


def execute(
    filters=None,
):

    original_result = (
        _productivity_execute_before_area_columns(
            filters
        )
    )


    if not original_result:

        return original_result


    if isinstance(
        original_result,
        tuple,
    ):

        parts = list(
            original_result
        )

        return_tuple = True


    elif isinstance(
        original_result,
        list,
    ):

        parts = list(
            original_result
        )

        return_tuple = False


    else:

        return original_result


    if parts:

        parts[0] = (
            _productivity_add_area_columns(
                parts[0]
            )
        )


    if (
        len(parts) > 1
        and parts[1]
    ):

        new_rows = []


        for row in parts[1]:

            if hasattr(
                row,
                "get",
            ):

                row = dict(
                    row
                )


                row.setdefault(
                    "from_area",
                    ""
                )


                row.setdefault(
                    "to_area",
                    ""
                )


            new_rows.append(
                row
            )


        parts[1] = new_rows


    if return_tuple:

        return tuple(
            parts
        )


    return parts


# END KOSI_PRODUCTIVITY_FROM_TO_AREA_COLUMNS_V1



# ============================================================
# KOSI_PRODUCTIVITY_POPULATE_FROM_AREA_V1
#
# FROM AREA SOURCES
#
# Excavator:
#     Truck Loads.mining_areas_trucks
#     matched by asset_name_shoval + mat_type
#
# ADT:
#     Truck Loads.mining_areas_trucks
#     matched by asset_name_truck + mat_type
#
# Dozer:
#     Dozer Production.mining_areas_dozer_child
#     matched by asset_name + dozer_geo_mat_layer
#
# TO AREA:
#     Intentionally remains blank.
#
# If more than one area applies to a row, unique areas are
# displayed as:
#
#     Area 1 / Area 2
#
# Monthly Production mode uses each month's own start/end dates.
# ============================================================


_productivity_execute_before_from_area_values = execute


def _productivity_area_text(
    values,
):

    cleaned = sorted({
        str(value or "").strip()
        for value in (
            values or []
        )
        if str(
            value or ""
        ).strip()
    })


    return " / ".join(
        cleaned
    )


def _productivity_build_area_maps(
    site,
    start_date,
    end_date,
):

    maps = {
        "Excavator": {
            "machine_material": {},
            "material": {},
        },

        "ADT": {
            "machine_material": {},
            "material": {},
        },

        "Dozer": {
            "machine_material": {},
            "material": {},
        },
    }


    site = str(
        site or ""
    ).strip()

    start_date = str(
        start_date or ""
    ).strip()

    end_date = str(
        end_date or ""
    ).strip()


    if not (
        site
        and start_date
        and end_date
    ):

        return maps


    params = {
        "site":
            site,

        "start_date":
            start_date,

        "end_date":
            end_date,
    }


    # ========================================================
    # TRUCK LOADS
    #
    # Same Truck Load row supplies:
    #
    # Excavator -> asset_name_shoval
    # ADT       -> asset_name_truck
    # Material  -> mat_type
    # From Area -> mining_areas_trucks
    # ========================================================

    truck_rows = frappe.db.sql(
        """
        SELECT
            TRIM(
                IFNULL(
                    tl.asset_name_shoval,
                    ''
                )
            ) AS excavator,

            TRIM(
                IFNULL(
                    tl.asset_name_truck,
                    ''
                )
            ) AS adt,

            TRIM(
                IFNULL(
                    tl.mat_type,
                    ''
                )
            ) AS material,

            TRIM(
                IFNULL(
                    tl.mining_areas_trucks,
                    ''
                )
            ) AS area

        FROM `tabHourly Production` hp

        INNER JOIN `tabTruck Loads` tl
            ON tl.parent = hp.name

        WHERE
            hp.location = %(site)s

            AND hp.prod_date
                BETWEEN %(start_date)s
                AND %(end_date)s

            AND hp.docstatus < 2

            AND IFNULL(
                tl.bcms,
                0
            ) > 0

            AND TRIM(
                IFNULL(
                    tl.mining_areas_trucks,
                    ''
                )
            ) != ''
        """,
        params,
        as_dict=True,
    )


    for source in truck_rows:

        material = str(
            source.material
            or ""
        ).strip()

        area = str(
            source.area
            or ""
        ).strip()


        if not (
            material
            and area
        ):

            continue


        # ----------------------------------------------------
        # EXCAVATOR
        # ----------------------------------------------------

        excavator = str(
            source.excavator
            or ""
        ).strip()


        if excavator:

            key = (
                excavator,
                material,
            )


            maps[
                "Excavator"
            ][
                "machine_material"
            ].setdefault(
                key,
                set(),
            ).add(
                area
            )


            maps[
                "Excavator"
            ][
                "material"
            ].setdefault(
                material,
                set(),
            ).add(
                area
            )


        # ----------------------------------------------------
        # ADT
        # ----------------------------------------------------

        adt = str(
            source.adt
            or ""
        ).strip()


        if adt:

            key = (
                adt,
                material,
            )


            maps[
                "ADT"
            ][
                "machine_material"
            ].setdefault(
                key,
                set(),
            ).add(
                area
            )


            maps[
                "ADT"
            ][
                "material"
            ].setdefault(
                material,
                set(),
            ).add(
                area
            )


    # ========================================================
    # DOZER PRODUCTION
    #
    # Machine   -> asset_name
    # Material  -> dozer_geo_mat_layer
    # From Area -> mining_areas_dozer_child
    # ========================================================

    dozer_rows = frappe.db.sql(
        """
        SELECT
            TRIM(
                IFNULL(
                    dp.asset_name,
                    ''
                )
            ) AS machine,

            TRIM(
                IFNULL(
                    dp.dozer_geo_mat_layer,
                    ''
                )
            ) AS material,

            TRIM(
                IFNULL(
                    dp.mining_areas_dozer_child,
                    ''
                )
            ) AS area

        FROM `tabHourly Production` hp

        INNER JOIN `tabDozer Production` dp
            ON dp.parent = hp.name

        WHERE
            hp.location = %(site)s

            AND hp.prod_date
                BETWEEN %(start_date)s
                AND %(end_date)s

            AND hp.docstatus < 2

            AND IFNULL(
                dp.bcm_hour,
                0
            ) > 0

            AND TRIM(
                IFNULL(
                    dp.mining_areas_dozer_child,
                    ''
                )
            ) != ''
        """,
        params,
        as_dict=True,
    )


    for source in dozer_rows:

        machine = str(
            source.machine
            or ""
        ).strip()

        material = str(
            source.material
            or ""
        ).strip()

        area = str(
            source.area
            or ""
        ).strip()


        if not (
            material
            and area
        ):

            continue


        if machine:

            key = (
                machine,
                material,
            )


            maps[
                "Dozer"
            ][
                "machine_material"
            ].setdefault(
                key,
                set(),
            ).add(
                area
            )


        maps[
            "Dozer"
        ][
            "material"
        ].setdefault(
            material,
            set(),
        ).add(
            area
        )


    return maps


def _productivity_populate_from_area(
    rows,
    filters,
):

    filters = frappe._dict(
        filters or {}
    )


    output = []

    cache = {}

    current_category = None


    valid_categories = (
        "Excavator",
        "ADT",
        "Dozer",
    )


    for original_row in (
        rows or []
    ):

        if not hasattr(
            original_row,
            "get",
        ):

            output.append(
                original_row
            )

            continue


        row = dict(
            original_row
        )


        row[
            "to_area"
        ] = ""


        # ----------------------------------------------------
        # MONTHLY HEADING
        # ----------------------------------------------------

        if int(
            row.get(
                "is_monthly_plan_header"
            )
            or 0
        ) == 1:

            current_category = None

            row[
                "from_area"
            ] = ""

            output.append(
                row
            )

            continue


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        # ----------------------------------------------------
        # CATEGORY HEADING
        # ----------------------------------------------------

        if (
            label in valid_categories
            and int(
                row.get(
                    "is_category_total"
                )
                or 0
            ) == 1
        ):

            current_category = label

            row[
                "from_area"
            ] = ""

            output.append(
                row
            )

            continue


        # ----------------------------------------------------
        # TOTAL FLEET
        # ----------------------------------------------------

        if int(
            row.get(
                "is_total_fleet"
            )
            or 0
        ) == 1:

            row[
                "from_area"
            ] = ""

            output.append(
                row
            )

            continue


        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()


        # Blank material rows are working-hour reconciliation
        # rows, not production area rows.
        if not material:

            row[
                "from_area"
            ] = ""

            output.append(
                row
            )

            continue


        category = str(
            row.get(
                "category"
            )
            or current_category
            or ""
        ).strip()


        if category not in valid_categories:

            row[
                "from_area"
            ] = ""

            output.append(
                row
            )

            continue


        # ----------------------------------------------------
        # DATE RANGE
        #
        # Multi-month rows carry their own period.
        # Normal report uses selected Start / End Date.
        # ----------------------------------------------------

        start_date = (
            row.get(
                "monthly_start_date"
            )
            or filters.get(
                "start_date"
            )
        )


        end_date = (
            row.get(
                "monthly_end_date"
            )
            or filters.get(
                "end_date"
            )
        )


        site = (
            filters.get(
                "site"
            )
            or ""
        )


        cache_key = (
            str(site or "").strip(),
            str(start_date or "").strip(),
            str(end_date or "").strip(),
        )


        if cache_key not in cache:

            cache[
                cache_key
            ] = (
                _productivity_build_area_maps(
                    cache_key[0],
                    cache_key[1],
                    cache_key[2],
                )
            )


        area_maps = cache[
            cache_key
        ]


        # ----------------------------------------------------
        # DETERMINE MACHINE
        #
        # Summary Per Machine rows normally retain "machine".
        #
        # As a fallback:
        #     label = machine
        #     material = Coal / Hards / etc.
        #
        # Hours and Material rows normally have:
        #     label = material
        # ----------------------------------------------------

        machine = str(
            row.get(
                "machine"
            )
            or ""
        ).strip()


        if (
            not machine
            and label
            and label != material
            and label not in valid_categories
            and label != "Total Fleet"
        ):

            machine = label


        areas = set()


        # ----------------------------------------------------
        # SUMMARY PER MACHINE
        # ----------------------------------------------------

        if machine:

            areas.update(
                area_maps[
                    category
                ][
                    "machine_material"
                ].get(
                    (
                        machine,
                        material,
                    ),
                    set(),
                )
            )


        # ----------------------------------------------------
        # HOURS AND MATERIAL
        #
        # Or fallback if a machine-level row has no exact
        # source match.
        # ----------------------------------------------------

        if not areas:

            areas.update(
                area_maps[
                    category
                ][
                    "material"
                ].get(
                    material,
                    set(),
                )
            )


        row[
            "from_area"
        ] = (
            _productivity_area_text(
                areas
            )
        )


        output.append(
            row
        )


    return output


def execute(
    filters=None,
):

    original_result = (
        _productivity_execute_before_from_area_values(
            filters
        )
    )


    if not original_result:

        return original_result


    if isinstance(
        original_result,
        tuple,
    ):

        parts = list(
            original_result
        )

        return_tuple = True


    elif isinstance(
        original_result,
        list,
    ):

        parts = list(
            original_result
        )

        return_tuple = False


    else:

        return original_result


    if (
        len(parts) > 1
        and parts[1]
    ):

        parts[1] = (
            _productivity_populate_from_area(
                parts[1],
                filters,
            )
        )


    if return_tuple:

        return tuple(
            parts
        )


    return parts


# END KOSI_PRODUCTIVITY_POPULATE_FROM_AREA_V1



# ============================================================
# KOSI_PRODUCTIVITY_AUTO_FROM_AREA_WIDTH_V1
#
# Automatically resize From Area according to the longest
# visible area description in the current report.
#
# Minimum:
#     180 px
#
# Maximum:
#     650 px
#
# Examples:
#
# Ramp 3
#     -> small column
#
# Ramp 1 / Ramp 2 / Ramp 3
#     -> wider column
#
# Many areas
#     -> grows automatically, capped at 650 px
#
# ============================================================


_productivity_execute_before_auto_from_area_width = execute


def _productivity_auto_from_area_width(
    columns,
    rows,
):

    columns = [
        dict(column)
        if hasattr(
            column,
            "get",
        )
        else column

        for column in (
            columns or []
        )
    ]


    longest_text = "From Area"


    for row in (
        rows or []
    ):

        if not hasattr(
            row,
            "get",
        ):
            continue


        value = str(
            row.get(
                "from_area"
            )
            or ""
        ).strip()


        if (
            len(value)
            > len(longest_text)
        ):

            longest_text = value


    # --------------------------------------------------------
    # APPROXIMATE PIXEL WIDTH
    #
    # Around 8 pixels per character plus padding.
    # --------------------------------------------------------

    calculated_width = (
        len(
            longest_text
        )
        * 8
        + 50
    )


    final_width = max(
        180,
        calculated_width,
    )


    final_width = min(
        final_width,
        650,
    )


    for column in columns:

        if not hasattr(
            column,
            "get",
        ):
            continue


        if str(
            column.get(
                "fieldname"
            )
            or ""
        ).strip() == "from_area":

            column[
                "width"
            ] = final_width


    return (
        columns,
        final_width,
        longest_text,
    )


def execute(
    filters=None,
):

    original_result = (
        _productivity_execute_before_auto_from_area_width(
            filters
        )
    )


    if not original_result:

        return original_result


    if isinstance(
        original_result,
        tuple,
    ):

        parts = list(
            original_result
        )

        return_tuple = True


    elif isinstance(
        original_result,
        list,
    ):

        parts = list(
            original_result
        )

        return_tuple = False


    else:

        return original_result


    columns = (
        parts[0]
        if len(parts) > 0
        else []
    )


    rows = (
        parts[1]
        if len(parts) > 1
        else []
    )


    (
        columns,
        from_area_width,
        longest_from_area,
    ) = _productivity_auto_from_area_width(
        columns,
        rows,
    )


    parts[0] = columns


    if return_tuple:

        return tuple(
            parts
        )


    return parts


# END KOSI_PRODUCTIVITY_AUTO_FROM_AREA_WIDTH_V1



# ============================================================
# KOSI_PRODUCTIVITY_EDITABLE_AREA_OVERRIDE_V1
#
# Editable report fields:
#
#     From Area
#     To Area
#     Hauling Distance (M)
#
# Source report data is NOT changed.
#
# Manual edits are stored in:
#
#     Productivity Area Override
#
# A saved override always takes precedence over automatically
# derived From Area values.
# ============================================================


_productivity_execute_before_editable_area_override = execute


def _productivity_manual_edit_key(
    site,
    start_date,
    end_date,
    shift,
    monthly_production_plan,
    category,
    machine,
    material,
):

    import hashlib

    parts = [
        str(site or "").strip(),
        str(start_date or "").strip(),
        str(end_date or "").strip(),
        str(shift or "").strip(),
        str(monthly_production_plan or "").strip(),
        str(category or "").strip(),
        str(machine or "").strip(),
        str(material or "").strip(),
    ]

    raw = "|".join(parts)

    return hashlib.sha1(
        raw.encode("utf-8")
    ).hexdigest()


def _productivity_prepare_editable_rows(
    rows,
    filters,
):

    filters = frappe._dict(
        filters or {}
    )

    result = []

    current_category = None
    current_machine = None

    valid_categories = (
        "Excavator",
        "ADT",
        "Dozer",
    )

    row_keys = []


    # ========================================================
    # FIRST PASS
    # Build stable keys for editable material rows.
    # ========================================================

    for original_row in (
        rows or []
    ):

        if not hasattr(
            original_row,
            "get",
        ):

            result.append(
                original_row
            )

            continue


        row = dict(
            original_row
        )


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        # ----------------------------------------------------
        # MONTH HEADING
        # ----------------------------------------------------

        if int(
            row.get(
                "is_monthly_plan_header"
            )
            or 0
        ) == 1:

            current_category = None
            current_machine = None

            row[
                "productivity_editable"
            ] = 0

            result.append(
                row
            )

            continue


        # ----------------------------------------------------
        # CATEGORY HEADING
        # ----------------------------------------------------

        if (
            label in valid_categories
            and int(
                row.get(
                    "is_category_total"
                )
                or 0
            ) == 1
        ):

            current_category = label
            current_machine = None

            row[
                "productivity_editable"
            ] = 0

            result.append(
                row
            )

            continue


        # ----------------------------------------------------
        # TOTAL FLEET
        # ----------------------------------------------------

        if int(
            row.get(
                "is_total_fleet"
            )
            or 0
        ) == 1:

            current_machine = None

            row[
                "productivity_editable"
            ] = 0

            result.append(
                row
            )

            continue


        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()


        category = str(
            row.get(
                "category"
            )
            or current_category
            or ""
        ).strip()


        # ----------------------------------------------------
        # MACHINE TOTAL / MACHINE HEADING
        # ----------------------------------------------------

        if (
            not material
            and category in valid_categories
            and label
        ):

            current_machine = label

            row[
                "productivity_editable"
            ] = 0

            result.append(
                row
            )

            continue


        # Only real material rows are manually editable.
        if (
            not material
            or category not in valid_categories
        ):

            row[
                "productivity_editable"
            ] = 0

            result.append(
                row
            )

            continue


        machine = str(
            row.get(
                "machine"
            )
            or ""
        ).strip()


        if (
            not machine
            and label
            and label != material
            and label not in valid_categories
        ):

            machine = label


        if not machine:

            machine = str(
                current_machine
                or ""
            ).strip()


        start_date = (
            row.get(
                "monthly_start_date"
            )
            or filters.get(
                "start_date"
            )
            or ""
        )


        end_date = (
            row.get(
                "monthly_end_date"
            )
            or filters.get(
                "end_date"
            )
            or ""
        )


        monthly_plan = str(
            row.get(
                "monthly_production_plan"
            )
            or ""
        ).strip()


        site = str(
            filters.get(
                "site"
            )
            or ""
        ).strip()


        shift = str(
            filters.get(
                "shift"
            )
            or ""
        ).strip()


        row_key = (
            _productivity_manual_edit_key(
                site,
                start_date,
                end_date,
                shift,
                monthly_plan,
                category,
                machine,
                material,
            )
        )


        row[
            "productivity_editable"
        ] = 1


        row[
            "productivity_edit_key"
        ] = row_key


        row[
            "productivity_edit_site"
        ] = site


        row[
            "productivity_edit_start_date"
        ] = str(
            start_date or ""
        )


        row[
            "productivity_edit_end_date"
        ] = str(
            end_date or ""
        )


        row[
            "productivity_edit_shift"
        ] = shift


        row[
            "productivity_edit_monthly_plan"
        ] = monthly_plan


        row[
            "productivity_edit_category"
        ] = category


        row[
            "productivity_edit_machine"
        ] = machine


        row[
            "productivity_edit_material"
        ] = material


        row_keys.append(
            row_key
        )


        result.append(
            row
        )


    # ========================================================
    # LOAD SAVED OVERRIDES IN ONE QUERY
    # ========================================================

    override_map = {}


    if (
        row_keys
        and frappe.db.exists(
            "DocType",
            "Productivity Area Override",
        )
    ):

        saved_rows = frappe.get_all(
            "Productivity Area Override",

            filters={
                "row_key": [
                    "in",
                    list(
                        set(
                            row_keys
                        )
                    ),
                ],
            },

            fields=[
                "row_key",
                "from_area",
                "to_area",
                "hauling_distance_m",
            ],

            limit_page_length=0,
        )


        override_map = {
            saved.row_key:
                saved

            for saved in saved_rows
        }


    # ========================================================
    # SAVED VALUES OVERRIDE AUTOMATIC VALUES
    # ========================================================

    for row in result:

        if not hasattr(
            row,
            "get",
        ):
            continue


        row_key = row.get(
            "productivity_edit_key"
        )


        if not row_key:
            continue


        saved = override_map.get(
            row_key
        )


        if not saved:
            continue


        row[
            "from_area"
        ] = str(
            saved.from_area
            or ""
        )


        row[
            "to_area"
        ] = str(
            saved.to_area
            or ""
        )


        row[
            "hauling_distance_m"
        ] = str(
            saved.hauling_distance_m
            or ""
        ).strip()


        row[
            "productivity_manual_override"
        ] = 1


    return result


def _productivity_resize_manual_area_columns(
    columns,
    rows,
):

    columns = [
        dict(column)
        if hasattr(
            column,
            "get",
        )
        else column

        for column in (
            columns or []
        )
    ]


    for fieldname in (
        "from_area",
        "to_area",
    ):

        longest = fieldname.replace(
            "_",
            " ",
        ).title()


        for row in (
            rows or []
        ):

            if not hasattr(
                row,
                "get",
            ):
                continue


            value = str(
                row.get(
                    fieldname
                )
                or ""
            )


            if len(value) > len(longest):

                longest = value


        width = min(
            650,
            max(
                180,
                len(longest) * 8 + 50,
            ),
        )


        for column in columns:

            if (
                hasattr(
                    column,
                    "get",
                )
                and column.get(
                    "fieldname"
                ) == fieldname
            ):

                column[
                    "width"
                ] = width


    return columns


@frappe.whitelist()
def save_productivity_area_override(
    row_key,
    site="",
    start_date="",
    end_date="",
    shift="",
    monthly_production_plan="",
    category="",
    machine="",
    material="",
    from_area="",
    to_area="",
    hauling_distance_m=0,
):

    from frappe.utils import flt


    if frappe.session.user == "Guest":

        frappe.throw(
            "Please log in before editing Productivity.",
            frappe.PermissionError,
        )


    # Users allowed to edit Hourly Production may save
    # Productivity area overrides.
    if (
        "System Manager"
        not in frappe.get_roles(
            frappe.session.user
        )
        and not frappe.has_permission(
            "Hourly Production",
            ptype="write",
        )
    ):

        frappe.throw(
            "You do not have permission to edit "
            "Productivity area information.",
            frappe.PermissionError,
        )


    expected_key = (
        _productivity_manual_edit_key(
            site,
            start_date,
            end_date,
            shift,
            monthly_production_plan,
            category,
            machine,
            material,
        )
    )


    # KOSI_PRODUCTIVITY_SCOPED_SAVE_KEY_V12
    #
    # Current Productivity rows may use:
    #
    #     VIEW::Hours and Material::<base key>
    #
    # or:
    #
    #     VIEW::Summary Per Machine::<base key>
    #
    # The BASE key must still match the server-generated
    # machine/material key, but the FULL scoped key must be
    # retained for permanent storage so the two report views
    # remain completely separate.

    submitted_row_key = str(
        row_key
        or ""
    ).strip()


    storage_key = submitted_row_key
    submitted_base_key = submitted_row_key
    summary_view = ""


    if submitted_row_key.startswith(
        "VIEW::"
    ):

        key_parts = submitted_row_key.split(
            "::",
            2,
        )


        if (
            len(key_parts) != 3
            or not str(
                key_parts[1]
                or ""
            ).strip()
            or not str(
                key_parts[2]
                or ""
            ).strip()
        ):

            frappe.throw(
                "Invalid Productivity report row key."
            )


        summary_view = str(
            key_parts[1]
            or ""
        ).strip()


        submitted_base_key = str(
            key_parts[2]
            or ""
        ).strip()


        if summary_view not in (
            "Hours and Material",
            "Summary Per Machine",
        ):

            frappe.throw(
                "Invalid Productivity report view."
            )


    if submitted_base_key != expected_key:

        frappe.throw(
            "Productivity row key does not match "
            "the selected report row."
        )


    existing_override_name = frappe.db.get_value(
        "Productivity Area Override",
        {
            "row_key":
                storage_key,
        },
        "name",
    )


    if existing_override_name:

        doc = frappe.get_doc(
            "Productivity Area Override",
            existing_override_name,
        )

    else:

        doc = frappe.get_doc({
            "doctype":
                "Productivity Area Override",

            "row_key":
                storage_key,

            "site":
                site,

            "start_date":
                start_date or None,

            "end_date":
                end_date or None,

            "shift":
                shift,

            "monthly_production_plan":
                monthly_production_plan or None,

            "category":
                category,

            "machine":
                machine,

            "material":
                material,
        })


    # Always keep the permanent override attached to the
    # exact report-view-scoped row.
    doc.row_key = storage_key


    doc.from_area = str(
        from_area or ""
    ).strip()


    doc.to_area = str(
        to_area or ""
    ).strip()


    doc.hauling_distance_m = str(
        hauling_distance_m
        or ""
    ).strip()


    if doc.is_new():

        doc.insert(
            ignore_permissions=True
        )

    else:

        doc.save(
            ignore_permissions=True
        )


    return {
        "saved":
            True,

        # KOSI_PRODUCTIVITY_SAVE_RETURN_SCOPED_KEY_V13
        "row_key":
            storage_key,

        "from_area":
            doc.from_area,

        "to_area":
            doc.to_area,

        "hauling_distance_m":
            doc.hauling_distance_m,
    }


def execute(
    filters=None,
):

    original_result = (
        _productivity_execute_before_editable_area_override(
            filters
        )
    )


    if not original_result:

        return original_result


    if isinstance(
        original_result,
        tuple,
    ):

        parts = list(
            original_result
        )

        return_tuple = True


    elif isinstance(
        original_result,
        list,
    ):

        parts = list(
            original_result
        )

        return_tuple = False


    else:

        return original_result


    if (
        len(parts) > 1
        and parts[1]
    ):

        parts[1] = (
            _productivity_prepare_editable_rows(
                parts[1],
                filters,
            )
        )


    if parts:

        parts[0] = (
            _productivity_resize_manual_area_columns(
                parts[0],
                (
                    parts[1]
                    if len(parts) > 1
                    else []
                ),
            )
        )


    if return_tuple:

        return tuple(
            parts
        )


    return parts


# END KOSI_PRODUCTIVITY_EDITABLE_AREA_OVERRIDE_V1



# ============================================================
# KOSI_PRODUCTIVITY_CATEGORY_DISTANCE_TOTAL_V1
#
# HOURS AND MATERIAL:
#
# Sum the material Hauling Distance into the bold category row.
#
# Example:
#
# Excavator
#     Hards   20
#     Softs   20
#     Coal    20
#
# Excavator total = 60
#
# Works independently for:
#
#     Excavator
#     ADT
#     Dozer
#
# Monthly Production periods are handled separately because
# each month starts with its own month-header row.
# ============================================================


_productivity_execute_before_category_distance_total = execute


def _productivity_number_for_distance(
    value,
):

    try:

        return float(
            str(
                value or 0
            ).replace(
                ",",
                "",
            )
        )

    except (
        TypeError,
        ValueError,
    ):

        return 0.0


def _productivity_add_category_distance_totals(
    rows,
    filters=None,
):

    rows = [
        dict(row)
        if hasattr(
            row,
            "get",
        )
        else row

        for row in (
            rows or []
        )
    ]


    filters = frappe._dict(
        filters or {}
    )


    # Only Hours and Material has one summary material row
    # per category. This prevents double-counting distances
    # in Summary Per Machine.
    summary_view = str(
        filters.get(
            "summary_view"
        )
        or ""
    ).strip()


    if (
        summary_view
        != "Hours and Material"
    ):

        return rows


    categories = (
        "Excavator",
        "ADT",
        "Dozer",
    )


    current_category_index = None
    current_total = 0.0


    def finish_category():

        nonlocal current_category_index
        nonlocal current_total

        if current_category_index is None:
            return


        value = round(
            current_total,
            3,
        )


        if float(
            value
        ).is_integer():

            value = int(
                value
            )


        rows[
            current_category_index
        ][
            "hauling_distance_m"
        ] = value


        current_category_index = None
        current_total = 0.0


    for index, row in enumerate(
        rows
    ):

        if not hasattr(
            row,
            "get",
        ):
            continue


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()


        # ----------------------------------------------------
        # NEW MONTH
        # ----------------------------------------------------

        if int(
            row.get(
                "is_monthly_plan_header"
            )
            or 0
        ) == 1:

            finish_category()

            continue


        # ----------------------------------------------------
        # CATEGORY TOTAL ROW
        # ----------------------------------------------------

        if (
            label in categories
            and int(
                row.get(
                    "is_category_total"
                )
                or 0
            ) == 1
        ):

            finish_category()

            current_category_index = index
            current_total = 0.0

            continue


        # ----------------------------------------------------
        # TOTAL FLEET ENDS CURRENT CATEGORY
        # ----------------------------------------------------

        if int(
            row.get(
                "is_total_fleet"
            )
            or 0
        ) == 1:

            finish_category()

            continue


        if current_category_index is None:
            continue


        # ----------------------------------------------------
        # HOURS AND MATERIAL DETAIL ROW
        #
        # Example:
        #
        # label    = Hards
        # material = Hards
        #
        # This prevents machine-level rows being counted.
        # ----------------------------------------------------

        if (
            material
            and label == material
        ):

            current_total += (
                _productivity_number_for_distance(
                    row.get(
                        "hauling_distance_m"
                    )
                )
            )


    finish_category()


    return rows


def execute(
    filters=None,
):

    original_result = (
        _productivity_execute_before_category_distance_total(
            filters
        )
    )


    if not original_result:

        return original_result


    if isinstance(
        original_result,
        tuple,
    ):

        parts = list(
            original_result
        )

        return_tuple = True


    elif isinstance(
        original_result,
        list,
    ):

        parts = list(
            original_result
        )

        return_tuple = False


    else:

        return original_result


    if (
        len(parts) > 1
        and parts[1]
    ):

        parts[1] = (
            _productivity_add_category_distance_totals(
                parts[1],
                filters,
            )
        )


    if return_tuple:

        return tuple(
            parts
        )


    return parts


# END KOSI_PRODUCTIVITY_CATEGORY_DISTANCE_TOTAL_V1



# ============================================================
# KOSI_PRODUCTIVITY_MACHINE_DISTANCE_TOTAL_V1
#
# SUMMARY PER MACHINE:
#
# Each machine total row gets the sum of the hauling distance
# entered on its Machine + Material detail rows.
#
# Example:
#
# EX01                     40
#     EX01 / Hards         20
#     EX01 / Softs         20
#
# Category total then becomes:
#
# Excavator = sum of all Excavator machine totals
#
# This runs separately for:
#
#     Excavator
#     ADT
#     Dozer
#
# and separately for every Monthly Production period.
# ============================================================


_productivity_execute_before_machine_distance_total = execute


def _productivity_machine_distance_number(
    value,
):

    try:

        return float(
            str(
                value or 0
            ).replace(
                ",",
                "",
            )
        )

    except (
        TypeError,
        ValueError,
    ):

        return 0.0


def _productivity_clean_distance_total(
    value,
):

    value = round(
        float(
            value or 0
        ),
        3,
    )


    if value.is_integer():

        return int(
            value
        )


    return value


def _productivity_add_machine_distance_totals(
    rows,
    filters=None,
):

    filters = frappe._dict(
        filters or {}
    )


    summary_view = str(
        filters.get(
            "summary_view"
        )
        or ""
    ).strip()


    if (
        summary_view
        != "Summary Per Machine"
    ):

        return rows


    rows = [
        dict(row)
        if hasattr(
            row,
            "get",
        )
        else row

        for row in (
            rows or []
        )
    ]


    categories = (
        "Excavator",
        "ADT",
        "Dozer",
    )


    current_category = None
    current_category_row_index = None

    current_machine = None
    current_machine_row_index = None
    current_machine_distance = 0.0

    category_distance = 0.0


    def finish_machine():

        nonlocal current_machine
        nonlocal current_machine_row_index
        nonlocal current_machine_distance
        nonlocal category_distance


        if (
            current_machine_row_index
            is None
        ):

            return


        total = (
            _productivity_clean_distance_total(
                current_machine_distance
            )
        )


        rows[
            current_machine_row_index
        ][
            "hauling_distance_m"
        ] = total


        category_distance += float(
            current_machine_distance
            or 0
        )


        current_machine = None
        current_machine_row_index = None
        current_machine_distance = 0.0


    def finish_category():

        nonlocal current_category
        nonlocal current_category_row_index
        nonlocal category_distance


        finish_machine()


        if (
            current_category_row_index
            is not None
        ):

            rows[
                current_category_row_index
            ][
                "hauling_distance_m"
            ] = (
                _productivity_clean_distance_total(
                    category_distance
                )
            )


        current_category = None
        current_category_row_index = None
        category_distance = 0.0


    for index, row in enumerate(
        rows
    ):

        if not hasattr(
            row,
            "get",
        ):

            continue


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()


        # ----------------------------------------------------
        # NEW MONTH
        # ----------------------------------------------------

        if int(
            row.get(
                "is_monthly_plan_header"
            )
            or 0
        ) == 1:

            finish_category()

            continue


        # ----------------------------------------------------
        # CATEGORY ROW
        # ----------------------------------------------------

        if (
            label in categories
            and int(
                row.get(
                    "is_category_total"
                )
                or 0
            ) == 1
        ):

            finish_category()


            current_category = label
            current_category_row_index = index
            category_distance = 0.0


            continue


        # ----------------------------------------------------
        # TOTAL FLEET
        # ----------------------------------------------------

        if int(
            row.get(
                "is_total_fleet"
            )
            or 0
        ) == 1:

            finish_category()

            continue


        if not current_category:

            continue


        # ----------------------------------------------------
        # MACHINE SUMMARY ROW
        #
        # Example:
        #
        # EX01       Material blank
        #
        # The following EX01 material rows belong to it.
        # ----------------------------------------------------

        if (
            label
            and not material
        ):

            finish_machine()


            current_machine = label
            current_machine_row_index = index
            current_machine_distance = 0.0


            continue


        # ----------------------------------------------------
        # MACHINE + MATERIAL DETAIL
        #
        # Example:
        #
        # EX01       Hards       20
        # EX01       Softs       20
        # ----------------------------------------------------

        if (
            current_machine
            and label == current_machine
            and material
        ):

            current_machine_distance += (
                _productivity_machine_distance_number(
                    row.get(
                        "hauling_distance_m"
                    )
                )
            )


    finish_category()


    return rows


def _productivity_add_total_fleet_distance(
    rows,
):

    rows = [
        dict(row)
        if hasattr(
            row,
            "get",
        )
        else row

        for row in (
            rows or []
        )
    ]


    excavator_distance = 0.0
    dozer_distance = 0.0

    have_excavator = False
    have_dozer = False


    # Reset for each monthly section.
    month_start = 0


    def apply_section(
        start,
        end,
    ):

        excavator = 0.0
        dozer = 0.0

        found_excavator = False
        found_dozer = False


        for i in range(
            start,
            end,
        ):

            row = rows[i]

            if not hasattr(
                row,
                "get",
            ):

                continue


            label = str(
                row.get(
                    "label"
                )
                or ""
            ).strip()


            if (
                label == "Excavator"
                and int(
                    row.get(
                        "is_category_total"
                    )
                    or 0
                ) == 1
            ):

                excavator = (
                    _productivity_machine_distance_number(
                        row.get(
                            "hauling_distance_m"
                        )
                    )
                )

                found_excavator = True


            elif (
                label == "Dozer"
                and int(
                    row.get(
                        "is_category_total"
                    )
                    or 0
                ) == 1
            ):

                dozer = (
                    _productivity_machine_distance_number(
                        row.get(
                            "hauling_distance_m"
                        )
                    )
                )

                found_dozer = True


        for i in range(
            start,
            end,
        ):

            row = rows[i]

            if not hasattr(
                row,
                "get",
            ):

                continue


            if int(
                row.get(
                    "is_total_fleet"
                )
                or 0
            ) == 1:

                row[
                    "hauling_distance_m"
                ] = (
                    _productivity_clean_distance_total(
                        (
                            excavator
                            if found_excavator
                            else 0
                        )
                        +
                        (
                            dozer
                            if found_dozer
                            else 0
                        )
                    )
                )


    for index, row in enumerate(
        rows
    ):

        if not hasattr(
            row,
            "get",
        ):

            continue


        if (
            index > 0
            and int(
                row.get(
                    "is_monthly_plan_header"
                )
                or 0
            ) == 1
        ):

            apply_section(
                month_start,
                index,
            )

            month_start = index


    apply_section(
        month_start,
        len(
            rows
        ),
    )


    return rows


def execute(
    filters=None,
):

    original_result = (
        _productivity_execute_before_machine_distance_total(
            filters
        )
    )


    if not original_result:

        return original_result


    if isinstance(
        original_result,
        tuple,
    ):

        parts = list(
            original_result
        )

        return_tuple = True


    elif isinstance(
        original_result,
        list,
    ):

        parts = list(
            original_result
        )

        return_tuple = False


    else:

        return original_result


    if (
        len(parts) > 1
        and parts[1]
    ):

        parts[1] = (
            _productivity_add_machine_distance_totals(
                parts[1],
                filters,
            )
        )


        parts[1] = (
            _productivity_add_total_fleet_distance(
                parts[1]
            )
        )


    if return_tuple:

        return tuple(
            parts
        )


    return parts


# END KOSI_PRODUCTIVITY_MACHINE_DISTANCE_TOTAL_V1



# ============================================================
# KOSI_PRODUCTIVITY_FULL_OVERRIDE_SNAPSHOT_V1
#
# SAVE OVERRIDE now creates a PERMANENT Productivity snapshot.
#
# Snapshot contains the complete calculated report AFTER saved
# manual overrides have been applied.
#
# Stored permanently in:
#
#     Productivity Override Snapshot
#
# Also creates a private PDF attachment.
# ============================================================


def _productivity_snapshot_json_load(
    value,
):

    import json


    if not value:
        return {}


    if isinstance(
        value,
        dict,
    ):

        return value


    try:

        return json.loads(
            value
        )

    except Exception:

        frappe.throw(
            "Invalid Productivity snapshot filters."
        )


def _productivity_snapshot_text(
    value,
):

    if value is None:
        return ""


    if isinstance(
        value,
        (
            list,
            tuple,
            set,
        ),
    ):

        values = []


        for item in value:

            if isinstance(
                item,
                dict,
            ):

                item = (
                    item.get("value")
                    or item.get("name")
                    or ""
                )


            item = str(
                item or ""
            ).strip()


            if item:
                values.append(
                    item
                )


        return ", ".join(
            values
        )


    return str(
        value
    ).strip()


def _productivity_snapshot_number(
    value,
):

    try:

        return float(
            str(
                value or 0
            ).replace(
                ",",
                "",
            )
        )

    except (
        TypeError,
        ValueError,
    ):

        return 0.0


def _productivity_snapshot_format_number(
    value,
):

    number = (
        _productivity_snapshot_number(
            value
        )
    )


    return "{:,.0f}".format(
        number
    )


def _productivity_snapshot_build_html(
    filters,
    columns,
    rows,
    saved_by,
    saved_at,
):

    import html


    visible_columns = []


    for column in (
        columns or []
    ):

        if not hasattr(
            column,
            "get",
        ):

            continue


        if column.get(
            "hidden"
        ):

            continue


        fieldname = str(
            column.get(
                "fieldname"
            )
            or ""
        ).strip()


        if not fieldname:
            continue


        # Internal fields must never appear in the snapshot.
        # KOSI_PRODUCTIVITY_SNAPSHOT_BCM_HD_V1
        #
        # Most productivity_* fields are internal report
        # metadata and must not appear in the PDF.
        #
        # productivity_bcm_hd is different: it is a real
        # user-facing report column and must be included.
        if (
            fieldname.startswith(
                "productivity_"
            )
            and fieldname
                != "productivity_bcm_hd"
        ):

            continue


        visible_columns.append(
            dict(
                column
            )
        )


    numeric_fields = {
        "working_hours",
        "output",
        "adjusted_bcm",
        "tallies_bcm",
        "productivity",
    }


    def esc(
        value,
    ):

        return html.escape(
            str(
                value
                if value is not None
                else ""
            )
        )


    def cell_value(
        row,
        fieldname,
    ):

        value = row.get(
            fieldname
        )


        if fieldname in numeric_fields:

            if (
                value is None
                or value == ""
            ):

                return ""


            return (
                _productivity_snapshot_format_number(
                    value
                )
            )


        return esc(
            value
        )


    filter_items = []


    filter_labels = [
        (
            "start_date",
            "Start Date",
        ),
        (
            "end_date",
            "End Date",
        ),
        (
            "site",
            "Site",
        ),
        (
            "shift",
            "Shift",
        ),
        (
            "machine_type",
            "Machine Type",
        ),
        (
            "asset",
            "Asset",
        ),
        (
            "monthly_production_plans",
            "Monthly Production",
        ),
        (
            "bcm_basis",
            "BCM Basis",
        ),
        (
            "summary_view",
            "Summary",
        ),
    ]


    for fieldname, label in filter_labels:

        value = (
            _productivity_snapshot_text(
                filters.get(
                    fieldname
                )
            )
        )


        if not value:
            continue


        filter_items.append(
            f"""
            <div class="filter-box">
                <div class="filter-label">
                    {esc(label)}
                </div>
                <div class="filter-value">
                    {esc(value)}
                </div>
            </div>
            """
        )


    headers = []


    for column in visible_columns:

        headers.append(
            "<th>"
            + esc(
                column.get(
                    "label"
                )
                or column.get(
                    "fieldname"
                )
            )
            + "</th>"
        )


    body = []


    for row in (
        rows or []
    ):

        if not hasattr(
            row,
            "get",
        ):

            continue


        is_month = int(
            row.get(
                "is_monthly_plan_header"
            )
            or 0
        ) == 1


        is_category = int(
            row.get(
                "is_category_total"
            )
            or 0
        ) == 1


        is_fleet = int(
            row.get(
                "is_total_fleet"
            )
            or 0
        ) == 1


        row_classes = []


        if is_month:
            row_classes.append(
                "monthly-row"
            )


        if is_category:
            row_classes.append(
                "category-row"
            )


        if is_fleet:
            row_classes.append(
                "fleet-row"
            )


        cells = []


        for column in visible_columns:

            fieldname = (
                column.get(
                    "fieldname"
                )
            )


            value = (
                cell_value(
                    row,
                    fieldname,
                )
            )


            numeric_class = (
                " num"
                if fieldname
                in numeric_fields
                else ""
            )


            cells.append(
                f'<td class="{numeric_class.strip()}">'
                f"{value}"
                "</td>"
            )


        body.append(
            '<tr class="'
            + " ".join(
                row_classes
            )
            + '">'
            + "".join(
                cells
            )
            + "</tr>"
        )


    title = (
        "Productivity Override Snapshot"
    )


    return f"""
<!doctype html>

<html>

<head>

<meta charset="utf-8">

<style>

@page {{
    size: A3 landscape;
    margin: 7mm;
}}


* {{
    box-sizing: border-box;
}}


body {{
    font-family:
        Arial,
        Helvetica,
        sans-serif;

    font-size: 9px;
    color: #111827;
    margin: 0;
    padding: 0;
}}


.report-title {{
    font-size: 18px;
    font-weight: 700;
    margin-bottom: 3px;
}}


.report-meta {{
    font-size: 9px;
    color: #4b5563;
    margin-bottom: 10px;
}}


.filters {{
    display: block;
    margin-bottom: 10px;
}}


.filter-box {{
    display: inline-block;
    vertical-align: top;
    min-width: 120px;
    margin-right: 5px;
    margin-bottom: 5px;
    padding: 4px 6px;
    border: 1px solid #d1d5db;
}}


.filter-label {{
    font-size: 7px;
    color: #6b7280;
    text-transform: uppercase;
}}


.filter-value {{
    font-size: 9px;
    font-weight: 600;
}}


table {{
    width: 100%;
    border-collapse: collapse;
    table-layout: auto;
}}


thead {{
    display: table-header-group;
}}


th {{
    border: 1px solid #d1d5db;
    background: #f3f4f6;
    font-weight: 700;
    text-align: left;
    padding: 4px 5px;
    white-space: nowrap;
}}


td {{
    border: 1px solid #d9dde2;
    padding: 4px 5px;
    vertical-align: middle;
    white-space: nowrap;
}}


td.num {{
    text-align: right;
}}


.monthly-row td {{
    font-weight: 700;
    background: #f8fafc;
}}


.category-row td {{
    font-weight: 700;
}}


.fleet-row td {{
    font-weight: 700;
    border-top: 2px solid #6b7280;
}}


tr {{
    page-break-inside: avoid;
}}


.footer {{
    margin-top: 8px;
    font-size: 8px;
    color: #6b7280;
}}

</style>

</head>

<body>

<div class="report-title">
    {esc(title)}
</div>

<div class="report-meta">
    Saved by:
    <strong>{esc(saved_by)}</strong>
    &nbsp;&nbsp;|&nbsp;&nbsp;
    Saved at:
    <strong>{esc(saved_at)}</strong>
</div>

<div class="filters">
    {''.join(filter_items)}
</div>

<table>

<thead>
<tr>
    {''.join(headers)}
</tr>
</thead>

<tbody>
    {''.join(body)}
</tbody>

</table>

<div class="footer">
    Isambane Mining - Permanent Productivity Override Snapshot
</div>

</body>

</html>
"""


@frappe.whitelist()
def save_productivity_override_snapshot(
    filters_json=None,
):

    import json
    import re

    from frappe.utils import now_datetime
    from frappe.utils.pdf import get_pdf
    from frappe.utils.file_manager import save_file


    if frappe.session.user == "Guest":

        frappe.throw(
            "Please log in before saving a Productivity snapshot.",
            frappe.PermissionError,
        )


    if (
        "System Manager"
        not in frappe.get_roles(
            frappe.session.user
        )
        and not frappe.has_permission(
            "Hourly Production",
            ptype="write",
        )
    ):

        frappe.throw(
            "You do not have permission to save "
            "Productivity overrides.",
            frappe.PermissionError,
        )


    filters = frappe._dict(
        _productivity_snapshot_json_load(
            filters_json
        )
    )


    # --------------------------------------------------------
    # Re-run Productivity AFTER row overrides were saved.
    #
    # This gives the snapshot the correct:
    #
    # - From Area
    # - To Area
    # - Hauling Distance
    # - Machine totals
    # - Category totals
    # - Total Fleet
    # --------------------------------------------------------

    result = execute(
        filters
    )


    columns = (
        result[0]
        if result
        and len(result) > 0
        else []
    )


    rows = (
        result[1]
        if result
        and len(result) > 1
        else []
    )


    if not rows:

        frappe.throw(
            "There is no Productivity data to save."
        )


    saved_at_dt = (
        now_datetime()
    )


    saved_at = (
        saved_at_dt.strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )


    saved_by = (
        frappe.session.user
    )


    site = str(
        filters.get(
            "site"
        )
        or ""
    ).strip()


    start_date = str(
        filters.get(
            "start_date"
        )
        or ""
    ).strip()


    end_date = str(
        filters.get(
            "end_date"
        )
        or ""
    ).strip()


    # ========================================================
    # KOSI_PRODUCTIVITY_DESCRIPTIVE_SNAPSHOT_NAME_V1
    #
    # Example:
    #
    # PROD-OVR-2026-07-19 Klipfontein-
    # Actual BCMs-Hours and Material-20260917
    #
    # Or:
    #
    # PROD-OVR-2026-07-19 Klipfontein-
    # Tallies BCMs-Hours and Material-20260917
    # ========================================================

    monthly_plans = (
        _productivity_snapshot_text(
            filters.get(
                "monthly_production_plans"
            )
        )
    )


    bcm_basis = (
        _productivity_snapshot_text(
            filters.get(
                "bcm_basis"
            )
        )
        or "Tallies BCMs"
    )


    summary_view = (
        _productivity_snapshot_text(
            filters.get(
                "summary_view"
            )
        )
        or "Summary Per Machine"
    )


    # --------------------------------------------------------
    # SELECTED MONTHLY PRODUCTION PLAN
    #
    # Example source:
    #
    #     2026-07-19-Klipfontein
    #
    # Saved name:
    #
    #     2026-07-19 Klipfontein
    # --------------------------------------------------------

    try:

        selected_plan_names = (
            _productivity_selected_monthly_plans(
                filters.get(
                    "monthly_production_plans"
                )
            )
        )

    except Exception:

        selected_plan_names = []


    if len(
        selected_plan_names
    ) == 1:

        selected_plan = str(
            selected_plan_names[0]
            or ""
        ).strip()


        match = re.match(
            r"^(\d{4}-\d{2}-\d{2})-(.+)$",
            selected_plan,
        )


        if match:

            period_site_label = (
                match.group(1)
                + " "
                + match.group(2)
            )

        else:

            period_site_label = (
                selected_plan
            )


    elif len(
        selected_plan_names
    ) > 1:

        first_plan = str(
            selected_plan_names[0]
            or ""
        ).strip()


        match = re.match(
            r"^(\d{4}-\d{2}-\d{2})-(.+)$",
            first_plan,
        )


        if match:

            first_label = (
                match.group(1)
                + " "
                + match.group(2)
            )

        else:

            first_label = (
                first_plan
            )


        period_site_label = (
            first_label
            + " PLUS "
            + str(
                len(
                    selected_plan_names
                )
                - 1
            )
            + " MONTHS"
        )


    else:

        period_site_label = (
            (
                end_date
                or start_date
                or "NO-DATE"
            )
            + (
                " "
                + site
                if site
                else ""
            )
        )


    # --------------------------------------------------------
    # CLEAN NAME
    #
    # Keep spaces, but remove characters that are unsuitable
    # for file/document names.
    # --------------------------------------------------------

    def clean_snapshot_name_part(
        value,
    ):

        value = str(
            value or ""
        ).strip()


        value = re.sub(
            r'[\\/:*?"<>|]+',
            "-",
            value,
        )


        value = re.sub(
            r"\s+",
            " ",
            value,
        )


        return value.strip(
            " -"
        )


    period_site_label = (
        clean_snapshot_name_part(
            period_site_label
        )
    )


    bcm_basis = (
        clean_snapshot_name_part(
            bcm_basis
        )
    )


    summary_view = (
        clean_snapshot_name_part(
            summary_view
        )
    )


    save_date = (
        saved_at_dt.strftime(
            "%Y%m%d"
        )
    )


    snapshot_reference_base = (
        "PROD-OVR-"
        + period_site_label
        + "-"
        + bcm_basis
        + "-"
        + summary_view
        + "-"
        + save_date
    )


    # Keep below Frappe's normal name length.
    snapshot_reference_base = (
        snapshot_reference_base[
            :135
        ].rstrip(
            " -"
        )
    )


    # --------------------------------------------------------
    # ALLOW SAME REPORT TO BE SAVED MORE THAN ONCE.
    #
    # First save:
    #
    # ...-20260917
    #
    # Second save:
    #
    # ...-20260917-02
    #
    # Third save:
    #
    # ...-20260917-03
    # --------------------------------------------------------

    snapshot_reference = (
        snapshot_reference_base
    )


    sequence = 2


    while frappe.db.exists(
        "Productivity Override Snapshot",
        snapshot_reference,
    ):

        suffix = (
            "-"
            + str(
                sequence
            ).zfill(
                2
            )
        )


        snapshot_reference = (
            snapshot_reference_base[
                : 140 - len(suffix)
            ].rstrip(
                " -"
            )
            + suffix
        )


        sequence += 1


    title = (
        "Productivity Override - "
        + (
            site
            or "All Sites"
        )
    )


    if (
        start_date
        or end_date
    ):

        title += (
            " - "
            + start_date
            + " to "
            + end_date
        )


    snapshot_html = (
        _productivity_snapshot_build_html(
            filters,
            columns,
            rows,
            saved_by,
            saved_at,
        )
    )


    # Generate PDF BEFORE creating the database record.
    # This avoids creating an incomplete snapshot if PDF
    # generation itself fails.
    pdf_bytes = get_pdf(
        snapshot_html,
        options={
            "orientation":
                "Landscape",

            "page-size":
                "A3",

            "margin-top":
                "7mm",

            "margin-right":
                "7mm",

            "margin-bottom":
                "7mm",

            "margin-left":
                "7mm",
        },
    )


    doc = frappe.get_doc({
        "doctype":
            "Productivity Override Snapshot",

        "snapshot_reference":
            snapshot_reference,

        "title":
            title,

        "site":
            site or None,

        "start_date":
            start_date or None,

        "end_date":
            end_date or None,

        "monthly_production_plans":
            monthly_plans,

        "shift":
            _productivity_snapshot_text(
                filters.get(
                    "shift"
                )
            ),

        "machine_type":
            _productivity_snapshot_text(
                filters.get(
                    "machine_type"
                )
            ),

        "asset":
            _productivity_snapshot_text(
                filters.get(
                    "asset"
                )
            ),

        "bcm_basis":
            _productivity_snapshot_text(
                filters.get(
                    "bcm_basis"
                )
            ),

        "summary_view":
            _productivity_snapshot_text(
                filters.get(
                    "summary_view"
                )
            ),

        "saved_by":
            saved_by,

        "saved_at":
            saved_at_dt,

        "snapshot_html":
            snapshot_html,

        "snapshot_data":
            json.dumps(
                {
                    "filters":
                        dict(
                            filters
                        ),

                    "columns":
                        columns,

                    "rows":
                        rows,
                },

                default=str,
                ensure_ascii=False,
            ),
    })


    doc.insert(
        ignore_permissions=True
    )


    file_name = (
        snapshot_reference
        + ".pdf"
    )


    file_doc = save_file(
        file_name,
        pdf_bytes,
        "Productivity Override Snapshot",
        doc.name,
        is_private=1,
    )


    doc.db_set(
        "snapshot_pdf",
        file_doc.file_url,
        update_modified=False,
    )


    frappe.db.commit()


    return {
        "saved":
            True,

        "name":
            doc.name,

        "snapshot_reference":
            snapshot_reference,

        "file_url":
            file_doc.file_url,

        "row_count":
            len(
                rows
            ),

        "saved_at":
            saved_at,
    }


# END KOSI_PRODUCTIVITY_FULL_OVERRIDE_SNAPSHOT_V1



# ============================================================
# KOSI_PRODUCTIVITY_HAULING_DISTANCE_ADT_DOZER_V1
#
# FINAL HAULING DISTANCE RULES
#
# Excavator:
#     Always blank.
#
# ADT:
#     Category total = sum of editable ADT material rows.
#
# Dozer:
#     Category total = sum of editable Dozer material rows.
#
# Total Fleet:
#     ADT + Dozer hauling distance only.
#
# Zero:
#     Display blank instead of 0.
#
# Working Hours / BCM / Productivity are NOT changed.
#
# Applies to:
#     Hours and Material
#     Summary Per Machine
#     Multiple Monthly Production periods
#     Saved Override snapshots / PDFs
# ============================================================


_productivity_execute_before_final_distance_rule = execute


def _productivity_final_distance_number(
    value,
):

    if value in (
        None,
        "",
    ):

        return 0.0


    try:

        return float(
            str(
                value
            ).replace(
                ",",
                "",
            )
        )

    except (
        TypeError,
        ValueError,
    ):

        return 0.0


def _productivity_final_distance_display(
    value,
):

    value = round(
        float(
            value or 0
        ),
        3,
    )


    # Never display 0.
    if value <= 0:

        return ""


    if value.is_integer():

        return int(
            value
        )


    return value


def _productivity_apply_distance_section(
    rows,
    start_index,
    end_index,
):

    if (
        start_index >= end_index
    ):

        return


    adt_total = 0.0
    dozer_total = 0.0

    category_rows = {}
    fleet_rows = []

    current_category = ""


    # ========================================================
    # FIRST PASS
    #
    # - Blank Excavator distance
    # - Remove all visible zeros
    # - Sum ONLY editable material rows for ADT / Dozer
    # ========================================================

    for index in range(
        start_index,
        end_index,
    ):

        row = rows[
            index
        ]


        if not hasattr(
            row,
            "get",
        ):

            continue


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        # ----------------------------------------------------
        # CATEGORY HEADING
        # ----------------------------------------------------

        if (
            label
            in (
                "Excavator",
                "ADT",
                "Dozer",
            )
            and int(
                row.get(
                    "is_category_total"
                )
                or 0
            ) == 1
        ):

            current_category = label

            category_rows[
                label
            ] = index


        # ----------------------------------------------------
        # TOTAL FLEET ROW
        # ----------------------------------------------------

        if int(
            row.get(
                "is_total_fleet"
            )
            or 0
        ) == 1:

            fleet_rows.append(
                index
            )

            current_category = ""


        edit_category = str(
            row.get(
                "productivity_edit_category"
            )
            or row.get(
                "category"
            )
            or current_category
            or ""
        ).strip()


        # ----------------------------------------------------
        # EXCAVATOR
        #
        # Excavators do not carry hauling-distance values in
        # this report.
        # ----------------------------------------------------

        if (
            edit_category == "Excavator"
            or current_category == "Excavator"
        ):

            row[
                "hauling_distance_m"
            ] = ""

            continue


        distance = (
            _productivity_final_distance_number(
                row.get(
                    "hauling_distance_m"
                )
            )
        )


        # ----------------------------------------------------
        # REMOVE ZERO DISPLAY
        # ----------------------------------------------------

        row[
            "hauling_distance_m"
        ] = (
            _productivity_final_distance_display(
                distance
            )
        )


        # ----------------------------------------------------
        # CATEGORY TOTAL SOURCE
        #
        # Only real editable material rows are counted.
        #
        # This prevents double-counting:
        #
        # Category Total
        # Machine Total
        # Material Detail
        # ----------------------------------------------------

        if int(
            row.get(
                "productivity_editable"
            )
            or 0
        ) != 1:

            continue


        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()


        if not material:

            continue


        if edit_category == "ADT":

            adt_total += distance


        elif edit_category == "Dozer":

            dozer_total += distance


    # ========================================================
    # SECOND PASS
    #
    # Force category totals.
    # ========================================================

    excavator_index = (
        category_rows.get(
            "Excavator"
        )
    )


    if excavator_index is not None:

        rows[
            excavator_index
        ][
            "hauling_distance_m"
        ] = ""


    adt_index = (
        category_rows.get(
            "ADT"
        )
    )


    if adt_index is not None:

        rows[
            adt_index
        ][
            "hauling_distance_m"
        ] = (
            _productivity_final_distance_display(
                adt_total
            )
        )


    dozer_index = (
        category_rows.get(
            "Dozer"
        )
    )


    if dozer_index is not None:

        rows[
            dozer_index
        ][
            "hauling_distance_m"
        ] = (
            _productivity_final_distance_display(
                dozer_total
            )
        )


    # ========================================================
    # TOTAL FLEET
    #
    # IMPORTANT:
    #
    # Hauling Distance Total Fleet is:
    #
    #     ADT + DOZER
    #
    # NOT:
    #
    #     Excavator + Dozer
    #
    # This only changes Hauling Distance.
    # Existing Total Fleet Hours / BCM remain untouched.
    # ========================================================

    fleet_distance = (
        adt_total
        + dozer_total
    )


    fleet_display = (
        _productivity_final_distance_display(
            fleet_distance
        )
    )


    for fleet_index in fleet_rows:

        rows[
            fleet_index
        ][
            "hauling_distance_m"
        ] = fleet_display


def _productivity_apply_final_distance_rules(
    rows,
):

    rows = [
        dict(
            row
        )
        if hasattr(
            row,
            "get",
        )
        else row

        for row in (
            rows or []
        )
    ]


    if not rows:

        return rows


    # --------------------------------------------------------
    # Split by Monthly Production heading.
    #
    # Each month gets independent:
    #
    # ADT total
    # Dozer total
    # Total Fleet distance
    # --------------------------------------------------------

    section_start = 0


    for index, row in enumerate(
        rows
    ):

        if not hasattr(
            row,
            "get",
        ):

            continue


        if (
            index > 0
            and int(
                row.get(
                    "is_monthly_plan_header"
                )
                or 0
            ) == 1
        ):

            _productivity_apply_distance_section(
                rows,
                section_start,
                index,
            )


            section_start = index


    _productivity_apply_distance_section(
        rows,
        section_start,
        len(
            rows
        ),
    )


    return rows


def execute(
    filters=None,
):

    original_result = (
        _productivity_execute_before_final_distance_rule(
            filters
        )
    )


    if not original_result:

        return original_result


    if isinstance(
        original_result,
        tuple,
    ):

        parts = list(
            original_result
        )

        return_tuple = True


    elif isinstance(
        original_result,
        list,
    ):

        parts = list(
            original_result
        )

        return_tuple = False


    else:

        return original_result


    if (
        len(parts) > 1
        and parts[1]
    ):

        parts[1] = (
            _productivity_apply_final_distance_rules(
                parts[1]
            )
        )


    if return_tuple:

        return tuple(
            parts
        )


    return parts


# END KOSI_PRODUCTIVITY_HAULING_DISTANCE_ADT_DOZER_V1



# ============================================================
# KOSI_PRODUCTIVITY_EXCAVATOR_NO_AREA_V1
#
# FINAL DISPLAY RULE
#
# EXCAVATOR:
#     From Area          = blank
#     To Area            = blank
#     Hauling Distance   = blank
#
# ADT:
#     Areas + distance remain available/editable.
#
# DOZER:
#     Areas + distance remain available/editable.
#
# Existing saved override records are NOT deleted.
# They are simply not displayed for Excavator.
# ============================================================


_productivity_execute_before_excavator_no_area = execute


def _productivity_hide_excavator_area(
    rows,
):

    rows = [
        dict(row)
        if hasattr(
            row,
            "get",
        )
        else row

        for row in (
            rows or []
        )
    ]


    current_category = ""


    for row in rows:

        if not hasattr(
            row,
            "get",
        ):
            continue


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        # New monthly section.
        if int(
            row.get(
                "is_monthly_plan_header"
            )
            or 0
        ) == 1:

            current_category = ""

            row[
                "from_area"
            ] = ""

            row[
                "to_area"
            ] = ""

            row[
                "hauling_distance_m"
            ] = ""

            continue


        # Category heading.
        if (
            label
            in (
                "Excavator",
                "ADT",
                "Dozer",
            )
            and int(
                row.get(
                    "is_category_total"
                )
                or 0
            ) == 1
        ):

            current_category = label


        category = str(
            row.get(
                "productivity_edit_category"
            )
            or row.get(
                "category"
            )
            or current_category
            or ""
        ).strip()


        # ----------------------------------------------------
        # EXCAVATOR MUST HAVE NO AREA / DISTANCE
        # ----------------------------------------------------

        if category == "Excavator":

            row[
                "from_area"
            ] = ""

            row[
                "to_area"
            ] = ""

            row[
                "hauling_distance_m"
            ] = ""


            # These are the only editable fields in this
            # Productivity override workflow, therefore
            # Excavator rows must not render editable inputs.
            row[
                "productivity_editable"
            ] = 0


    return rows


def _productivity_resize_area_columns_after_excavator_clear(
    columns,
    rows,
):

    columns = [
        dict(column)
        if hasattr(
            column,
            "get",
        )
        else column

        for column in (
            columns or []
        )
    ]


    for fieldname in (
        "from_area",
        "to_area",
    ):

        longest = (
            "From Area"
            if fieldname == "from_area"
            else "To Area"
        )


        for row in (
            rows or []
        ):

            if not hasattr(
                row,
                "get",
            ):
                continue


            value = str(
                row.get(
                    fieldname
                )
                or ""
            ).strip()


            if len(value) > len(longest):

                longest = value


        width = min(
            650,
            max(
                180,
                len(longest) * 8 + 50,
            ),
        )


        for column in columns:

            if (
                hasattr(
                    column,
                    "get",
                )
                and str(
                    column.get(
                        "fieldname"
                    )
                    or ""
                ) == fieldname
            ):

                column[
                    "width"
                ] = width


    return columns


def execute(
    filters=None,
):

    original_result = (
        _productivity_execute_before_excavator_no_area(
            filters
        )
    )


    if not original_result:

        return original_result


    if isinstance(
        original_result,
        tuple,
    ):

        parts = list(
            original_result
        )

        return_tuple = True


    elif isinstance(
        original_result,
        list,
    ):

        parts = list(
            original_result
        )

        return_tuple = False


    else:

        return original_result


    if (
        len(parts) > 1
        and parts[1]
    ):

        parts[1] = (
            _productivity_hide_excavator_area(
                parts[1]
            )
        )


    if parts:

        parts[0] = (
            _productivity_resize_area_columns_after_excavator_clear(
                parts[0],
                (
                    parts[1]
                    if len(parts) > 1
                    else []
                ),
            )
        )


    if return_tuple:

        return tuple(
            parts
        )


    return parts


# END KOSI_PRODUCTIVITY_EXCAVATOR_NO_AREA_V1



# ============================================================
# KOSI_PRODUCTIVITY_BCM_HD_COLUMN_V1
#
# Add:
#
#     Productivity (BCM/HD)
#
# directly after:
#
#     Productivity (BCM/Hr)
#
# Calculation intentionally remains blank until BCM/HD
# business formula is confirmed.
# ============================================================


_productivity_execute_before_bcm_hd_column = execute


def _productivity_add_bcm_hd_column(
    columns,
):

    columns = [
        dict(column)
        if hasattr(
            column,
            "get",
        )
        else column

        for column in (
            columns or []
        )
    ]


    # Remove an existing experimental column if present.
    columns = [
        column
        for column in columns
        if not (
            hasattr(
                column,
                "get",
            )
            and str(
                column.get(
                    "fieldname"
                )
                or ""
            ).strip()
            == "productivity_bcm_hd"
        )
    ]


    new_column = {
        "label":
            "Productivity (BCM/HD)",

        "fieldname":
            "productivity_bcm_hd",

        "fieldtype":
            "Float",

        "precision":
            0,

        "width":
            180,
    }


    result = []

    inserted = False


    for column in columns:

        result.append(
            column
        )


        if (
            hasattr(
                column,
                "get",
            )
            and str(
                column.get(
                    "fieldname"
                )
                or ""
            ).strip()
            == "productivity"
        ):

            result.append(
                new_column
            )

            inserted = True


    if not inserted:

        result.append(
            new_column
        )


    return result


def execute(
    filters=None,
):

    original_result = (
        _productivity_execute_before_bcm_hd_column(
            filters
        )
    )


    if not original_result:

        return original_result


    if isinstance(
        original_result,
        tuple,
    ):

        parts = list(
            original_result
        )

        return_tuple = True


    elif isinstance(
        original_result,
        list,
    ):

        parts = list(
            original_result
        )

        return_tuple = False


    else:

        return original_result


    if parts:

        parts[0] = (
            _productivity_add_bcm_hd_column(
                parts[0]
            )
        )


    if (
        len(parts) > 1
        and parts[1]
    ):

        new_rows = []


        for row in parts[1]:

            if hasattr(
                row,
                "get",
            ):

                row = dict(
                    row
                )


                row[
                    "productivity_bcm_hd"
                ] = ""


            new_rows.append(
                row
            )


        parts[1] = new_rows


    if return_tuple:

        return tuple(
            parts
        )


    return parts


# END KOSI_PRODUCTIVITY_BCM_HD_COLUMN_V1



# ============================================================
# KOSI_PRODUCTIVITY_HAULING_DISTANCE_TEXT_V1
#
# Hauling Distance is a TEXT value.
#
# Examples:
#
#     100-500
#     500-1000
#     1000-1500
#     750
#
# Saved values are restored AFTER all earlier Productivity
# calculation wrappers so range bands cannot be converted
# back to 0.
#
# Excavator remains blank.
# ============================================================


_productivity_execute_before_distance_text = execute


def _productivity_restore_saved_distance_text(
    rows,
):

    rows = [
        dict(row)
        if hasattr(
            row,
            "get",
        )
        else row

        for row in (
            rows or []
        )
    ]


    row_keys = [
        str(
            row.get(
                "productivity_edit_key"
            )
            or ""
        ).strip()

        for row in rows

        if hasattr(
            row,
            "get",
        )
        and str(
            row.get(
                "productivity_edit_key"
            )
            or ""
        ).strip()
    ]


    saved_map = {}


    if (
        row_keys
        and frappe.db.exists(
            "DocType",
            "Productivity Area Override",
        )
    ):

        saved = frappe.get_all(
            "Productivity Area Override",

            filters={
                "row_key": [
                    "in",
                    list(
                        set(
                            row_keys
                        )
                    ),
                ],
            },

            fields=[
                "row_key",
                "hauling_distance_m",
            ],

            limit_page_length=0,
        )


        saved_map = {
            str(
                item.row_key
            ):
                str(
                    item.hauling_distance_m
                    or ""
                ).strip()

            for item in saved
        }


    current_category = ""


    for row in rows:

        if not hasattr(
            row,
            "get",
        ):
            continue


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        if int(
            row.get(
                "is_monthly_plan_header"
            )
            or 0
        ) == 1:

            current_category = ""

            row[
                "hauling_distance_m"
            ] = ""

            continue


        if (
            label
            in (
                "Excavator",
                "ADT",
                "Dozer",
            )
            and int(
                row.get(
                    "is_category_total"
                )
                or 0
            ) == 1
        ):

            current_category = label


        category = str(
            row.get(
                "productivity_edit_category"
            )
            or row.get(
                "category"
            )
            or current_category
            or ""
        ).strip()


        # Excavator must remain completely blank.
        if category == "Excavator":

            row[
                "hauling_distance_m"
            ] = ""

            continue


        row_key = str(
            row.get(
                "productivity_edit_key"
            )
            or ""
        ).strip()


        if (
            row_key
            and row_key in saved_map
        ):

            row[
                "hauling_distance_m"
            ] = saved_map[
                row_key
            ]


        # Never show numeric zero.
        if str(
            row.get(
                "hauling_distance_m"
            )
            or ""
        ).strip() in (
            "0",
            "0.0",
            "0.00",
            "0.000",
        ):

            row[
                "hauling_distance_m"
            ] = ""


    return rows


def execute(
    filters=None,
):

    original_result = (
        _productivity_execute_before_distance_text(
            filters
        )
    )


    if not original_result:

        return original_result


    if isinstance(
        original_result,
        tuple,
    ):

        parts = list(
            original_result
        )

        return_tuple = True

    elif isinstance(
        original_result,
        list,
    ):

        parts = list(
            original_result
        )

        return_tuple = False

    else:

        return original_result


    if (
        len(parts) > 1
        and parts[1]
    ):

        parts[1] = (
            _productivity_restore_saved_distance_text(
                parts[1]
            )
        )


    if return_tuple:

        return tuple(
            parts
        )


    return parts


# END KOSI_PRODUCTIVITY_HAULING_DISTANCE_TEXT_V1



# ============================================================
# KOSI_PRODUCTIVITY_BCM_HD_FORMULA_V1
#
# PRODUCTIVITY BCM/HD
#
# Distance band:
#
#     500-1000
#
# Midpoint:
#
#     (500 + 1000) / 2
#     = 750
#
# Formula:
#
#     Midpoint x selected BCM
#
# Actual BCM mode:
#
#     Midpoint x Adjusted BCM
#
# Tallies BCM mode:
#
#     Midpoint x Tallies BCM
#
# IMPORTANT:
#
# - Material/detail rows ONLY.
# - No category totals.
# - No machine totals.
# - No Total Fleet total.
# - No Hauling Distance totals.
# - Excavator remains blank.
# ============================================================


_productivity_execute_before_bcm_hd_formula = execute


def _productivity_bcm_hd_number(
    value,
):

    try:

        return float(
            str(
                value
                or 0
            ).replace(
                ",",
                "",
            )
        )

    except (
        TypeError,
        ValueError,
    ):

        return 0.0


def _productivity_distance_midpoint(
    value,
):

    import re


    text = str(
        value
        or ""
    ).strip()


    if not text:

        return None


    # Normalise different dash characters.
    text = (
        text
        .replace(
            "–",
            "-"
        )
        .replace(
            "—",
            "-"
        )
    )


    # --------------------------------------------------------
    # RANGE
    #
    # 500-1000
    # 500 - 1000
    # --------------------------------------------------------

    match = re.match(
        r"^\s*([\d,.]+)\s*-\s*([\d,.]+)\s*$",
        text,
    )


    if match:

        low = (
            _productivity_bcm_hd_number(
                match.group(1)
            )
        )


        high = (
            _productivity_bcm_hd_number(
                match.group(2)
            )
        )


        if (
            low <= 0
            and high <= 0
        ):

            return None


        return (
            low
            + high
        ) / 2.0


    # --------------------------------------------------------
    # SINGLE DISTANCE
    #
    # If user enters:
    #
    # 750
    #
    # midpoint = 750
    # --------------------------------------------------------

    single = re.match(
        r"^\s*([\d,.]+)\s*$",
        text,
    )


    if single:

        value = (
            _productivity_bcm_hd_number(
                single.group(1)
            )
        )


        if value > 0:

            return value


    return None


def _productivity_bcm_hd_display(
    value,
):

    value = float(
        value
        or 0
    )


    if value <= 0:

        return ""


    return "{:,.0f}".format(
        value
    )


def _productivity_apply_bcm_hd_formula(
    columns,
    rows,
    filters=None,
):

    filters = frappe._dict(
        filters
        or {}
    )


    # ========================================================
    # COLUMN
    #
    # Use Data instead of Float so blank rows stay BLANK
    # rather than Frappe showing 0.000.
    # ========================================================

    new_columns = []


    for original_column in (
        columns
        or []
    ):

        if not hasattr(
            original_column,
            "get",
        ):

            new_columns.append(
                original_column
            )

            continue


        column = dict(
            original_column
        )


        if str(
            column.get(
                "fieldname"
            )
            or ""
        ).strip() == "productivity_bcm_hd":

            column[
                "label"
            ] = "Productivity (BCM/HD)"


            column[
                "fieldtype"
            ] = "Data"


            column[
                "width"
            ] = 180


            column.pop(
                "precision",
                None,
            )


        new_columns.append(
            column
        )


    rows = [
        dict(row)
        if hasattr(
            row,
            "get",
        )
        else row

        for row in (
            rows
            or []
        )
    ]


    current_category = ""


    for row in rows:

        if not hasattr(
            row,
            "get",
        ):

            continue


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()


        # ----------------------------------------------------
        # MONTH HEADER
        # ----------------------------------------------------

        if int(
            row.get(
                "is_monthly_plan_header"
            )
            or 0
        ) == 1:

            current_category = ""


            row[
                "hauling_distance_m"
            ] = ""


            row[
                "productivity_bcm_hd"
            ] = ""


            continue


        # ----------------------------------------------------
        # CATEGORY
        # ----------------------------------------------------

        if (
            label
            in (
                "Excavator",
                "ADT",
                "Dozer",
            )
            and int(
                row.get(
                    "is_category_total"
                )
                or 0
            ) == 1
        ):

            current_category = label


            # NO TOTALS.
            row[
                "hauling_distance_m"
            ] = ""


            row[
                "productivity_bcm_hd"
            ] = ""


            continue


        # ----------------------------------------------------
        # TOTAL FLEET
        # ----------------------------------------------------

        if int(
            row.get(
                "is_total_fleet"
            )
            or 0
        ) == 1:

            row[
                "hauling_distance_m"
            ] = ""


            row[
                "productivity_bcm_hd"
            ] = ""


            continue


        category = str(
            row.get(
                "productivity_edit_category"
            )
            or row.get(
                "category"
            )
            or current_category
            or ""
        ).strip()


        # ----------------------------------------------------
        # EXCAVATOR
        #
        # Completely blank for:
        #
        # From Area
        # To Area
        # Hauling Distance
        # BCM/HD
        # ----------------------------------------------------

        if category == "Excavator":

            row[
                "from_area"
            ] = ""


            row[
                "to_area"
            ] = ""


            row[
                "hauling_distance_m"
            ] = ""


            row[
                "productivity_bcm_hd"
            ] = ""


            continue


        # ----------------------------------------------------
        # NO TOTALS / SUMMARY ROWS
        #
        # Only rows with actual Material calculate BCM/HD.
        #
        # This removes:
        #
        # machine totals
        # category totals
        # unallocated rows
        # total fleet
        # ----------------------------------------------------

        if not material:

            row[
                "hauling_distance_m"
            ] = ""


            row[
                "productivity_bcm_hd"
            ] = ""


            continue


        # Only ADT and Dozer use hauling distance.
        if category not in (
            "ADT",
            "Dozer",
        ):

            row[
                "hauling_distance_m"
            ] = ""


            row[
                "productivity_bcm_hd"
            ] = ""


            continue


        distance_text = str(
            row.get(
                "hauling_distance_m"
            )
            or ""
        ).strip()


        midpoint = (
            _productivity_distance_midpoint(
                distance_text
            )
        )


        if midpoint is None:

            row[
                "productivity_bcm_hd"
            ] = ""

            continue


        # ====================================================
        # SELECTED BCM
        #
        # The final "output" field already follows the user's
        # BCM Basis:
        #
        # Actual BCMs  -> Adjusted BCM
        # Tallies BCMs -> Tallies BCM
        #
        # Therefore SAME formula works for BOTH modes.
        # ====================================================

        selected_bcm = (
            _productivity_bcm_hd_number(
                row.get(
                    "output"
                )
            )
        )


        if selected_bcm <= 0:

            row[
                "productivity_bcm_hd"
            ] = ""

            continue


        # KOSI_PRODUCTIVITY_BCM_HD_DIVISION_V2
        #
        # Productivity BCM/HD:
        #
        #     BCM
        #     -----------------------------
        #     Average Hauling Distance
        #
        # Example:
        #
        #     Distance = 500-1000
        #     Average  = 750
        #
        #     141,748 / 750 = 189
        #
        result = (
            selected_bcm
            / midpoint
            if midpoint > 0
            else 0
        )


        row[
            "productivity_bcm_hd"
        ] = (
            _productivity_bcm_hd_display(
                result
            )
        )


    return (
        new_columns,
        rows,
    )


def execute(
    filters=None,
):

    original_result = (
        _productivity_execute_before_bcm_hd_formula(
            filters
        )
    )


    if not original_result:

        return original_result


    if isinstance(
        original_result,
        tuple,
    ):

        parts = list(
            original_result
        )

        return_tuple = True


    elif isinstance(
        original_result,
        list,
    ):

        parts = list(
            original_result
        )

        return_tuple = False


    else:

        return original_result


    columns = (
        parts[0]
        if len(parts) > 0
        else []
    )


    rows = (
        parts[1]
        if len(parts) > 1
        else []
    )


    (
        columns,
        rows,
    ) = _productivity_apply_bcm_hd_formula(
        columns,
        rows,
        filters,
    )


    if len(parts) > 0:

        parts[0] = columns


    if len(parts) > 1:

        parts[1] = rows


    if return_tuple:

        return tuple(
            parts
        )


    return parts


# END KOSI_PRODUCTIVITY_BCM_HD_FORMULA_V1



# ============================================================
# KOSI_PRODUCTIVITY_SUMMARY_MACHINE_BCM_HD_V1
#
# SUMMARY PER MACHINE
#
# Use the Hours and Material override as the DEFAULT hauling
# distance for every machine/material row.
#
# Example:
#
# Shared ADT Coal:
#     500-1000
#
# Then:
#
# ADT01 Coal -> 500-1000
# ADT02 Coal -> 500-1000
# ADT03 Coal -> 500-1000
#
# A machine-specific override takes precedence when one exists.
#
# FORMULA:
#
#     Average HD = (Lower + Upper) / 2
#
#     BCM/HD = selected BCM / Average HD
#
# Actual BCM mode:
#     Adjusted BCM / Average HD
#
# Tallies BCM mode:
#     Tallies BCM / Average HD
#
# NO TOTALS.
# EXCAVATOR remains blank.
# ============================================================


_productivity_execute_before_summary_machine_bcm_hd = execute


def _productivity_machine_bcm_hd_number(
    value,
):

    try:

        return float(
            str(
                value
                or 0
            ).replace(
                ",",
                "",
            )
        )

    except (
        TypeError,
        ValueError,
    ):

        return 0.0


def _productivity_machine_bcm_hd_display(
    value,
):

    value = float(
        value
        or 0
    )


    if value <= 0:

        return ""


    # Machine/material production can be small relative
    # to hauling distance.
    #
    # Keep 3 decimals below 1 so values such as:
    #
    # 124 / 750 = 0.165
    #
    # do not become 0.
    if value < 1:

        return "{:,.3f}".format(
            value
        )


    # Preserve the existing report appearance for normal
    # larger results.
    return "{:,.0f}".format(
        value
    )


def _productivity_apply_summary_machine_bcm_hd(
    rows,
    filters=None,
):

    filters = frappe._dict(
        filters
        or {}
    )


    summary_view = str(
        filters.get(
            "summary_view"
        )
        or ""
    ).strip()


    if summary_view != "Summary Per Machine":

        return rows


    rows = [
        dict(row)
        if hasattr(
            row,
            "get",
        )
        else row

        for row in (
            rows
            or []
        )
    ]


    # ========================================================
    # BUILD SHARED HOURS-AND-MATERIAL OVERRIDE KEYS
    #
    # machine = ""
    #
    # These are the overrides previously saved from the
    # Hours and Material view.
    # ========================================================

    shared_keys = {}


    for row in rows:

        if not hasattr(
            row,
            "get",
        ):

            continue


        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()


        category = str(
            row.get(
                "productivity_edit_category"
            )
            or row.get(
                "category"
            )
            or ""
        ).strip()


        if (
            not material
            or category
            not in (
                "ADT",
                "Dozer",
            )
        ):

            continue


        site = str(
            row.get(
                "productivity_edit_site"
            )
            or filters.get(
                "site"
            )
            or ""
        ).strip()


        start_date = str(
            row.get(
                "productivity_edit_start_date"
            )
            or filters.get(
                "start_date"
            )
            or ""
        ).strip()


        end_date = str(
            row.get(
                "productivity_edit_end_date"
            )
            or filters.get(
                "end_date"
            )
            or ""
        ).strip()


        shift = str(
            row.get(
                "productivity_edit_shift"
            )
            or filters.get(
                "shift"
            )
            or ""
        ).strip()


        monthly_plan = str(
            row.get(
                "productivity_edit_monthly_plan"
            )
            or ""
        ).strip()


        shared_key = (
            _productivity_manual_edit_key(
                site,
                start_date,
                end_date,
                shift,
                monthly_plan,
                category,
                "",
                material,
            )
        )


        shared_keys[
            shared_key
        ] = True


    shared_map = {}


    if (
        shared_keys
        and frappe.db.exists(
            "DocType",
            "Productivity Area Override",
        )
    ):

        saved = frappe.get_all(
            "Productivity Area Override",

            filters={
                "row_key": [
                    "in",
                    list(
                        shared_keys.keys()
                    ),
                ],
            },

            fields=[
                "row_key",
                "to_area",
                "hauling_distance_m",
            ],

            limit_page_length=0,
        )


        shared_map = {
            str(
                item.row_key
            ):
                item

            for item in saved
        }


    # ========================================================
    # APPLY TO MACHINE MATERIAL ROWS
    # ========================================================

    current_category = ""


    for row in rows:

        if not hasattr(
            row,
            "get",
        ):

            continue


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()


        # ----------------------------------------------------
        # MONTH HEADER
        # ----------------------------------------------------

        if int(
            row.get(
                "is_monthly_plan_header"
            )
            or 0
        ) == 1:

            current_category = ""

            row[
                "productivity_bcm_hd"
            ] = ""

            row[
                "hauling_distance_m"
            ] = ""

            continue


        # ----------------------------------------------------
        # CATEGORY ROW
        # ----------------------------------------------------

        if (
            label
            in (
                "Excavator",
                "ADT",
                "Dozer",
            )
            and int(
                row.get(
                    "is_category_total"
                )
                or 0
            ) == 1
        ):

            current_category = label


            row[
                "productivity_bcm_hd"
            ] = ""


            row[
                "hauling_distance_m"
            ] = ""


            continue


        # ----------------------------------------------------
        # TOTAL FLEET
        # ----------------------------------------------------

        if int(
            row.get(
                "is_total_fleet"
            )
            or 0
        ) == 1:

            row[
                "productivity_bcm_hd"
            ] = ""


            row[
                "hauling_distance_m"
            ] = ""


            continue


        category = str(
            row.get(
                "productivity_edit_category"
            )
            or row.get(
                "category"
            )
            or current_category
            or ""
        ).strip()


        # ----------------------------------------------------
        # EXCAVATOR
        # ----------------------------------------------------

        if category == "Excavator":

            row[
                "from_area"
            ] = ""


            row[
                "to_area"
            ] = ""


            row[
                "hauling_distance_m"
            ] = ""


            row[
                "productivity_bcm_hd"
            ] = ""


            continue


        # Machine total / blank material row.
        if not material:

            row[
                "hauling_distance_m"
            ] = ""


            row[
                "productivity_bcm_hd"
            ] = ""


            continue


        if category not in (
            "ADT",
            "Dozer",
        ):

            row[
                "hauling_distance_m"
            ] = ""


            row[
                "productivity_bcm_hd"
            ] = ""


            continue


        # ====================================================
        # SHARED OVERRIDE
        # ====================================================

        site = str(
            row.get(
                "productivity_edit_site"
            )
            or filters.get(
                "site"
            )
            or ""
        ).strip()


        start_date = str(
            row.get(
                "productivity_edit_start_date"
            )
            or filters.get(
                "start_date"
            )
            or ""
        ).strip()


        end_date = str(
            row.get(
                "productivity_edit_end_date"
            )
            or filters.get(
                "end_date"
            )
            or ""
        ).strip()


        shift = str(
            row.get(
                "productivity_edit_shift"
            )
            or filters.get(
                "shift"
            )
            or ""
        ).strip()


        monthly_plan = str(
            row.get(
                "productivity_edit_monthly_plan"
            )
            or ""
        ).strip()


        shared_key = (
            _productivity_manual_edit_key(
                site,
                start_date,
                end_date,
                shift,
                monthly_plan,
                category,
                "",
                material,
            )
        )


        shared = shared_map.get(
            shared_key
        )


        current_distance = str(
            row.get(
                "hauling_distance_m"
            )
            or ""
        ).strip()


        # ----------------------------------------------------
        # Machine-specific value wins.
        #
        # If none exists, inherit shared category/material
        # value from Hours and Material.
        # ----------------------------------------------------

        if (
            not current_distance
            and shared
        ):

            current_distance = str(
                shared.hauling_distance_m
                or ""
            ).strip()


            row[
                "hauling_distance_m"
            ] = current_distance


        # To Area follows same fallback rule.
        current_to_area = str(
            row.get(
                "to_area"
            )
            or ""
        ).strip()


        if (
            not current_to_area
            and shared
        ):

            row[
                "to_area"
            ] = str(
                shared.to_area
                or ""
            ).strip()


        # IMPORTANT:
        #
        # From Area stays machine-specific from Hourly
        # Production. Do not replace it with the shared value.


        midpoint = (
            _productivity_distance_midpoint(
                current_distance
            )
        )


        if (
            midpoint is None
            or midpoint <= 0
        ):

            row[
                "productivity_bcm_hd"
            ] = ""

            continue


        selected_bcm = (
            _productivity_machine_bcm_hd_number(
                row.get(
                    "output"
                )
            )
        )


        if selected_bcm <= 0:

            row[
                "productivity_bcm_hd"
            ] = ""

            continue


        result = (
            selected_bcm
            / midpoint
        )


        row[
            "productivity_bcm_hd"
        ] = (
            _productivity_machine_bcm_hd_display(
                result
            )
        )


    return rows


def execute(
    filters=None,
):

    original_result = (
        _productivity_execute_before_summary_machine_bcm_hd(
            filters
        )
    )


    if not original_result:

        return original_result


    if isinstance(
        original_result,
        tuple,
    ):

        parts = list(
            original_result
        )

        return_tuple = True


    elif isinstance(
        original_result,
        list,
    ):

        parts = list(
            original_result
        )

        return_tuple = False


    else:

        return original_result


    if (
        len(parts) > 1
        and parts[1]
    ):

        parts[1] = (
            _productivity_apply_summary_machine_bcm_hd(
                parts[1],
                filters,
            )
        )


    if return_tuple:

        return tuple(
            parts
        )


    return parts


# END KOSI_PRODUCTIVITY_SUMMARY_MACHINE_BCM_HD_V1



# ============================================================
# KOSI_PRODUCTIVITY_ADT_SPECIFIC_HD_V1
#
# SUMMARY PER MACHINE:
#
# Every ADT + Material row receives its OWN override key.
#
# Examples:
#
# ADT01 + Coal
# ADT01 + Hards
# ADT01 + Softs
#
# ADT02 + Coal
# ADT02 + Softs
#
# etc.
#
# The general Hours and Material hauling-distance value is
# NOT copied into these rows.
#
# If no machine-specific override exists:
#
#     To Area          = blank
#     Hauling Distance = blank
#     BCM/HD           = blank
#
# User captures the correct value, then clicks Save Override.
#
# FROM AREA remains automatic from Hourly Production.
#
# BCM/HD:
#
#     Average HD = (Lower + Upper) / 2
#     BCM/HD     = selected BCM / Average HD
#
# Actual mode  = Adjusted BCM
# Tallies mode = Tallies BCM
#
# No totals.
# ============================================================


_productivity_execute_before_adt_specific_hd = execute


def _productivity_adt_hd_number(
    value,
):

    try:

        return float(
            str(
                value
                or 0
            ).replace(
                ",",
                "",
            )
        )

    except (
        TypeError,
        ValueError,
    ):

        return 0.0


def _productivity_adt_hd_result(
    value,
):

    value = float(
        value
        or 0
    )


    if value <= 0:

        return ""


    # Keep small machine values meaningful.
    #
    # Example:
    #
    # 124 / 750 = 0.165
    if value < 1:

        return "{:,.3f}".format(
            value
        )


    return "{:,.0f}".format(
        value
    )


def _productivity_prepare_adt_specific_hd(
    rows,
    filters=None,
):

    filters = frappe._dict(
        filters
        or {}
    )


    if str(
        filters.get(
            "summary_view"
        )
        or ""
    ).strip() != "Summary Per Machine":

        return rows


    rows = [
        dict(row)
        if hasattr(
            row,
            "get",
        )
        else row

        for row in (
            rows
            or []
        )
    ]


    current_category = ""

    adt_keys = []


    # ========================================================
    # PASS 1
    #
    # Build unique ADT + Material edit keys.
    # ========================================================

    for row in rows:

        if not hasattr(
            row,
            "get",
        ):

            continue


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()


        # ----------------------------------------------------
        # MONTH HEADER
        # ----------------------------------------------------

        if int(
            row.get(
                "is_monthly_plan_header"
            )
            or 0
        ) == 1:

            current_category = ""

            continue


        # ----------------------------------------------------
        # CATEGORY
        # ----------------------------------------------------

        if (
            label
            in (
                "Excavator",
                "ADT",
                "Dozer",
            )
            and int(
                row.get(
                    "is_category_total"
                )
                or 0
            ) == 1
        ):

            current_category = label

            continue


        category = str(
            row.get(
                "productivity_edit_category"
            )
            or row.get(
                "category"
            )
            or current_category
            or ""
        ).strip()


        if (
            category != "ADT"
            or not material
        ):

            continue


        # ----------------------------------------------------
        # MACHINE
        #
        # Summary Per Machine label is normally:
        #
        #     ADT01
        #     ADT02
        #     IS0601
        # ----------------------------------------------------

        machine = str(
            row.get(
                "productivity_edit_machine"
            )
            or row.get(
                "machine"
            )
            or label
            or ""
        ).strip()


        if not machine:

            continue


        site = str(
            row.get(
                "productivity_edit_site"
            )
            or filters.get(
                "site"
            )
            or ""
        ).strip()


        start_date = str(
            row.get(
                "productivity_edit_start_date"
            )
            or row.get(
                "monthly_start_date"
            )
            or filters.get(
                "start_date"
            )
            or ""
        ).strip()


        end_date = str(
            row.get(
                "productivity_edit_end_date"
            )
            or row.get(
                "monthly_end_date"
            )
            or filters.get(
                "end_date"
            )
            or ""
        ).strip()


        shift = str(
            row.get(
                "productivity_edit_shift"
            )
            or filters.get(
                "shift"
            )
            or ""
        ).strip()


        monthly_plan = str(
            row.get(
                "productivity_edit_monthly_plan"
            )
            or row.get(
                "monthly_production_plan"
            )
            or ""
        ).strip()


        row_key = (
            _productivity_manual_edit_key(
                site,
                start_date,
                end_date,
                shift,
                monthly_plan,
                "ADT",
                machine,
                material,
            )
        )


        # ----------------------------------------------------
        # FORCE THIS EXACT MACHINE/MATERIAL ROW EDITABLE.
        # ----------------------------------------------------

        row[
            "productivity_editable"
        ] = 1


        row[
            "productivity_edit_key"
        ] = row_key


        row[
            "productivity_edit_site"
        ] = site


        row[
            "productivity_edit_start_date"
        ] = start_date


        row[
            "productivity_edit_end_date"
        ] = end_date


        row[
            "productivity_edit_shift"
        ] = shift


        row[
            "productivity_edit_monthly_plan"
        ] = monthly_plan


        row[
            "productivity_edit_category"
        ] = "ADT"


        row[
            "productivity_edit_machine"
        ] = machine


        row[
            "productivity_edit_material"
        ] = material


        adt_keys.append(
            row_key
        )


    # ========================================================
    # LOAD MACHINE-SPECIFIC SAVED OVERRIDES
    # ========================================================

    saved_map = {}


    if (
        adt_keys
        and frappe.db.exists(
            "DocType",
            "Productivity Area Override",
        )
    ):

        saved_rows = frappe.get_all(
            "Productivity Area Override",

            filters={
                "row_key": [
                    "in",
                    list(
                        set(
                            adt_keys
                        )
                    ),
                ],
            },

            fields=[
                "row_key",
                "to_area",
                "hauling_distance_m",
            ],

            limit_page_length=0,
        )


        saved_map = {
            str(
                saved.row_key
            ):
                saved

            for saved in saved_rows
        }


    # ========================================================
    # PASS 2
    #
    # Apply ONLY machine-specific values.
    #
    # No shared category/material fallback.
    # ========================================================

    current_category = ""


    for row in rows:

        if not hasattr(
            row,
            "get",
        ):

            continue


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()


        if int(
            row.get(
                "is_monthly_plan_header"
            )
            or 0
        ) == 1:

            current_category = ""

            continue


        if (
            label
            in (
                "Excavator",
                "ADT",
                "Dozer",
            )
            and int(
                row.get(
                    "is_category_total"
                )
                or 0
            ) == 1
        ):

            current_category = label


            if label == "ADT":

                # Category totals stay blank.
                row[
                    "hauling_distance_m"
                ] = ""


                row[
                    "productivity_bcm_hd"
                ] = ""


            continue


        category = str(
            row.get(
                "productivity_edit_category"
            )
            or row.get(
                "category"
            )
            or current_category
            or ""
        ).strip()


        if category != "ADT":

            continue


        # Machine total row:
        #
        # ADT01
        #
        # No Material = no HD total.
        if not material:

            row[
                "hauling_distance_m"
            ] = ""


            row[
                "productivity_bcm_hd"
            ] = ""


            continue


        row_key = str(
            row.get(
                "productivity_edit_key"
            )
            or ""
        ).strip()


        saved = (
            saved_map.get(
                row_key
            )
            if row_key
            else None
        )


        if saved:

            row[
                "to_area"
            ] = str(
                saved.to_area
                or ""
            ).strip()


            distance = str(
                saved.hauling_distance_m
                or ""
            ).strip()

        else:

            # IMPORTANT:
            #
            # Do not inherit 500-1000 / 1000-1500 etc from
            # Hours and Material.
            #
            # Every ADT must be captured separately.
            row[
                "to_area"
            ] = ""


            distance = ""


        if distance in (
            "0",
            "0.0",
            "0.00",
            "0.000",
        ):

            distance = ""


        row[
            "hauling_distance_m"
        ] = distance


        # ----------------------------------------------------
        # CALCULATE MACHINE-SPECIFIC BCM/HD
        # ----------------------------------------------------

        midpoint = (
            _productivity_distance_midpoint(
                distance
            )
        )


        if (
            midpoint is None
            or midpoint <= 0
        ):

            row[
                "productivity_bcm_hd"
            ] = ""

            continue


        selected_bcm = (
            _productivity_adt_hd_number(
                row.get(
                    "output"
                )
            )
        )


        if selected_bcm <= 0:

            row[
                "productivity_bcm_hd"
            ] = ""

            continue


        row[
            "productivity_bcm_hd"
        ] = (
            _productivity_adt_hd_result(
                selected_bcm
                / midpoint
            )
        )


    return rows


def execute(
    filters=None,
):

    original_result = (
        _productivity_execute_before_adt_specific_hd(
            filters
        )
    )


    if not original_result:

        return original_result


    if isinstance(
        original_result,
        tuple,
    ):

        parts = list(
            original_result
        )

        return_tuple = True


    elif isinstance(
        original_result,
        list,
    ):

        parts = list(
            original_result
        )

        return_tuple = False


    else:

        return original_result


    if (
        len(parts) > 1
        and parts[1]
    ):

        parts[1] = (
            _productivity_prepare_adt_specific_hd(
                parts[1],
                filters,
            )
        )


    if return_tuple:

        return tuple(
            parts
        )


    return parts


# END KOSI_PRODUCTIVITY_ADT_SPECIFIC_HD_V1



# ============================================================
# KOSI_PRODUCTIVITY_ADT_DOZER_MACHINE_HD_V2
#
# SUMMARY PER MACHINE
#
# ADT and Dozer work exactly the same:
#
#     Machine + Material = unique override
#
# Examples:
#
#     ADT01 + Coal
#     ADT01 + Hards
#     ADT01 + Softs
#
#     ADT02 + Coal
#     ADT02 + Softs
#
#     IS0335 + 2 - Midburden
#     IS0338 + 1 - Overburden
#
# Each row independently stores:
#
#     To Area
#     Hauling Distance
#
# From Area remains automatically sourced.
#
# No category/material default hauling distance is inherited
# in Summary Per Machine.
#
# BCM/HD =
#
#     selected BCM
#     --------------------------
#     average hauling distance
#
# where:
#
# average HD = (lower + upper) / 2
#
# Actual BCM mode:
#     Adjusted BCM / Average HD
#
# Tallies mode:
#     Tallies BCM / Average HD
#
# Excavator:
#     Area / HD / BCM-HD = blank
#
# TOTALS:
#     blank
# ============================================================


_productivity_execute_before_machine_specific_hd_v2 = execute


def _productivity_machine_hd_v2_number(
    value,
):

    try:
        return float(
            str(
                value or 0
            ).replace(
                ",",
                "",
            )
        )

    except (
        TypeError,
        ValueError,
    ):
        return 0.0


def _productivity_machine_hd_v2_result(
    value,
):

    value = float(
        value or 0
    )


    if value <= 0:
        return ""


    # Keep meaningful precision for low machine-level values.
    if value < 1:

        return "{:,.3f}".format(
            value
        )


    return "{:,.0f}".format(
        value
    )


def _productivity_apply_machine_specific_hd_v2(
    rows,
    filters=None,
):

    filters = frappe._dict(
        filters or {}
    )


    if str(
        filters.get(
            "summary_view"
        )
        or ""
    ).strip() != "Summary Per Machine":

        return rows


    rows = [
        dict(row)
        if hasattr(
            row,
            "get",
        )
        else row

        for row in (
            rows or []
        )
    ]


    MACHINE_HD_CATEGORIES = (
        "ADT",
        "Dozer",
    )


    current_category = ""

    exact_keys = []


    # ========================================================
    # PASS 1
    #
    # Give EVERY ADT/Dozer material row its own unique key.
    # ========================================================

    for row in rows:

        if not hasattr(
            row,
            "get",
        ):
            continue


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()


        if int(
            row.get(
                "is_monthly_plan_header"
            )
            or 0
        ) == 1:

            current_category = ""

            continue


        if (
            label
            in (
                "Excavator",
                "ADT",
                "Dozer",
            )
            and int(
                row.get(
                    "is_category_total"
                )
                or 0
            ) == 1
        ):

            current_category = label

            continue


        category = str(
            row.get(
                "productivity_edit_category"
            )
            or row.get(
                "category"
            )
            or current_category
            or ""
        ).strip()


        if (
            category not in MACHINE_HD_CATEGORIES
            or not material
        ):

            continue


        # Summary Per Machine label is the machine itself.
        machine = str(
            row.get(
                "machine"
            )
            or row.get(
                "productivity_edit_machine"
            )
            or label
            or ""
        ).strip()


        if not machine:
            continue


        site = str(
            row.get(
                "productivity_edit_site"
            )
            or filters.get(
                "site"
            )
            or ""
        ).strip()


        start_date = str(
            row.get(
                "monthly_start_date"
            )
            or row.get(
                "productivity_edit_start_date"
            )
            or filters.get(
                "start_date"
            )
            or ""
        ).strip()


        end_date = str(
            row.get(
                "monthly_end_date"
            )
            or row.get(
                "productivity_edit_end_date"
            )
            or filters.get(
                "end_date"
            )
            or ""
        ).strip()


        shift = str(
            row.get(
                "productivity_edit_shift"
            )
            or filters.get(
                "shift"
            )
            or ""
        ).strip()


        monthly_plan = str(
            row.get(
                "monthly_production_plan"
            )
            or row.get(
                "productivity_edit_monthly_plan"
            )
            or ""
        ).strip()


        row_key = (
            _productivity_manual_edit_key(
                site,
                start_date,
                end_date,
                shift,
                monthly_plan,
                category,
                machine,
                material,
            )
        )


        row[
            "productivity_editable"
        ] = 1


        row[
            "productivity_edit_key"
        ] = row_key


        row[
            "productivity_edit_site"
        ] = site


        row[
            "productivity_edit_start_date"
        ] = start_date


        row[
            "productivity_edit_end_date"
        ] = end_date


        row[
            "productivity_edit_shift"
        ] = shift


        row[
            "productivity_edit_monthly_plan"
        ] = monthly_plan


        row[
            "productivity_edit_category"
        ] = category


        row[
            "productivity_edit_machine"
        ] = machine


        row[
            "productivity_edit_material"
        ] = material


        exact_keys.append(
            row_key
        )


    # ========================================================
    # LOAD ONLY EXACT MACHINE + MATERIAL SAVED OVERRIDES
    # ========================================================

    exact_saved = {}


    if (
        exact_keys
        and frappe.db.exists(
            "DocType",
            "Productivity Area Override",
        )
    ):

        saved_rows = frappe.get_all(
            "Productivity Area Override",

            filters={
                "row_key": [
                    "in",
                    list(
                        set(
                            exact_keys
                        )
                    ),
                ],
            },

            fields=[
                "row_key",
                "from_area",
                "to_area",
                "hauling_distance_m",
            ],

            limit_page_length=0,
        )


        exact_saved = {
            str(
                saved.row_key
            ):
                saved

            for saved in saved_rows
        }


    # ========================================================
    # PASS 2
    #
    # Apply exact machine-specific values.
    # ========================================================

    current_category = ""


    for row in rows:

        if not hasattr(
            row,
            "get",
        ):
            continue


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()


        # ----------------------------------------------------
        # MONTH HEADER
        # ----------------------------------------------------

        if int(
            row.get(
                "is_monthly_plan_header"
            )
            or 0
        ) == 1:

            current_category = ""

            row[
                "hauling_distance_m"
            ] = ""

            row[
                "productivity_bcm_hd"
            ] = ""

            continue


        # ----------------------------------------------------
        # CATEGORY ROW
        # ----------------------------------------------------

        if (
            label
            in (
                "Excavator",
                "ADT",
                "Dozer",
            )
            and int(
                row.get(
                    "is_category_total"
                )
                or 0
            ) == 1
        ):

            current_category = label


            row[
                "hauling_distance_m"
            ] = ""


            row[
                "productivity_bcm_hd"
            ] = ""


            continue


        # ----------------------------------------------------
        # TOTAL FLEET
        # ----------------------------------------------------

        if int(
            row.get(
                "is_total_fleet"
            )
            or 0
        ) == 1:

            row[
                "hauling_distance_m"
            ] = ""


            row[
                "productivity_bcm_hd"
            ] = ""


            continue


        category = str(
            row.get(
                "productivity_edit_category"
            )
            or row.get(
                "category"
            )
            or current_category
            or ""
        ).strip()


        # ----------------------------------------------------
        # EXCAVATOR BLANK
        # ----------------------------------------------------

        if category == "Excavator":

            row[
                "from_area"
            ] = ""


            row[
                "to_area"
            ] = ""


            row[
                "hauling_distance_m"
            ] = ""


            row[
                "productivity_bcm_hd"
            ] = ""


            continue


        # ----------------------------------------------------
        # MACHINE TOTAL ROW
        # ----------------------------------------------------

        if not material:

            row[
                "hauling_distance_m"
            ] = ""


            row[
                "productivity_bcm_hd"
            ] = ""


            continue


        if category not in MACHINE_HD_CATEGORIES:

            continue


        row_key = str(
            row.get(
                "productivity_edit_key"
            )
            or ""
        ).strip()


        saved = (
            exact_saved.get(
                row_key
            )
            if row_key
            else None
        )


        # ----------------------------------------------------
        # FROM AREA
        #
        # Keep automatically sourced From Area unless user
        # previously manually saved a nonblank override.
        # ----------------------------------------------------

        if (
            saved
            and str(
                saved.from_area
                or ""
            ).strip()
        ):

            row[
                "from_area"
            ] = str(
                saved.from_area
                or ""
            ).strip()


        # ----------------------------------------------------
        # TO AREA / HAULING DISTANCE
        #
        # EXACT machine + material only.
        #
        # No category fallback.
        # ----------------------------------------------------

        if saved:

            row[
                "to_area"
            ] = str(
                saved.to_area
                or ""
            ).strip()


            distance = str(
                saved.hauling_distance_m
                or ""
            ).strip()

        else:

            # User must capture this machine's own value.
            row[
                "to_area"
            ] = ""


            distance = ""


        if distance in (
            "0",
            "0.0",
            "0.00",
            "0.000",
        ):

            distance = ""


        row[
            "hauling_distance_m"
        ] = distance


        # ----------------------------------------------------
        # BCM/HD
        # ----------------------------------------------------

        midpoint = (
            _productivity_distance_midpoint(
                distance
            )
        )


        if (
            midpoint is None
            or midpoint <= 0
        ):

            row[
                "productivity_bcm_hd"
            ] = ""

            continue


        selected_bcm = (
            _productivity_machine_hd_v2_number(
                row.get(
                    "output"
                )
            )
        )


        if selected_bcm <= 0:

            row[
                "productivity_bcm_hd"
            ] = ""

            continue


        row[
            "productivity_bcm_hd"
        ] = (
            _productivity_machine_hd_v2_result(
                selected_bcm
                / midpoint
            )
        )


    return rows


def execute(
    filters=None,
):

    original_result = (
        _productivity_execute_before_machine_specific_hd_v2(
            filters
        )
    )


    if not original_result:

        return original_result


    if isinstance(
        original_result,
        tuple,
    ):

        parts = list(
            original_result
        )

        return_tuple = True

    elif isinstance(
        original_result,
        list,
    ):

        parts = list(
            original_result
        )

        return_tuple = False

    else:

        return original_result


    if (
        len(parts) > 1
        and parts[1]
    ):

        parts[1] = (
            _productivity_apply_machine_specific_hd_v2(
                parts[1],
                filters,
            )
        )


    if return_tuple:

        return tuple(
            parts
        )


    return parts


# END KOSI_PRODUCTIVITY_ADT_DOZER_MACHINE_HD_V2



# ============================================================
# KOSI_PRODUCTIVITY_DOZER_SPECIFIC_HD_FINAL_V1
#
# SUMMARY PER MACHINE
#
# Every Dozer + Material row gets its OWN override.
#
# Examples:
#
# IS0335 + 2 - Midburden
# IS0336 + 2 - Midburden
# IS0338 + 1 - Overburden
# IS0338 + 2 - Midburden
# IS0340 + 1 - Overburden
# IS0340 + 2 - Midburden
#
# Each row independently stores:
#
#     To Area
#     Hauling Distance
#
# From Area remains automatic.
#
# No shared Dozer/material distance is inherited.
#
# BCM/HD:
#
#     selected BCM / average hauling distance
#
# Average HD:
#
#     (lower + upper) / 2
#
# Totals remain blank.
# ============================================================


_productivity_execute_before_dozer_specific_hd_final = execute


def _productivity_dozer_specific_number(
    value,
):

    try:
        return float(
            str(
                value or 0
            ).replace(
                ",",
                "",
            )
        )
    except (
        TypeError,
        ValueError,
    ):
        return 0.0


def _productivity_dozer_specific_result(
    value,
):

    value = float(
        value or 0
    )

    if value <= 0:
        return ""

    if value < 1:
        return "{:,.3f}".format(
            value
        )

    return "{:,.0f}".format(
        value
    )


def _productivity_apply_dozer_specific_hd_final(
    rows,
    filters=None,
):

    filters = frappe._dict(
        filters or {}
    )


    if str(
        filters.get(
            "summary_view"
        )
        or ""
    ).strip() != "Summary Per Machine":

        return rows


    rows = [
        dict(row)
        if hasattr(
            row,
            "get",
        )
        else row

        for row in (
            rows or []
        )
    ]


    current_category = ""

    dozer_keys = []


    # ========================================================
    # PASS 1
    #
    # Generate exact:
    #
    # Site + Period + Dozer + Material
    #
    # keys.
    # ========================================================

    for row in rows:

        if not hasattr(
            row,
            "get",
        ):
            continue


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()


        if int(
            row.get(
                "is_monthly_plan_header"
            )
            or 0
        ) == 1:

            current_category = ""
            continue


        if (
            label
            in (
                "Excavator",
                "ADT",
                "Dozer",
            )
            and int(
                row.get(
                    "is_category_total"
                )
                or 0
            ) == 1
        ):

            current_category = label
            continue


        category = str(
            row.get(
                "productivity_edit_category"
            )
            or row.get(
                "category"
            )
            or current_category
            or ""
        ).strip()


        if (
            category != "Dozer"
            or not material
        ):

            continue


        machine = str(
            row.get(
                "machine"
            )
            or row.get(
                "productivity_edit_machine"
            )
            or label
            or ""
        ).strip()


        if not machine:
            continue


        site = str(
            row.get(
                "productivity_edit_site"
            )
            or filters.get(
                "site"
            )
            or ""
        ).strip()


        start_date = str(
            row.get(
                "monthly_start_date"
            )
            or row.get(
                "productivity_edit_start_date"
            )
            or filters.get(
                "start_date"
            )
            or ""
        ).strip()


        end_date = str(
            row.get(
                "monthly_end_date"
            )
            or row.get(
                "productivity_edit_end_date"
            )
            or filters.get(
                "end_date"
            )
            or ""
        ).strip()


        shift = str(
            row.get(
                "productivity_edit_shift"
            )
            or filters.get(
                "shift"
            )
            or ""
        ).strip()


        monthly_plan = str(
            row.get(
                "monthly_production_plan"
            )
            or row.get(
                "productivity_edit_monthly_plan"
            )
            or ""
        ).strip()


        row_key = (
            _productivity_manual_edit_key(
                site,
                start_date,
                end_date,
                shift,
                monthly_plan,
                "Dozer",
                machine,
                material,
            )
        )


        row[
            "productivity_editable"
        ] = 1


        row[
            "productivity_edit_key"
        ] = row_key


        row[
            "productivity_edit_site"
        ] = site


        row[
            "productivity_edit_start_date"
        ] = start_date


        row[
            "productivity_edit_end_date"
        ] = end_date


        row[
            "productivity_edit_shift"
        ] = shift


        row[
            "productivity_edit_monthly_plan"
        ] = monthly_plan


        row[
            "productivity_edit_category"
        ] = "Dozer"


        row[
            "productivity_edit_machine"
        ] = machine


        row[
            "productivity_edit_material"
        ] = material


        dozer_keys.append(
            row_key
        )


    # ========================================================
    # LOAD EXACT SAVED DOZER VALUES
    # ========================================================

    saved_map = {}


    if (
        dozer_keys
        and frappe.db.exists(
            "DocType",
            "Productivity Area Override",
        )
    ):

        saved_rows = frappe.get_all(
            "Productivity Area Override",

            filters={
                "row_key": [
                    "in",
                    list(
                        set(
                            dozer_keys
                        )
                    ),
                ],
            },

            fields=[
                "row_key",
                "from_area",
                "to_area",
                "hauling_distance_m",
            ],

            limit_page_length=0,
        )


        saved_map = {
            str(saved.row_key):
                saved

            for saved in saved_rows
        }


    # ========================================================
    # PASS 2
    #
    # Apply exact saved value only.
    # ========================================================

    current_category = ""


    for row in rows:

        if not hasattr(
            row,
            "get",
        ):
            continue


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()


        if int(
            row.get(
                "is_monthly_plan_header"
            )
            or 0
        ) == 1:

            current_category = ""
            continue


        if (
            label
            in (
                "Excavator",
                "ADT",
                "Dozer",
            )
            and int(
                row.get(
                    "is_category_total"
                )
                or 0
            ) == 1
        ):

            current_category = label


            if label == "Dozer":

                row[
                    "hauling_distance_m"
                ] = ""

                row[
                    "productivity_bcm_hd"
                ] = ""


            continue


        category = str(
            row.get(
                "productivity_edit_category"
            )
            or row.get(
                "category"
            )
            or current_category
            or ""
        ).strip()


        if category != "Dozer":
            continue


        # Dozer machine summary row.
        if not material:

            row[
                "hauling_distance_m"
            ] = ""

            row[
                "productivity_bcm_hd"
            ] = ""

            continue


        row_key = str(
            row.get(
                "productivity_edit_key"
            )
            or ""
        ).strip()


        saved = (
            saved_map.get(
                row_key
            )
            if row_key
            else None
        )


        # ----------------------------------------------------
        # Keep automatic From Area unless manually overridden.
        # ----------------------------------------------------

        if (
            saved
            and str(
                saved.from_area
                or ""
            ).strip()
        ):

            row[
                "from_area"
            ] = str(
                saved.from_area
                or ""
            ).strip()


        # ----------------------------------------------------
        # Exact Dozer/material values only.
        # ----------------------------------------------------

        if saved:

            row[
                "to_area"
            ] = str(
                saved.to_area
                or ""
            ).strip()


            distance = str(
                saved.hauling_distance_m
                or ""
            ).strip()

        else:

            row[
                "to_area"
            ] = ""

            distance = ""


        if distance in (
            "0",
            "0.0",
            "0.00",
            "0.000",
        ):

            distance = ""


        row[
            "hauling_distance_m"
        ] = distance


        # ----------------------------------------------------
        # BCM/HD
        # ----------------------------------------------------

        midpoint = (
            _productivity_distance_midpoint(
                distance
            )
        )


        if (
            midpoint is None
            or midpoint <= 0
        ):

            row[
                "productivity_bcm_hd"
            ] = ""

            continue


        selected_bcm = (
            _productivity_dozer_specific_number(
                row.get(
                    "output"
                )
            )
        )


        if selected_bcm <= 0:

            row[
                "productivity_bcm_hd"
            ] = ""

            continue


        row[
            "productivity_bcm_hd"
        ] = (
            _productivity_dozer_specific_result(
                selected_bcm
                / midpoint
            )
        )


    return rows


def execute(
    filters=None,
):

    original_result = (
        _productivity_execute_before_dozer_specific_hd_final(
            filters
        )
    )


    if not original_result:

        return original_result


    if isinstance(
        original_result,
        tuple,
    ):

        parts = list(
            original_result
        )

        return_tuple = True


    elif isinstance(
        original_result,
        list,
    ):

        parts = list(
            original_result
        )

        return_tuple = False


    else:

        return original_result


    if (
        len(parts) > 1
        and parts[1]
    ):

        parts[1] = (
            _productivity_apply_dozer_specific_hd_final(
                parts[1],
                filters,
            )
        )


    if return_tuple:

        return tuple(
            parts
        )


    return parts


# END KOSI_PRODUCTIVITY_DOZER_SPECIFIC_HD_FINAL_V1



# ============================================================
# KOSI_PRODUCTIVITY_STRICT_MACHINE_HD_V7
#
# SUMMARY PER MACHINE
#
# ADT / DOZER hauling distance:
#
#     NEVER inherit.
#
#     NEVER fetch shared category/material distance.
#
#     NEVER fetch another machine's value.
#
# ONLY:
#
#     exact Machine + Material saved override
#
# If user has never captured that exact row:
#
#     Hauling Distance = blank
#     To Area          = blank
#     BCM/HD           = blank
#
# From Area remains automatic.
#
# ============================================================


_productivity_execute_before_strict_machine_hd_v7 = execute


def _productivity_strict_hd_number_v7(
    value,
):

    try:

        return float(
            str(
                value or 0
            ).replace(
                ",",
                "",
            )
        )

    except (
        TypeError,
        ValueError,
    ):

        return 0.0


def _productivity_strict_hd_result_v7(
    value,
):

    value = float(
        value or 0
    )


    if value <= 0:

        return ""


    if value < 1:

        return "{:,.3f}".format(
            value
        )


    return "{:,.0f}".format(
        value
    )


def _productivity_apply_strict_machine_hd_v7(
    rows,
    filters=None,
):

    filters = frappe._dict(
        filters or {}
    )


    if str(
        filters.get(
            "summary_view"
        )
        or ""
    ).strip() != "Summary Per Machine":

        return rows


    rows = [
        dict(row)
        if hasattr(
            row,
            "get",
        )
        else row

        for row in (
            rows or []
        )
    ]


    categories = (
        "ADT",
        "Dozer",
    )


    current_category = ""

    target_rows = []

    exact_keys = []


    # ========================================================
    # PASS 1
    #
    # Rebuild exact machine keys.
    # ========================================================

    for row in rows:

        if not hasattr(
            row,
            "get",
        ):

            continue


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()


        if int(
            row.get(
                "is_monthly_plan_header"
            )
            or 0
        ) == 1:

            current_category = ""

            continue


        if (
            label in (
                "Excavator",
                "ADT",
                "Dozer",
            )
            and int(
                row.get(
                    "is_category_total"
                )
                or 0
            ) == 1
        ):

            current_category = label

            continue


        category = str(
            row.get(
                "productivity_edit_category"
            )
            or row.get(
                "category"
            )
            or current_category
            or ""
        ).strip()


        if (
            category not in categories
            or not material
        ):

            continue


        # Summary Per Machine:
        # Label is the actual machine.
        machine = label


        site = str(
            row.get(
                "productivity_edit_site"
            )
            or filters.get(
                "site"
            )
            or ""
        ).strip()


        start_date = str(
            row.get(
                "monthly_start_date"
            )
            or row.get(
                "productivity_edit_start_date"
            )
            or filters.get(
                "start_date"
            )
            or ""
        ).strip()


        end_date = str(
            row.get(
                "monthly_end_date"
            )
            or row.get(
                "productivity_edit_end_date"
            )
            or filters.get(
                "end_date"
            )
            or ""
        ).strip()


        shift = str(
            row.get(
                "productivity_edit_shift"
            )
            or filters.get(
                "shift"
            )
            or ""
        ).strip()


        monthly_plan = str(
            row.get(
                "monthly_production_plan"
            )
            or row.get(
                "productivity_edit_monthly_plan"
            )
            or ""
        ).strip()


        row_key = (
            _productivity_manual_edit_key(
                site,
                start_date,
                end_date,
                shift,
                monthly_plan,
                category,
                machine,
                material,
            )
        )


        row[
            "productivity_editable"
        ] = 1


        row[
            "productivity_edit_key"
        ] = row_key


        row[
            "productivity_edit_site"
        ] = site


        row[
            "productivity_edit_start_date"
        ] = start_date


        row[
            "productivity_edit_end_date"
        ] = end_date


        row[
            "productivity_edit_shift"
        ] = shift


        row[
            "productivity_edit_monthly_plan"
        ] = monthly_plan


        row[
            "productivity_edit_category"
        ] = category


        row[
            "productivity_edit_machine"
        ] = machine


        row[
            "productivity_edit_material"
        ] = material


        # IMPORTANT:
        #
        # Clear anything supplied by earlier wrappers.
        row[
            "to_area"
        ] = ""


        row[
            "hauling_distance_m"
        ] = ""


        row[
            "productivity_bcm_hd"
        ] = ""


        exact_keys.append(
            row_key
        )


        target_rows.append(
            row
        )


    # ========================================================
    # FETCH ONLY EXACT SAVED MACHINE KEYS.
    # ========================================================

    saved_map = {}


    if (
        exact_keys
        and frappe.db.exists(
            "DocType",
            "Productivity Area Override",
        )
    ):

        saved_rows = frappe.get_all(
            "Productivity Area Override",

            filters={
                "row_key": [
                    "in",
                    list(
                        set(
                            exact_keys
                        )
                    ),
                ],
            },

            fields=[
                "row_key",
                "to_area",
                "hauling_distance_m",
            ],

            limit_page_length=0,
        )


        saved_map = {
            str(
                saved.row_key
            ):
                saved

            for saved in saved_rows
        }


    # ========================================================
    # PASS 2
    #
    # Exact saved record or BLANK.
    # ========================================================

    for row in target_rows:

        row_key = str(
            row.get(
                "productivity_edit_key"
            )
            or ""
        ).strip()


        saved = (
            saved_map.get(
                row_key
            )
        )


        if not saved:

            # User has not captured this exact machine yet.
            row[
                "to_area"
            ] = ""


            row[
                "hauling_distance_m"
            ] = ""


            row[
                "productivity_bcm_hd"
            ] = ""


            continue


        distance = str(
            saved.hauling_distance_m
            or ""
        ).strip()


        if distance in (
            "",
            "0",
            "0.0",
            "0.00",
            "0.000",
        ):

            distance = ""


        row[
            "to_area"
        ] = str(
            saved.to_area
            or ""
        ).strip()


        row[
            "hauling_distance_m"
        ] = distance


        if not distance:

            row[
                "productivity_bcm_hd"
            ] = ""

            continue


        midpoint = (
            _productivity_distance_midpoint(
                distance
            )
        )


        if (
            midpoint is None
            or midpoint <= 0
        ):

            row[
                "productivity_bcm_hd"
            ] = ""

            continue


        bcm = (
            _productivity_strict_hd_number_v7(
                row.get(
                    "output"
                )
            )
        )


        if bcm <= 0:

            row[
                "productivity_bcm_hd"
            ] = ""

            continue


        row[
            "productivity_bcm_hd"
        ] = (
            _productivity_strict_hd_result_v7(
                bcm
                / midpoint
            )
        )


    # ========================================================
    # FORCE ALL TOTAL ROWS BLANK.
    # ========================================================

    for row in rows:

        if not hasattr(
            row,
            "get",
        ):

            continue


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()


        if (
            label in (
                "ADT",
                "Dozer",
                "Total Fleet",
            )
            or (
                not material
                and str(
                    row.get(
                        "productivity_edit_category"
                    )
                    or ""
                ) in categories
            )
        ):

            row[
                "hauling_distance_m"
            ] = ""


            row[
                "productivity_bcm_hd"
            ] = ""


    return rows


def execute(
    filters=None,
):

    original_result = (
        _productivity_execute_before_strict_machine_hd_v7(
            filters
        )
    )


    if not original_result:

        return original_result


    if isinstance(
        original_result,
        tuple,
    ):

        parts = list(
            original_result
        )

        return_tuple = True


    elif isinstance(
        original_result,
        list,
    ):

        parts = list(
            original_result
        )

        return_tuple = False


    else:

        return original_result


    if (
        len(parts) > 1
        and parts[1]
    ):

        parts[1] = (
            _productivity_apply_strict_machine_hd_v7(
                parts[1],
                filters,
            )
        )


    if return_tuple:

        return tuple(
            parts
        )


    return parts


# END KOSI_PRODUCTIVITY_STRICT_MACHINE_HD_V7



# ============================================================
# KOSI_PRODUCTIVITY_VIEW_SCOPED_OVERRIDES_V8
#
# CRITICAL RULE:
#
# Hours and Material
#
# and
#
# Summary Per Machine
#
# are TWO SEPARATE Productivity reports.
#
# Overrides must NEVER flow between them.
#
# Saved row key:
#
# VIEW::Hours and Material::<base key>
#
# VIEW::Summary Per Machine::<base key>
#
# ============================================================


_productivity_execute_before_view_scoped_v8 = execute


def _productivity_view_scope_key_v8(
    summary_view,
    row_key,
):

    summary_view = str(
        summary_view
        or ""
    ).strip()


    row_key = str(
        row_key
        or ""
    ).strip()


    if not row_key:

        return ""


    # Strip an existing view prefix if present.
    if row_key.startswith(
        "VIEW::"
    ):

        parts = row_key.split(
            "::",
            2,
        )


        if len(parts) == 3:

            row_key = parts[2]


    return (
        "VIEW::"
        + summary_view
        + "::"
        + row_key
    )


def _productivity_view_number_v8(
    value,
):

    try:

        return float(
            str(
                value
                or 0
            ).replace(
                ",",
                "",
            )
        )

    except (
        TypeError,
        ValueError,
    ):

        return 0.0


def _productivity_view_bcm_hd_v8(
    value,
):

    value = float(
        value
        or 0
    )


    if value <= 0:

        return ""


    if value < 1:

        return "{:,.3f}".format(
            value
        )


    return "{:,.0f}".format(
        value
    )


def _productivity_apply_view_scoped_overrides_v8(
    rows,
    filters=None,
):

    filters = frappe._dict(
        filters
        or {}
    )


    summary_view = str(
        filters.get(
            "summary_view"
        )
        or "Summary Per Machine"
    ).strip()


    if summary_view not in (
        "Hours and Material",
        "Summary Per Machine",
    ):

        return rows


    rows = [
        dict(row)
        if hasattr(
            row,
            "get",
        )
        else row

        for row in (
            rows
            or []
        )
    ]


    categories = (
        "ADT",
        "Dozer",
    )


    current_category = ""

    target_rows = []

    scoped_keys = []


    # ========================================================
    # PASS 1
    #
    # Give each row a REPORT-VIEW-SPECIFIC key.
    # ========================================================

    for row in rows:

        if not hasattr(
            row,
            "get",
        ):

            continue


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()


        if int(
            row.get(
                "is_monthly_plan_header"
            )
            or 0
        ) == 1:

            current_category = ""

            continue


        if (
            label in (
                "Excavator",
                "ADT",
                "Dozer",
            )
            and int(
                row.get(
                    "is_category_total"
                )
                or 0
            ) == 1
        ):

            current_category = label

            continue


        category = str(
            row.get(
                "productivity_edit_category"
            )
            or row.get(
                "category"
            )
            or current_category
            or ""
        ).strip()


        if (
            category not in categories
            or not material
        ):

            continue


        base_key = str(
            row.get(
                "productivity_edit_key"
            )
            or ""
        ).strip()


        if not base_key:

            continue


        scoped_key = (
            _productivity_view_scope_key_v8(
                summary_view,
                base_key,
            )
        )


        row[
            "productivity_edit_key"
        ] = scoped_key


        # ----------------------------------------------------
        # IMPORTANT:
        #
        # Remove anything loaded by an earlier wrapper.
        #
        # We will now reload ONLY the record belonging to the
        # current report view.
        # ----------------------------------------------------

        row[
            "to_area"
        ] = ""


        row[
            "hauling_distance_m"
        ] = ""


        row[
            "productivity_bcm_hd"
        ] = ""


        target_rows.append(
            row
        )


        scoped_keys.append(
            scoped_key
        )


    # ========================================================
    # LOAD ONLY CURRENT REPORT VIEW
    # ========================================================

    saved_map = {}


    if (
        scoped_keys
        and frappe.db.exists(
            "DocType",
            "Productivity Area Override",
        )
    ):

        saved_rows = frappe.get_all(
            "Productivity Area Override",

            filters={
                "row_key": [
                    "in",
                    list(
                        set(
                            scoped_keys
                        )
                    ),
                ],
            },

            fields=[
                "row_key",
                "to_area",
                "hauling_distance_m",
            ],

            limit_page_length=0,
        )


        saved_map = {
            str(
                saved.row_key
            ):
                saved

            for saved in saved_rows
        }


    # ========================================================
    # APPLY ONLY MATCHING VIEW RECORD
    # ========================================================

    for row in target_rows:

        row_key = str(
            row.get(
                "productivity_edit_key"
            )
            or ""
        ).strip()


        saved = (
            saved_map.get(
                row_key
            )
        )


        if not saved:

            # Nothing captured in THIS report.
            row[
                "to_area"
            ] = ""


            row[
                "hauling_distance_m"
            ] = ""


            row[
                "productivity_bcm_hd"
            ] = ""


            continue


        distance = str(
            saved.hauling_distance_m
            or ""
        ).strip()


        if distance in (
            "",
            "0",
            "0.0",
            "0.00",
            "0.000",
        ):

            distance = ""


        row[
            "to_area"
        ] = str(
            saved.to_area
            or ""
        ).strip()


        row[
            "hauling_distance_m"
        ] = distance


        if not distance:

            row[
                "productivity_bcm_hd"
            ] = ""

            continue


        midpoint = (
            _productivity_distance_midpoint(
                distance
            )
        )


        if (
            midpoint is None
            or midpoint <= 0
        ):

            row[
                "productivity_bcm_hd"
            ] = ""

            continue


        bcm = (
            _productivity_view_number_v8(
                row.get(
                    "output"
                )
            )
        )


        if bcm <= 0:

            row[
                "productivity_bcm_hd"
            ] = ""

            continue


        row[
            "productivity_bcm_hd"
        ] = (
            _productivity_view_bcm_hd_v8(
                bcm
                / midpoint
            )
        )


    # ========================================================
    # NO TOTALS
    # ========================================================

    for row in rows:

        if not hasattr(
            row,
            "get",
        ):

            continue


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()


        if (
            label in (
                "ADT",
                "Dozer",
                "Total Fleet",
            )
            or (
                not material
                and str(
                    row.get(
                        "productivity_edit_category"
                    )
                    or ""
                ).strip()
                in categories
            )
        ):

            row[
                "hauling_distance_m"
            ] = ""


            row[
                "productivity_bcm_hd"
            ] = ""


    return rows


def execute(
    filters=None,
):

    original_result = (
        _productivity_execute_before_view_scoped_v8(
            filters
        )
    )


    if not original_result:

        return original_result


    if isinstance(
        original_result,
        tuple,
    ):

        parts = list(
            original_result
        )

        return_tuple = True


    elif isinstance(
        original_result,
        list,
    ):

        parts = list(
            original_result
        )

        return_tuple = False


    else:

        return original_result


    if (
        len(parts) > 1
        and parts[1]
    ):

        parts[1] = (
            _productivity_apply_view_scoped_overrides_v8(
                parts[1],
                filters,
            )
        )


    if return_tuple:

        return tuple(
            parts
        )


    return parts


# END KOSI_PRODUCTIVITY_VIEW_SCOPED_OVERRIDES_V8



# ============================================================
# KOSI_PRODUCTIVITY_ALL_TOTALS_BOLD_V14
#
# Mark the TRUE machine total rows in Summary Per Machine.
#
# Bold rows:
#
#     Monthly Production heading
#     Excavator
#     ADT
#     Dozer
#     EX01 / ADT01 / IS0335 etc. MACHINE TOTAL
#     Total Fleet
#
# Material/detail rows remain normal.
# ============================================================


_productivity_execute_before_all_totals_bold_v14 = execute


def _productivity_mark_total_rows_v14(
    rows,
    filters=None,
):

    filters = frappe._dict(
        filters
        or {}
    )


    summary_view = str(
        filters.get(
            "summary_view"
        )
        or ""
    ).strip()


    categories = (
        "Excavator",
        "ADT",
        "Dozer",
    )


    current_category = ""

    seen_machines = set()


    for row in (
        rows
        or []
    ):

        if not hasattr(
            row,
            "get",
        ):

            continue


        # Default.
        row[
            "productivity_is_machine_total"
        ] = 0


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()


        # ----------------------------------------------------
        # New monthly section.
        # ----------------------------------------------------

        if (
            int(
                row.get(
                    "is_monthly_plan_header"
                )
                or 0
            ) == 1
            or label.startswith(
                "MONTHLY PRODUCTION:"
            )
        ):

            current_category = ""
            seen_machines = set()

            continue


        # ----------------------------------------------------
        # Category total.
        # ----------------------------------------------------

        if (
            label in categories
            and int(
                row.get(
                    "is_category_total"
                )
                or 0
            ) == 1
        ):

            current_category = label

            continue


        if summary_view != "Summary Per Machine":

            continue


        # ----------------------------------------------------
        # Exclusions.
        # ----------------------------------------------------

        if (
            not label
            or label == "Total Fleet"
            or label in categories
            or material
        ):

            continue


        if current_category not in categories:

            continue


        # ----------------------------------------------------
        # First non-material row for a machine is its machine
        # TOTAL row.
        #
        # This avoids making duplicate/unallocated rows bold.
        # ----------------------------------------------------

        machine_key = (
            current_category,
            label,
        )


        if machine_key in seen_machines:

            continue


        seen_machines.add(
            machine_key
        )


        row[
            "productivity_is_machine_total"
        ] = 1


    return rows


def execute(
    filters=None,
):

    original_result = (
        _productivity_execute_before_all_totals_bold_v14(
            filters
        )
    )


    if not original_result:

        return original_result


    if isinstance(
        original_result,
        tuple,
    ):

        parts = list(
            original_result
        )

        return_tuple = True


    elif isinstance(
        original_result,
        list,
    ):

        parts = list(
            original_result
        )

        return_tuple = False


    else:

        return original_result


    if (
        len(parts) > 1
        and parts[1]
    ):

        parts[1] = (
            _productivity_mark_total_rows_v14(
                parts[1],
                filters,
            )
        )


    if return_tuple:

        return tuple(
            parts
        )


    return parts


# END KOSI_PRODUCTIVITY_ALL_TOTALS_BOLD_V14


# ============================================================
# KOSI_PRODUCTIVITY_SURVEY_ACTUAL_V15
#
# ACTUAL BCM SURVEY ALIGNMENT
#
# Rules:
#
# - Applies ONLY to Actual BCMs.
# - Tallies BCMs remain unchanged.
# - Working Hours remain from Pre-Use.
# - Survey documents are cumulative MTD snapshots.
# - Never sum Survey documents together.
# - Use latest submitted Survey for the selected MPP whose
#   last_production_shift_start_date is on/before report end.
#
# Truck + Shovel Survey BCM:
#     Excavator and ADT use the same surveyed BCM.
#
# Dozing Survey BCM:
#     Dozer uses surveyed Dozing BCM.
#
# Total Fleet:
#     Excavator + Dozer.
#     ADT is NOT added again.
#
# Existing machine/material distribution is retained
# proportionally where multiple machine rows exist, but category
# + material BCM totals are forced to the Survey source of truth.
# ============================================================

import json as _productivity_json_v15
import re as _productivity_re_v15
from collections import defaultdict as _productivity_defaultdict_v15


_productivity_execute_before_survey_actual_v15 = execute


def _productivity_float_v15(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _productivity_date_v15(value):
    if not value:
        return None

    try:
        return frappe.utils.getdate(value)
    except Exception:
        return None


def _productivity_category_v15(value):
    text = str(value or "").strip().lower()

    if not text:
        return ""

    if "excavator" in text:
        return "Excavator"

    if (
        text == "adt"
        or text.startswith("adt")
        or " adt" in text
    ):
        return "ADT"

    if "dozer" in text:
        return "Dozer"

    return ""


def _productivity_material_v15(
    material,
    fallback=None,
):
    text = " ".join(
        str(value or "").strip()
        for value in (
            material,
            fallback,
        )
        if value
    ).lower()

    if not text:
        return "Unassigned"

    if "coal" in text:
        return "Coal"

    if (
        "soft" in text
        or "topsoil" in text
    ):
        return "Softs"

    if (
        "hard" in text
        or "midburden" in text
        or "overburden" in text
        or "burden" in text
        or "waste" in text
    ):
        return "Hards"

    return (
        str(material or fallback or "Unassigned")
        .strip()
    )


def _productivity_selected_plans_v15(filters):
    filters = frappe._dict(
        filters or {}
    )

    result = []

    keys = (
        "monthly_production_plans",
        "monthly_production_plan",
        "monthly_production",
        "define_monthly_production",
    )

    for key in keys:
        raw = filters.get(key)

        if not raw:
            continue

        values = []

        if isinstance(
            raw,
            (
                list,
                tuple,
                set,
            ),
        ):
            values = list(raw)

        elif isinstance(raw, str):
            text = raw.strip()

            if not text:
                continue

            try:
                parsed = (
                    _productivity_json_v15.loads(
                        text
                    )
                )

                if isinstance(
                    parsed,
                    list,
                ):
                    values = parsed
                else:
                    values = [text]

            except Exception:
                values = [
                    part.strip()
                    for part in text.split(",")
                    if part.strip()
                ]

        else:
            values = [raw]

        for value in values:
            name = str(
                value or ""
            ).strip()

            if (
                name
                and name not in result
            ):
                result.append(
                    name
                )

    return result


def _productivity_plan_end_v15(
    plan_name,
):
    match = (
        _productivity_re_v15.match(
            r"^(\d{4}-\d{2}-\d{2})",
            str(plan_name or ""),
        )
    )

    if not match:
        return None

    return _productivity_date_v15(
        match.group(1)
    )


def _productivity_header_range_v15(
    label,
):
    matches = (
        _productivity_re_v15.findall(
            r"(\d{2})-(\d{2})-(\d{4})",
            str(label or ""),
        )
    )

    if not matches:
        return None, None

    dates = []

    for day, month, year in matches:
        value = (
            _productivity_date_v15(
                f"{year}-{month}-{day}"
            )
        )

        if value:
            dates.append(value)

    if not dates:
        return None, None

    return (
        dates[0],
        dates[-1],
    )


def _productivity_plan_for_section_v15(
    header_label,
    plans,
    section_index,
):
    if not plans:
        return None

    start_date, end_date = (
        _productivity_header_range_v15(
            header_label
        )
    )

    if end_date:
        for plan in plans:
            plan_end = (
                _productivity_plan_end_v15(
                    plan
                )
            )

            if (
                plan_end
                and plan_end == end_date
            ):
                return plan

    if len(plans) == 1:
        return plans[0]

    if (
        section_index
        < len(plans)
    ):
        return plans[
            section_index
        ]

    return None


def _productivity_effective_end_v15(
    filters,
    plan_name,
    section_end,
):
    filters = frappe._dict(
        filters or {}
    )

    candidates = []

    filter_end = (
        _productivity_date_v15(
            filters.get(
                "end_date"
            )
        )
    )

    plan_end = (
        _productivity_plan_end_v15(
            plan_name
        )
    )

    for value in (
        filter_end,
        section_end,
        plan_end,
    ):
        if value:
            candidates.append(
                value
            )

    if not candidates:
        return None

    return min(
        candidates
    )


def _productivity_full_section_v15(
    filters,
    section_start,
    section_end,
):
    if (
        not section_start
        or not section_end
    ):
        return True

    filters = frappe._dict(
        filters or {}
    )

    filter_start = (
        _productivity_date_v15(
            filters.get(
                "start_date"
            )
        )
    )

    filter_end = (
        _productivity_date_v15(
            filters.get(
                "end_date"
            )
        )
    )

    # Survey values are cumulative MTD snapshots.
    #
    # Until period-difference Survey logic is deliberately added,
    # do not force cumulative Survey values into a partial
    # monthly-period report.
    if (
        filter_start
        and filter_start > section_start
    ):
        return False

    if (
        filter_end
        and filter_end < section_end
    ):
        return False

    return True


def _productivity_latest_survey_v15(
    site,
    plan_name,
    cutoff_date=None,
):
    if (
        not site
        or not plan_name
    ):
        return None

    survey_filters = {
        "location": site,
        "docstatus": 1,
        "monthly_production_plan_ref":
            plan_name,
    }

    if cutoff_date:
        survey_filters[
            "last_production_shift_start_date"
        ] = [
            "<=",
            cutoff_date,
        ]

    surveys = frappe.get_all(
        "Survey",
        filters=survey_filters,
        fields=[
            "name",
            "last_production_shift_start_date",
            "survey_datetime",
            "total_ts_bcm",
            "total_dozing_bcm",
            "total_surveyed_bcm",
            "modified",
        ],
        order_by=(
            "last_production_shift_start_date desc, "
            "survey_datetime desc, "
            "modified desc"
        ),
        limit=1,
    )

    if not surveys:
        return None

    survey_info = surveys[0]

    doc = frappe.get_doc(
        "Survey",
        survey_info.name,
    )

    ts = (
        _productivity_defaultdict_v15(
            float
        )
    )

    dozing = (
        _productivity_defaultdict_v15(
            float
        )
    )

    for row in (
        doc.get(
            "surveyed_values"
        )
        or []
    ):
        handling = (
            _productivity_re_v15.sub(
                r"[^a-z]",
                "",
                str(
                    row.get(
                        "handling_method"
                    )
                    or ""
                ).lower(),
            )
        )

        material = (
            _productivity_material_v15(
                row.get(
                    "mat_type"
                ),
                row.get(
                    "mat_type_ref"
                ),
            )
        )

        bcm = (
            _productivity_float_v15(
                row.get(
                    "bcm"
                )
            )
        )

        # Handles both:
        #   Truck and Shovel
        #   Truckand Shovel
        if (
            handling
            == "truckandshovel"
        ):
            ts[material] += bcm

        elif handling == "dozing":
            dozing[material] += bcm

    return {
        "name":
            survey_info.name,

        "last_production_shift_start_date":
            survey_info.last_production_shift_start_date,

        "survey_datetime":
            survey_info.survey_datetime,

        "Excavator":
            dict(ts),

        "ADT":
            dict(ts),

        "Dozer":
            dict(dozing),

        "total_ts_bcm":
            sum(ts.values()),

        "total_dozing_bcm":
            sum(dozing.values()),

        "total_surveyed_bcm":
            (
                sum(ts.values())
                + sum(
                    dozing.values()
                )
            ),
    }


def _productivity_output_v15(
    row,
):
    if not row:
        return 0.0

    return (
        _productivity_float_v15(
            row.get(
                "output"
            )
        )
    )


def _productivity_hd_numeric_v15(
    value,
):
    text = str(
        value or ""
    ).replace(
        ",",
        "",
    )

    values = [
        _productivity_float_v15(
            number
        )
        for number in (
            _productivity_re_v15.findall(
                r"\d+(?:\.\d+)?",
                text,
            )
        )
    ]

    if not values:
        return 0.0

    if len(values) >= 2:
        return (
            values[0]
            + values[1]
        ) / 2.0

    return values[0]


def _productivity_set_output_v15(
    row,
    value,
    category=None,
):
    value = (
        _productivity_float_v15(
            value
        )
    )

    row["output"] = value

    if (
        "adjusted_bcm"
        in row
    ):
        row[
            "adjusted_bcm"
        ] = value

    hours = (
        _productivity_float_v15(
            row.get(
                "working_hours"
            )
        )
    )

    row[
        "productivity"
    ] = (
        round(
            value / hours,
            3,
        )
        if hours > 0
        else 0.0
    )

    if category in (
        "ADT",
        "Dozer",
    ):
        hd = (
            _productivity_hd_numeric_v15(
                row.get(
                    "hauling_distance_m"
                )
            )
        )

        if hd > 0:
            row[
                "productivity_bcm_hd"
            ] = round(
                value / hd,
                3,
            )

        elif (
            "productivity_bcm_hd"
            in row
        ):
            row[
                "productivity_bcm_hd"
            ] = ""


def _productivity_section_categories_v15(
    rows,
    indices,
):
    categories = {}

    current = ""

    for index in indices:
        row = rows[index]

        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()

        if (
            label.lower()
            == "total fleet"
        ):
            current = ""
            continue

        explicit = (
            _productivity_category_v15(
                row.get(
                    "category"
                )
                or row.get(
                    "asset_category"
                )
            )
        )

        label_category = (
            _productivity_category_v15(
                label
            )
        )

        if explicit:
            current = explicit

        elif (
            label in (
                "Excavator",
                "ADT",
                "Dozer",
            )
        ):
            current = label

        elif label_category:
            # Useful for machine rows such as ADT01,
            # while remaining inside the same category.
            if (
                label_category
                in (
                    "Excavator",
                    "ADT",
                    "Dozer",
                )
            ):
                current = (
                    label_category
                )

        if current:
            categories[
                index
            ] = current

    return categories


def _productivity_align_category_v15(
    rows,
    indices,
    category,
    target_map,
    machine_view=False,
):
    target_map = dict(
        target_map
        or {}
    )

    groups = (
        _productivity_defaultdict_v15(
            list
        )
    )

    material_indices = []

    # ----------------------------------------
    # Find material rows
    # ----------------------------------------
    for index in indices:
        row = rows[index]

        if row.get(
            "is_category_total"
        ):
            continue

        if row.get(
            "is_total_fleet"
        ):
            continue

        if row.get(
            "productivity_is_machine_total"
        ):
            continue

        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()

        if not material:
            continue

        # KOSI_PRODUCTIVITY_SURVEY_MACHINE_KEY_FIX_V16
        # KOSI_PRODUCTIVITY_SURVEY_MACHINE_LABEL_FIX_V17
        #
        # Summary Per Machine material rows identify the machine
        # through label in the current report structure.
        # Keep support for machine / asset_name as well.
        if machine_view:
            machine_name = str(
                row.get("machine")
                or row.get("asset_name")
                or row.get("label")
                or ""
            ).strip()

            if not machine_name:
                continue

        key = (
            _productivity_material_v15(
                material,
                row.get(
                    "label"
                ),
            )
        )

        groups[
            key
        ].append(
            index
        )

        material_indices.append(
            index
        )

    # ----------------------------------------
    # Align each material to Survey
    # ----------------------------------------
    for material, group in groups.items():
        target = (
            _productivity_float_v15(
                target_map.get(
                    material,
                    0
                )
            )
        )

        current_values = [
            max(
                _productivity_output_v15(
                    rows[index]
                ),
                0.0,
            )
            for index in group
        ]

        current_total = sum(
            current_values
        )

        new_values = []

        if target <= 0:
            new_values = [
                0.0
                for _ in group
            ]

        elif current_total > 0:
            allocated = 0.0

            for position, current in enumerate(
                current_values
            ):
                if (
                    position
                    == len(group) - 1
                ):
                    new_value = (
                        target
                        - allocated
                    )

                else:
                    new_value = (
                        target
                        * current
                        / current_total
                    )

                    allocated += (
                        new_value
                    )

                new_values.append(
                    new_value
                )

        else:
            new_values = [
                0.0
                for _ in group
            ]

            if group:
                new_values[0] = (
                    target
                )

        for index, new_value in zip(
            group,
            new_values,
        ):
            _productivity_set_output_v15(
                rows[index],
                new_value,
                category,
            )

    # ----------------------------------------
    # Machine totals in Summary Per Machine
    # ----------------------------------------
    if machine_view:
        machine_totals = (
            _productivity_defaultdict_v15(
                float
            )
        )

        for index in material_indices:
            row = rows[index]

            # KOSI_PRODUCTIVITY_SURVEY_MACHINE_TOTAL_FIX_V18
            #
            # Summary Per Machine material rows currently use
            # label as the machine identifier.
            machine = str(
                row.get(
                    "machine"
                )
                or row.get(
                    "asset_name"
                )
                or row.get(
                    "label"
                )
                or ""
            ).strip()

            if not machine:
                continue

            machine_totals[
                machine
            ] += (
                _productivity_output_v15(
                    row
                )
            )

        for index in indices:
            row = rows[index]

            if not row.get(
                "productivity_is_machine_total"
            ):
                continue

            machine = str(
                row.get(
                    "machine"
                )
                or row.get(
                    "asset_name"
                )
                or row.get(
                    "label"
                )
                or ""
            ).strip()

            if machine in machine_totals:
                _productivity_set_output_v15(
                    row,
                    machine_totals[
                        machine
                    ],
                    category,
                )

    # ----------------------------------------
    # Category total
    # ----------------------------------------
    target_total = sum(
        _productivity_float_v15(
            value
        )
        for value in (
            target_map.values()
        )
    )

    category_total_indices = []

    for index in indices:
        row = rows[index]

        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()

        if (
            label == category
            or (
                row.get(
                    "is_category_total"
                )
                and (
                    _productivity_category_v15(
                        row.get(
                            "category"
                        )
                        or label
                    )
                    == category
                )
            )
        ):
            category_total_indices.append(
                index
            )

    for index in (
        category_total_indices
    ):
        _productivity_set_output_v15(
            rows[index],
            target_total,
            category,
        )

    return {
        "output":
            target_total,

        "category_total_indices":
            category_total_indices,
    }


def _productivity_apply_survey_v15(
    result,
    filters,
):
    if not result:
        return result

    filters = frappe._dict(
        filters or {}
    )

    bcm_basis = str(
        filters.get(
            "bcm_basis"
        )
        or ""
    ).strip().lower()

    # Tallies remain completely untouched.
    if not bcm_basis.startswith(
        "actual"
    ):
        return result

    plans = (
        _productivity_selected_plans_v15(
            filters
        )
    )

    site = str(
        filters.get(
            "site"
        )
        or filters.get(
            "location"
        )
        or ""
    ).strip()

    if (
        not plans
        or not site
    ):
        return result

    parts = list(
        result
    )

    if len(parts) < 2:
        return result

    rows = list(
        parts[1]
        or []
    )

    if not rows:
        return result

    # ----------------------------------------
    # Build monthly sections
    # ----------------------------------------
    header_positions = []

    for index, row in enumerate(
        rows
    ):
        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()

        if (
            row.get(
                "is_monthly_plan_header"
            )
            or label.upper().startswith(
                "MONTHLY PRODUCTION:"
            )
        ):
            header_positions.append(
                index
            )

    sections = []

    if header_positions:
        for position, start_index in enumerate(
            header_positions
        ):
            end_index = (
                header_positions[
                    position + 1
                ]
                if (
                    position + 1
                    < len(
                        header_positions
                    )
                )
                else len(rows)
            )

            sections.append(
                (
                    position,
                    list(
                        range(
                            start_index,
                            end_index,
                        )
                    ),
                    str(
                        rows[
                            start_index
                        ].get(
                            "label"
                        )
                        or ""
                    ),
                )
            )

    else:
        sections.append(
            (
                0,
                list(
                    range(
                        len(rows)
                    )
                ),
                "",
            )
        )

    summary_accumulator = {
        "Excavator": {
            "output": 0.0,
            "hours": 0.0,
        },
        "Dozer": {
            "output": 0.0,
            "hours": 0.0,
        },
    }

    summary_view = str(
        filters.get(
            "summary_view"
        )
        or ""
    ).strip().lower()

    machine_view = (
        "summary per machine"
        in summary_view
    )

    for (
        section_index,
        indices,
        header_label,
    ) in sections:

        section_start, section_end = (
            _productivity_header_range_v15(
                header_label
            )
        )

        plan_name = (
            _productivity_plan_for_section_v15(
                header_label,
                plans,
                section_index,
            )
        )

        if not plan_name:
            continue

        # Do not use cumulative MTD Survey values for a
        # deliberately partial monthly section.
        if not (
            _productivity_full_section_v15(
                filters,
                section_start,
                section_end,
            )
        ):
            continue

        cutoff = (
            _productivity_effective_end_v15(
                filters,
                plan_name,
                section_end,
            )
        )

        survey = (
            _productivity_latest_survey_v15(
                site,
                plan_name,
                cutoff,
            )
        )

        if not survey:
            continue

        # Store invisible diagnostic metadata on header.
        if indices:
            rows[
                indices[0]
            ][
                "productivity_survey_source"
            ] = survey[
                "name"
            ]

        category_lookup = (
            _productivity_section_categories_v15(
                rows,
                indices,
            )
        )

        category_indices = {
            "Excavator": [],
            "ADT": [],
            "Dozer": [],
        }

        for index in indices:
            category = (
                category_lookup.get(
                    index
                )
            )

            if category in (
                category_indices
            ):
                category_indices[
                    category
                ].append(
                    index
                )

        category_results = {}

        for category in (
            "Excavator",
            "ADT",
            "Dozer",
        ):
            category_results[
                category
            ] = (
                _productivity_align_category_v15(
                    rows,
                    category_indices[
                        category
                    ],
                    category,
                    survey.get(
                        category
                    )
                    or {},
                    machine_view,
                )
            )

        # ------------------------------------
        # Total Fleet = Excavator + Dozer
        # ADT is duplicate haul output.
        # ------------------------------------
        fleet_output = (
            category_results[
                "Excavator"
            ][
                "output"
            ]
            + category_results[
                "Dozer"
            ][
                "output"
            ]
        )

        for index in indices:
            row = rows[index]

            label = str(
                row.get(
                    "label"
                )
                or ""
            ).strip()

            if (
                row.get(
                    "is_total_fleet"
                )
                or label.lower()
                == "total fleet"
            ):
                _productivity_set_output_v15(
                    row,
                    fleet_output,
                    None,
                )

        # ------------------------------------
        # Summary cards
        # ------------------------------------
        for category in (
            "Excavator",
            "Dozer",
        ):
            total_indices = (
                category_results[
                    category
                ][
                    "category_total_indices"
                ]
            )

            if not total_indices:
                continue

            row = rows[
                total_indices[0]
            ]

            summary_accumulator[
                category
            ][
                "output"
            ] += (
                _productivity_output_v15(
                    row
                )
            )

            summary_accumulator[
                category
            ][
                "hours"
            ] += (
                _productivity_float_v15(
                    row.get(
                        "working_hours"
                    )
                )
            )

    parts[1] = rows

    # Update report summary cards so they follow the
    # Survey-aligned category totals.
    if (
        len(parts) > 4
        and isinstance(
            parts[4],
            list,
        )
    ):
        for item in parts[4]:
            label = str(
                item.get(
                    "label"
                )
                or ""
            ).lower()

            if (
                "truck + shovel productivity"
                in label
            ):
                values = (
                    summary_accumulator[
                        "Excavator"
                    ]
                )

                if values[
                    "hours"
                ] > 0:
                    item[
                        "value"
                    ] = str(
                        int(
                            round(
                                values[
                                    "output"
                                ]
                                / values[
                                    "hours"
                                ]
                            )
                        )
                    )

            elif (
                "dozing productivity"
                in label
            ):
                values = (
                    summary_accumulator[
                        "Dozer"
                    ]
                )

                if values[
                    "hours"
                ] > 0:
                    item[
                        "value"
                    ] = str(
                        int(
                            round(
                                values[
                                    "output"
                                ]
                                / values[
                                    "hours"
                                ]
                            )
                        )
                    )

    if isinstance(
        result,
        tuple,
    ):
        return tuple(
            parts
        )

    return parts


def execute(filters=None):
    result = (
        _productivity_execute_before_survey_actual_v15(
            filters
        )
    )

    return (
        _productivity_apply_survey_v15(
            result,
            filters,
        )
    )


# END KOSI_PRODUCTIVITY_SURVEY_ACTUAL_V15


# ============================================================
# KOSI_PRODUCTIVITY_COAL_METRIC_TONNES_V19
#
# OPTION 1:
#
# Coal DETAIL rows use Survey Metric Tonnes.
#
# Everything else remains on the existing BCM basis:
#
# - Softs / Hards = BCM
# - Category totals = BCM
# - Machine totals = BCM
# - Total Fleet = BCM
# - Summary cards = BCM based
# - BCM/HD = BCM based
# - Tallies = unchanged
#
# Coal Productivity detail rows become Tonnes / Hour.
#
# Survey documents remain cumulative MTD snapshots, therefore
# the same Survey-selection rules as V15 are used.
# ============================================================

import re as _productivity_re_v19
from collections import defaultdict as _productivity_defaultdict_v19


_productivity_execute_before_coal_tonnes_v19 = execute


def _productivity_survey_coal_tonnes_v19(
    survey_name,
):
    result = {
        "Excavator": 0.0,
        "ADT": 0.0,
        "Dozer": 0.0,
    }

    if not survey_name:
        return result

    doc = frappe.get_doc(
        "Survey",
        survey_name,
    )

    truck_tonnes = 0.0
    dozer_tonnes = 0.0

    for row in (
        doc.get(
            "surveyed_values"
        )
        or []
    ):
        material = (
            _productivity_material_v15(
                row.get(
                    "mat_type"
                ),
                row.get(
                    "mat_type_ref"
                ),
            )
        )

        if material != "Coal":
            continue

        handling = (
            _productivity_re_v19.sub(
                r"[^a-z]",
                "",
                str(
                    row.get(
                        "handling_method"
                    )
                    or ""
                ).lower(),
            )
        )

        tonnes = (
            _productivity_float_v15(
                row.get(
                    "metric_tonnes"
                )
            )
        )

        if (
            handling
            == "truckandshovel"
        ):
            truck_tonnes += tonnes

        elif handling == "dozing":
            dozer_tonnes += tonnes

    result[
        "Excavator"
    ] = truck_tonnes

    result[
        "ADT"
    ] = truck_tonnes

    result[
        "Dozer"
    ] = dozer_tonnes

    return result


def _productivity_set_coal_tonnes_v19(
    row,
    tonnes,
):
    tonnes = (
        _productivity_float_v15(
            tonnes
        )
    )

    # Keep the Survey-aligned BCM value as internal metadata.
    # Category / machine / fleet totals have already been
    # calculated by V15-V18 before V19 changes the display row.
    row[
        "productivity_coal_bcm_v19"
    ] = (
        _productivity_float_v15(
            row.get(
                "output"
            )
        )
    )

    # IMPORTANT:
    # productivity_bcm_hd has already been calculated using BCM.
    # Do not recalculate it from tonnes.
    row[
        "output"
    ] = tonnes

    if (
        "adjusted_bcm"
        in row
    ):
        row[
            "adjusted_bcm"
        ] = tonnes

    hours = (
        _productivity_float_v15(
            row.get(
                "working_hours"
            )
        )
    )

    row[
        "productivity"
    ] = (
        round(
            tonnes / hours,
            3,
        )
        if hours > 0
        else 0.0
    )

    row[
        "productivity_output_unit_v19"
    ] = "Metric Tonnes"

    row[
        "productivity_rate_unit_v19"
    ] = "Tonnes/Hr"


def _productivity_actual_column_labels_v19(
    columns,
):
    columns = [
        dict(column)
        if hasattr(
            column,
            "get",
        )
        else column

        for column in (
            columns or []
        )
    ]

    for column in columns:

        if not hasattr(
            column,
            "get",
        ):
            continue

        fieldname = str(
            column.get(
                "fieldname"
            )
            or ""
        ).strip()

        if fieldname in (
            "output",
            "adjusted_bcm",
        ):
            column[
                "label"
            ] = (
                "Output "
                "(BCM / Coal Tonnes)"
            )

        elif (
            fieldname
            == "productivity"
        ):
            column[
                "label"
            ] = (
                "Productivity "
                "(BCM/Hr / Coal t/Hr)"
            )

    return columns


def _productivity_apply_coal_tonnes_v19(
    result,
    filters,
):
    if not result:
        return result

    filters = frappe._dict(
        filters or {}
    )

    bcm_basis = str(
        filters.get(
            "bcm_basis"
        )
        or ""
    ).strip().lower()

    # OPTION 1 applies to Actual only.
    # Tallies must remain completely untouched.
    if not bcm_basis.startswith(
        "actual"
    ):
        return result

    site = str(
        filters.get(
            "site"
        )
        or filters.get(
            "location"
        )
        or ""
    ).strip()

    plans = (
        _productivity_selected_plans_v15(
            filters
        )
    )

    if (
        not site
        or not plans
    ):
        return result

    parts = list(
        result
    )

    if len(parts) < 2:
        return result

    columns = (
        parts[0]
        or []
    )

    rows = list(
        parts[1]
        or []
    )

    if not rows:
        return result

    # Clarify mixed-unit display only for Actual BCM view.
    columns = (
        _productivity_actual_column_labels_v19(
            columns
        )
    )

    # --------------------------------------------------------
    # MONTHLY SECTIONS
    # --------------------------------------------------------

    header_positions = []

    for index, row in enumerate(
        rows
    ):
        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()

        if (
            row.get(
                "is_monthly_plan_header"
            )
            or label.upper().startswith(
                "MONTHLY PRODUCTION:"
            )
        ):
            header_positions.append(
                index
            )

    sections = []

    if header_positions:

        for position, start_index in enumerate(
            header_positions
        ):
            end_index = (
                header_positions[
                    position + 1
                ]
                if (
                    position + 1
                    < len(
                        header_positions
                    )
                )
                else len(rows)
            )

            sections.append(
                (
                    position,
                    list(
                        range(
                            start_index,
                            end_index,
                        )
                    ),
                    str(
                        rows[
                            start_index
                        ].get(
                            "label"
                        )
                        or ""
                    ),
                )
            )

    else:
        sections.append(
            (
                0,
                list(
                    range(
                        len(rows)
                    )
                ),
                "",
            )
        )

    # --------------------------------------------------------
    # PROCESS EACH MONTHLY PRODUCTION SECTION
    # --------------------------------------------------------

    for (
        section_index,
        indices,
        header_label,
    ) in sections:

        section_start, section_end = (
            _productivity_header_range_v15(
                header_label
            )
        )

        plan_name = (
            _productivity_plan_for_section_v15(
                header_label,
                plans,
                section_index,
            )
        )

        if not plan_name:
            continue

        # Same cumulative-Survey safety rule as V15.
        if not (
            _productivity_full_section_v15(
                filters,
                section_start,
                section_end,
            )
        ):
            continue

        cutoff = (
            _productivity_effective_end_v15(
                filters,
                plan_name,
                section_end,
            )
        )

        survey = (
            _productivity_latest_survey_v15(
                site,
                plan_name,
                cutoff,
            )
        )

        if not survey:
            continue

        coal_tonnes = (
            _productivity_survey_coal_tonnes_v19(
                survey.get(
                    "name"
                )
            )
        )

        category_lookup = (
            _productivity_section_categories_v15(
                rows,
                indices,
            )
        )

        # ----------------------------------------------------
        # For Summary Per Machine there can be many Coal rows.
        #
        # Distribute Survey Coal Tonnes by each machine's
        # Survey-aligned Coal BCM share.
        #
        # For Hours and Material there is normally one Coal row,
        # therefore it receives the full Survey tonnes total.
        # ----------------------------------------------------

        for category in (
            "Excavator",
            "ADT",
            "Dozer",
        ):

            target_tonnes = (
                _productivity_float_v15(
                    coal_tonnes.get(
                        category,
                        0
                    )
                )
            )

            coal_rows = []

            for index in indices:

                if (
                    category_lookup.get(
                        index
                    )
                    != category
                ):
                    continue

                row = rows[
                    index
                ]

                if row.get(
                    "is_category_total"
                ):
                    continue

                if row.get(
                    "is_total_fleet"
                ):
                    continue

                if row.get(
                    "productivity_is_machine_total"
                ):
                    continue

                material = (
                    _productivity_material_v15(
                        row.get(
                            "material"
                        ),
                        row.get(
                            "label"
                        ),
                    )
                )

                if material != "Coal":
                    continue

                coal_rows.append(
                    index
                )

            if not coal_rows:
                continue

            bcm_values = [
                max(
                    _productivity_float_v15(
                        rows[
                            index
                        ].get(
                            "output"
                        )
                    ),
                    0.0,
                )

                for index in coal_rows
            ]

            total_coal_bcm = sum(
                bcm_values
            )

            allocated = 0.0

            for position, index in enumerate(
                coal_rows
            ):

                if (
                    position
                    == len(
                        coal_rows
                    ) - 1
                ):
                    tonnes = (
                        target_tonnes
                        - allocated
                    )

                elif (
                    total_coal_bcm > 0
                ):
                    tonnes = (
                        target_tonnes
                        * bcm_values[
                            position
                        ]
                        / total_coal_bcm
                    )

                    allocated += (
                        tonnes
                    )

                else:
                    tonnes = 0.0

                _productivity_set_coal_tonnes_v19(
                    rows[
                        index
                    ],
                    tonnes,
                )

            # Diagnostic metadata on monthly header.
            if indices:
                header_row = rows[
                    indices[0]
                ]

                header_row[
                    "productivity_coal_tonnes_survey_v19"
                ] = survey.get(
                    "name"
                )

                header_row[
                    f"productivity_{category.lower()}_coal_tonnes_v19"
                ] = target_tonnes

    parts[0] = columns
    parts[1] = rows

    if isinstance(
        result,
        tuple,
    ):
        return tuple(
            parts
        )

    return parts


def execute(filters=None):
    result = (
        _productivity_execute_before_coal_tonnes_v19(
            filters
        )
    )

    return (
        _productivity_apply_coal_tonnes_v19(
            result,
            filters,
        )
    )


# END KOSI_PRODUCTIVITY_COAL_METRIC_TONNES_V19


# ============================================================
# KOSI_PRODUCTIVITY_COAL_REF_BREAKDOWN_V20
#
# Hours and Material / Actual BCM only.
#
# Keep the existing Coal TOTAL row.
#
# Immediately below Coal show the Survey Material Type Ref
# breakdown, for example:
#
#     Coal                       240,301.5 t
#         2COAL - LOAD & HAUL      xxxxx t
#         4LCOAL - LOAD & HAUL     xxxxx t
#
# Child rows:
# - use Survey metric_tonnes
# - preserve exact mat_type_ref wording
# - have no allocated Working Hours
# - have no calculated Productivity
#
# This avoids inventing per-reference working hours.
#
# Excavator / ADT / Dozer / Total Fleet totals remain unchanged.
# Summary Per Machine remains unchanged.
# Tallies remains unchanged.
# ============================================================

import re as _productivity_re_v20


_productivity_execute_before_coal_ref_breakdown_v20 = execute


def _productivity_survey_coal_refs_v20(
    survey_name,
):
    result = {
        "Truck and Shovel": {},
        "Dozing": {},
    }

    if not survey_name:
        return result

    doc = frappe.get_doc(
        "Survey",
        survey_name,
    )

    for row in (
        doc.get(
            "surveyed_values"
        )
        or []
    ):

        material = (
            _productivity_material_v15(
                row.get(
                    "mat_type"
                ),
                row.get(
                    "mat_type_ref"
                ),
            )
        )

        if material != "Coal":
            continue

        tonnes = (
            _productivity_float_v15(
                row.get(
                    "metric_tonnes"
                )
            )
        )

        if tonnes == 0:
            continue

        material_ref = str(
            row.get(
                "mat_type_ref"
            )
            or ""
        ).strip()

        if not material_ref:
            material_ref = (
                "COAL - UNSPECIFIED"
            )

        handling = (
            _productivity_re_v20.sub(
                r"[^a-z]",
                "",
                str(
                    row.get(
                        "handling_method"
                    )
                    or ""
                ).lower(),
            )
        )

        if (
            handling
            == "truckandshovel"
        ):
            handling_key = (
                "Truck and Shovel"
            )

        elif handling == "dozing":
            handling_key = "Dozing"

        else:
            continue

        result[
            handling_key
        ][
            material_ref
        ] = (
            result[
                handling_key
            ].get(
                material_ref,
                0.0,
            )
            + tonnes
        )

    return result


def _productivity_coal_child_v20(
    parent_row,
    material_ref,
    tonnes,
):
    child = {
        "label":
            material_ref,

        "working_hours":
            "",

        "output":
            tonnes,

        "productivity":
            "",

        "productivity_bcm_hd":
            "",

        "material":
            material_ref,

        "from_area":
            "",

        "to_area":
            "",

        "hauling_distance_m":
            "",

        "indent":
            (
                int(
                    parent_row.get(
                        "indent"
                    )
                    or 0
                )
                + 1
            ),

        "style":
            "color:#555;",

        "productivity_coal_ref_breakdown_v20":
            1,

        "productivity_coal_parent_v20":
            "Coal",

        "productivity_output_unit_v19":
            "Metric Tonnes",
    }

    if (
        "adjusted_bcm"
        in parent_row
    ):
        child[
            "adjusted_bcm"
        ] = tonnes

    return child


def _productivity_apply_coal_ref_breakdown_v20(
    result,
    filters,
):
    if not result:
        return result

    filters = frappe._dict(
        filters or {}
    )

    basis = str(
        filters.get(
            "bcm_basis"
        )
        or ""
    ).strip().lower()

    if not basis.startswith(
        "actual"
    ):
        return result

    view = str(
        filters.get(
            "summary_view"
        )
        or ""
    ).strip().lower()

    # Survey has no machine link for the individual
    # coal reference rows, therefore V20 is intentionally
    # limited to Hours and Material.
    if view != "hours and material":
        return result

    parts = list(
        result
    )

    if len(parts) < 2:
        return result

    rows = list(
        parts[1]
        or []
    )

    if not rows:
        return result

    new_rows = []

    current_category = ""
    current_survey_name = ""
    breakdown = {
        "Truck and Shovel": {},
        "Dozing": {},
    }

    for row in rows:

        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()

        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()

        # V19 stores the exact Survey source on the
        # monthly production header.
        survey_name = str(
            row.get(
                "productivity_coal_tonnes_survey_v19"
            )
            or ""
        ).strip()

        if survey_name:

            current_survey_name = (
                survey_name
            )

            breakdown = (
                _productivity_survey_coal_refs_v20(
                    current_survey_name
                )
            )

        if label in (
            "Excavator",
            "ADT",
            "Dozer",
        ):
            current_category = label

        new_rows.append(
            row
        )

        if material != "Coal":
            continue

        if current_category in (
            "Excavator",
            "ADT",
        ):
            refs = breakdown.get(
                "Truck and Shovel",
                {},
            )

        elif current_category == "Dozer":
            refs = breakdown.get(
                "Dozing",
                {},
            )

        else:
            refs = {}

        for (
            material_ref,
            tonnes,
        ) in refs.items():

            new_rows.append(
                _productivity_coal_child_v20(
                    row,
                    material_ref,
                    tonnes,
                )
            )

    parts[1] = new_rows

    if isinstance(
        result,
        tuple,
    ):
        return tuple(
            parts
        )

    return parts


def execute(filters=None):

    result = (
        _productivity_execute_before_coal_ref_breakdown_v20(
            filters
        )
    )

    return (
        _productivity_apply_coal_ref_breakdown_v20(
            result,
            filters,
        )
    )


# END KOSI_PRODUCTIVITY_COAL_REF_BREAKDOWN_V20


# ============================================================
# KOSI_PRODUCTIVITY_SURVEY_ROW_BREAKDOWN_V21
#
# Actual BCMs / Hours and Material only.
#
# Follow the Survey child table row-by-row.
#
# IMPORTANT:
# - DO NOT combine duplicate Material Type Ref rows.
# - Preserve Survey row order within each material.
#
# Example:
#
# Coal TOTAL
#   2COAL - LOAD & HAUL
#   4LCOAL - LOAD & HAUL
#   4LCOAL - LOAD & HAUL
#
# Softs / Hards use Survey BCM.
# Coal uses Survey Metric Tonnes.
#
# Truck and Shovel breakdown is shown under:
# - Excavator
# - ADT
#
# Dozing breakdown is shown under:
# - Dozer
#
# Child rows have no allocated Working Hours or Productivity.
#
# V20 aggregated child rows are removed and replaced with these
# exact Survey rows.
#
# Summary Per Machine and Tallies remain unchanged.
# ============================================================

import re as _productivity_re_v21


_productivity_execute_before_survey_row_breakdown_v21 = execute


def _productivity_survey_rows_v21(
    survey_name,
):
    rows = []

    if not survey_name:
        return rows

    doc = frappe.get_doc(
        "Survey",
        survey_name,
    )

    for survey_row in (
        doc.get(
            "surveyed_values"
        )
        or []
    ):

        material = (
            _productivity_material_v15(
                survey_row.get(
                    "mat_type"
                ),
                survey_row.get(
                    "mat_type_ref"
                ),
            )
        )

        if material not in (
            "Softs",
            "Hards",
            "Coal",
        ):
            continue

        handling_raw = str(
            survey_row.get(
                "handling_method"
            )
            or ""
        ).strip()

        handling_clean = (
            _productivity_re_v21.sub(
                r"[^a-z]",
                "",
                handling_raw.lower(),
            )
        )

        if (
            handling_clean
            == "truckandshovel"
        ):
            handling = (
                "Truck and Shovel"
            )

        elif (
            handling_clean
            == "dozing"
        ):
            handling = "Dozing"

        else:
            continue

        material_ref = str(
            survey_row.get(
                "mat_type_ref"
            )
            or ""
        ).strip()

        if not material_ref:
            material_ref = (
                f"{material} - UNSPECIFIED"
            )

        bcm = (
            _productivity_float_v15(
                survey_row.get(
                    "bcm"
                )
            )
        )

        tonnes = (
            _productivity_float_v15(
                survey_row.get(
                    "metric_tonnes"
                )
            )
        )

        rows.append({
            "idx":
                survey_row.get(
                    "idx"
                ),

            "material":
                material,

            "material_ref":
                material_ref,

            "handling":
                handling,

            "bcm":
                bcm,

            "metric_tonnes":
                tonnes,

            "rd":
                _productivity_float_v15(
                    survey_row.get(
                        "rd"
                    )
                ),
        })

    return rows


def _productivity_survey_child_v21(
    parent_row,
    source_row,
):
    material = str(
        source_row.get(
            "material"
        )
        or ""
    ).strip()

    if material == "Coal":
        output = (
            _productivity_float_v15(
                source_row.get(
                    "metric_tonnes"
                )
            )
        )

        unit = "Metric Tonnes"

    else:
        output = (
            _productivity_float_v15(
                source_row.get(
                    "bcm"
                )
            )
        )

        unit = "BCM"

    child = {
        "label":
            source_row.get(
                "material_ref"
            ),

        "working_hours":
            "",

        "output":
            output,

        "productivity":
            "",

        "productivity_bcm_hd":
            "",

        "material":
            source_row.get(
                "material_ref"
            ),

        "from_area":
            "",

        "to_area":
            "",

        "hauling_distance_m":
            "",

        "indent":
            (
                int(
                    parent_row.get(
                        "indent"
                    )
                    or 0
                )
                + 1
            ),

        "style":
            "color:#555;",

        "productivity_survey_row_breakdown_v21":
            1,

        "productivity_survey_row_idx_v21":
            source_row.get(
                "idx"
            ),

        "productivity_parent_material_v21":
            material,

        "productivity_survey_handling_v21":
            source_row.get(
                "handling"
            ),

        "productivity_output_unit_v19":
            unit,
    }

    if (
        "adjusted_bcm"
        in parent_row
    ):
        child[
            "adjusted_bcm"
        ] = output

    return child


def _productivity_apply_survey_rows_v21(
    result,
    filters,
):
    if not result:
        return result

    filters = frappe._dict(
        filters or {}
    )

    basis = str(
        filters.get(
            "bcm_basis"
        )
        or ""
    ).strip().lower()

    if not basis.startswith(
        "actual"
    ):
        return result

    view = str(
        filters.get(
            "summary_view"
        )
        or ""
    ).strip().lower()

    if view != "hours and material":
        return result

    parts = list(
        result
    )

    if len(parts) < 2:
        return result

    original_rows = list(
        parts[1]
        or []
    )

    if not original_rows:
        return result

    new_rows = []

    current_category = ""
    current_survey = ""
    survey_rows = []

    for row in original_rows:

        # ----------------------------------------------------
        # Remove V20 aggregated coal child rows.
        # V21 replaces them with exact Survey rows.
        # ----------------------------------------------------

        if row.get(
            "productivity_coal_ref_breakdown_v20"
        ):
            continue

        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()

        material_raw = str(
            row.get(
                "material"
            )
            or ""
        ).strip()

        survey_name = str(
            row.get(
                "productivity_coal_tonnes_survey_v19"
            )
            or ""
        ).strip()

        if survey_name:

            current_survey = (
                survey_name
            )

            survey_rows = (
                _productivity_survey_rows_v21(
                    current_survey
                )
            )

        if label in (
            "Excavator",
            "ADT",
            "Dozer",
        ):
            current_category = label

        new_rows.append(
            row
        )

        if not material_raw:
            continue

        if row.get(
            "productivity_is_machine_total"
        ):
            continue

        material = (
            _productivity_material_v15(
                material_raw,
                label,
            )
        )

        if material not in (
            "Softs",
            "Hards",
            "Coal",
        ):
            continue

        if current_category in (
            "Excavator",
            "ADT",
        ):
            required_handling = (
                "Truck and Shovel"
            )

        elif (
            current_category
            == "Dozer"
        ):
            required_handling = (
                "Dozing"
            )

        else:
            continue

        # ----------------------------------------------------
        # IMPORTANT:
        # Iterate the original Survey rows directly.
        #
        # Do not group by Material Type Ref.
        # Do not deduplicate.
        #
        # Duplicate 4LCOAL rows therefore remain separate.
        # ----------------------------------------------------

        for source_row in survey_rows:

            if (
                source_row.get(
                    "material"
                )
                != material
            ):
                continue

            if (
                source_row.get(
                    "handling"
                )
                != required_handling
            ):
                continue

            new_rows.append(
                _productivity_survey_child_v21(
                    row,
                    source_row,
                )
            )

    parts[1] = new_rows

    if isinstance(
        result,
        tuple,
    ):
        return tuple(
            parts
        )

    return parts


def execute(filters=None):

    result = (
        _productivity_execute_before_survey_row_breakdown_v21(
            filters
        )
    )

    return (
        _productivity_apply_survey_rows_v21(
            result,
            filters,
        )
    )


# END KOSI_PRODUCTIVITY_SURVEY_ROW_BREAKDOWN_V21


# ============================================================
# KOSI_PRODUCTIVITY_SURVEY_HOURS_AREA_V22
#
# Hours and Material / Actual BCMs only.
#
# V21 creates exact Survey rows.
#
# V22:
#
# 1. Keeps parent material total intact:
#       Coal / Hards / Softs / Dozer material total
#
# 2. Splits parent Working Hours to exact Survey child rows
#    according to each child's output share.
#
#       Coal:
#           Metric Tonnes share
#
#       Non-Coal:
#           BCM share
#
# 3. Recalculates child Productivity.
#
# 4. Removes From Area / To Area / Hauling Distance from the
#    parent material-total row.
#
# 5. Moves the existing automatic material-level From Area
#    onto each exact Survey child row.
#
# IMPORTANT:
# Truck Loads currently do not identify separate Survey
# mat_type_ref rows, so V22 does NOT invent different source
# areas for duplicate Survey rows.
#
# It uses the existing material-area map already used by
# Productivity.
#
# 6. Existing parent To Area / Hauling Distance values are
#    carried down to the children so existing report values
#    are not lost visually.
#
# Summary Per Machine and Tallies are untouched.
# ============================================================


_productivity_execute_before_survey_hours_area_v22 = execute


def _productivity_parent_from_area_v22(
    area_maps,
    category,
    parent_row,
):
    category = str(
        category or ""
    ).strip()

    raw_material = str(
        parent_row.get(
            "material"
        )
        or parent_row.get(
            "label"
        )
        or ""
    ).strip()

    if not (
        category
        and raw_material
    ):
        return ""

    category_map = (
        area_maps.get(
            category,
            {}
        )
        or {}
    )

    material_map = (
        category_map.get(
            "material",
            {}
        )
        or {}
    )

    values = (
        material_map.get(
            raw_material
        )
        or set()
    )

    # Dozer Survey Hards can sit under a production material
    # such as "2 - Midburden", therefore always try the visible
    # parent material first.
    if not values:

        canonical = (
            _productivity_material_v15(
                raw_material,
                parent_row.get(
                    "label"
                ),
            )
        )

        values = (
            material_map.get(
                canonical
            )
            or set()
        )

    if not values:
        return str(
            parent_row.get(
                "from_area"
            )
            or ""
        ).strip()

    try:
        return (
            _productivity_area_text(
                values
            )
        )

    except Exception:

        return " / ".join(
            sorted(
                {
                    str(value).strip()
                    for value in values
                    if str(
                        value
                    ).strip()
                }
            )
        )


def _productivity_split_child_hours_v22(
    parent_hours,
    children,
):
    parent_hours = (
        _productivity_float_v15(
            parent_hours
        )
    )

    outputs = [
        _productivity_float_v15(
            child.get(
                "output"
            )
        )
        for child in children
    ]

    total_output = sum(
        outputs
    )

    if (
        parent_hours <= 0
        or total_output <= 0
    ):

        return [
            0.0
            for child in children
        ]

    result = []

    allocated = 0.0

    for index, output in enumerate(
        outputs
    ):

        if index == (
            len(outputs) - 1
        ):

            hours = round(
                parent_hours
                - allocated,
                3,
            )

        else:

            hours = round(
                parent_hours
                * (
                    output
                    / total_output
                ),
                3,
            )

            allocated += hours

        result.append(
            hours
        )

    return result


def _productivity_apply_survey_hours_area_v22(
    result,
    filters,
):
    if not result:
        return result

    filters = frappe._dict(
        filters or {}
    )

    basis = str(
        filters.get(
            "bcm_basis"
        )
        or ""
    ).strip().lower()

    if not basis.startswith(
        "actual"
    ):
        return result

    view = str(
        filters.get(
            "summary_view"
        )
        or ""
    ).strip().lower()

    if view != "hours and material":
        return result

    parts = list(
        result
    )

    if len(parts) < 2:
        return result

    rows = list(
        parts[1]
        or []
    )

    if not rows:
        return result

    site = str(
        filters.get(
            "site"
        )
        or filters.get(
            "location"
        )
        or ""
    ).strip()

    start_date = (
        filters.get(
            "start_date"
        )
        or filters.get(
            "from_date"
        )
    )

    end_date = (
        filters.get(
            "end_date"
        )
        or filters.get(
            "to_date"
        )
    )

    try:

        area_maps = (
            _productivity_build_area_maps(
                site,
                start_date,
                end_date,
            )
            or {}
        )

    except Exception:

        area_maps = {}

    output_rows = []

    current_category = ""

    index = 0

    while index < len(
        rows
    ):

        original = rows[
            index
        ]

        if not hasattr(
            original,
            "get",
        ):

            output_rows.append(
                original
            )

            index += 1

            continue

        row = dict(
            original
        )

        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()

        if label in (
            "Excavator",
            "ADT",
            "Dozer",
        ):

            current_category = label

        # V21 child should only be processed as part of
        # its immediately preceding parent block.
        if row.get(
            "productivity_survey_row_breakdown_v21"
        ):

            output_rows.append(
                row
            )

            index += 1

            continue

        child_start = (
            index + 1
        )

        child_end = (
            child_start
        )

        children = []

        while (
            child_end
            < len(rows)
        ):

            candidate = rows[
                child_end
            ]

            if not (
                hasattr(
                    candidate,
                    "get",
                )
                and candidate.get(
                    "productivity_survey_row_breakdown_v21"
                )
            ):

                break

            children.append(
                dict(
                    candidate
                )
            )

            child_end += 1

        # No V21 Survey children after this row.
        if not children:

            output_rows.append(
                row
            )

            index += 1

            continue

        parent_hours = (
            _productivity_float_v15(
                row.get(
                    "working_hours"
                )
            )
        )

        split_hours = (
            _productivity_split_child_hours_v22(
                parent_hours,
                children,
            )
        )

        child_from_area = (
            _productivity_parent_from_area_v22(
                area_maps,
                current_category,
                row,
            )
        )

        parent_from_area = str(
            row.get(
                "from_area"
            )
            or ""
        ).strip()

        parent_to_area = str(
            row.get(
                "to_area"
            )
            or ""
        ).strip()

        parent_hauling_distance = str(
            row.get(
                "hauling_distance_m"
            )
            or ""
        ).strip()

        # If automatic lookup found nothing, never throw away
        # an already-populated parent From Area.
        if not child_from_area:

            child_from_area = (
                parent_from_area
            )

        # ----------------------------------------------------
        # Parent remains the TOTAL row, but areas belong to the
        # detailed Survey rows underneath it.
        # ----------------------------------------------------

        row[
            "from_area"
        ] = ""

        row[
            "to_area"
        ] = ""

        row[
            "hauling_distance_m"
        ] = ""

        row[
            "productivity_bcm_hd"
        ] = ""

        row[
            "productivity_survey_parent_area_cleared_v22"
        ] = 1

        output_rows.append(
            row
        )

        # ----------------------------------------------------
        # Exact Survey child rows.
        # ----------------------------------------------------

        for child_index, child in enumerate(
            children
        ):

            hours = (
                split_hours[
                    child_index
                ]
            )

            child_output = (
                _productivity_float_v15(
                    child.get(
                        "output"
                    )
                )
            )

            productivity = (
                round(
                    child_output
                    / hours,
                    3,
                )
                if hours > 0
                else 0
            )

            child[
                "working_hours"
            ] = hours

            child[
                "productivity"
            ] = productivity

            child[
                "from_area"
            ] = child_from_area

            child[
                "to_area"
            ] = parent_to_area

            child[
                "hauling_distance_m"
            ] = (
                parent_hauling_distance
            )

            # Coal child Output is Metric Tonnes, therefore do
            # not create a fake BCM/HD calculation from tonnes.
            child[
                "productivity_bcm_hd"
            ] = ""

            child[
                "productivity_survey_hours_area_v22"
            ] = 1

            child[
                "productivity_parent_hours_v22"
            ] = parent_hours

            child[
                "productivity_parent_from_area_v22"
            ] = child_from_area

            output_rows.append(
                child
            )

        index = child_end

    parts[1] = output_rows

    if isinstance(
        result,
        tuple,
    ):
        return tuple(
            parts
        )

    return parts


def execute(filters=None):

    result = (
        _productivity_execute_before_survey_hours_area_v22(
            filters
        )
    )

    return (
        _productivity_apply_survey_hours_area_v22(
            result,
            filters,
        )
    )


# END KOSI_PRODUCTIVITY_SURVEY_HOURS_AREA_V22


# ============================================================
# KOSI_PRODUCTIVITY_SURVEY_CHILD_EDIT_V23
#
# Exact Survey child rows become independently editable.
#
# Editable:
#   From Area
#   To Area
#   Hauling Distance (M)
#
# Material total rows remain totals only and are NOT editable.
#
# Duplicate Survey rows remain independent by Survey + IDX.
#
# Example:
#
#   4LCOAL - LOAD & HAUL   Survey IDX 12
#   4LCOAL - LOAD & HAUL   Survey IDX 13
#
# These receive different permanent override keys.
# ============================================================

import hashlib as _productivity_hashlib_v23


_productivity_execute_before_survey_child_edit_v23 = execute


def _productivity_survey_edit_key_v23(
    site,
    start_date,
    end_date,
    shift,
    monthly_production_plan,
    category,
    survey_name,
    survey_idx,
    material_ref,
):

    parts = [
        str(site or "").strip(),
        str(start_date or "").strip(),
        str(end_date or "").strip(),
        str(shift or "").strip(),
        str(
            monthly_production_plan
            or ""
        ).strip(),
        str(category or "").strip(),
        str(survey_name or "").strip(),
        str(survey_idx or "").strip(),
        str(material_ref or "").strip(),
    ]

    raw = "|".join(
        parts
    )

    digest = (
        _productivity_hashlib_v23.sha1(
            raw.encode(
                "utf-8"
            )
        ).hexdigest()
    )

    return (
        "SURVEYV23::"
        + digest
    )


def _productivity_survey_plan_v23(
    survey_name,
):

    if not survey_name:
        return ""

    try:

        return str(
            frappe.db.get_value(
                "Survey",
                survey_name,
                "monthly_production_plan_ref",
            )
            or ""
        ).strip()

    except Exception:

        return ""


def _productivity_apply_survey_child_edit_v23(
    result,
    filters,
):

    if not result:
        return result

    filters = frappe._dict(
        filters or {}
    )

    basis = str(
        filters.get(
            "bcm_basis"
        )
        or ""
    ).strip().lower()

    view = str(
        filters.get(
            "summary_view"
        )
        or ""
    ).strip().lower()

    if (
        not basis.startswith(
            "actual"
        )
        or view
        != "hours and material"
    ):
        return result

    parts = list(
        result
    )

    if len(parts) < 2:
        return result

    rows = list(
        parts[1]
        or []
    )

    site = str(
        filters.get(
            "site"
        )
        or filters.get(
            "location"
        )
        or ""
    ).strip()

    start_date = (
        filters.get(
            "start_date"
        )
        or filters.get(
            "from_date"
        )
        or ""
    )

    end_date = (
        filters.get(
            "end_date"
        )
        or filters.get(
            "to_date"
        )
        or ""
    )

    shift = str(
        filters.get(
            "shift"
        )
        or ""
    ).strip()

    current_category = ""
    current_survey = ""
    current_plan = ""

    keys = []

    # ========================================================
    # FIRST PASS
    # Assign unique keys to exact Survey child rows.
    # ========================================================

    for row in rows:

        if not hasattr(
            row,
            "get",
        ):
            continue

        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()

        survey_marker = str(
            row.get(
                "productivity_coal_tonnes_survey_v19"
            )
            or ""
        ).strip()

        if survey_marker:

            current_survey = (
                survey_marker
            )

            current_plan = (
                _productivity_survey_plan_v23(
                    current_survey
                )
            )

        if label in (
            "Excavator",
            "ADT",
            "Dozer",
        ):

            current_category = label

        # ----------------------------------------------------
        # Material total row:
        # no editor anymore.
        # ----------------------------------------------------

        if row.get(
            "productivity_survey_parent_area_cleared_v22"
        ):

            row.pop(
                "productivity_edit_key",
                None,
            )

            row[
                "productivity_survey_parent_locked_v23"
            ] = 1

            continue

        if not row.get(
            "productivity_survey_hours_area_v22"
        ):
            continue

        survey_idx = row.get(
            "productivity_survey_row_idx_v21"
        )

        material_ref = str(
            row.get(
                "label"
            )
            or row.get(
                "material"
            )
            or ""
        ).strip()

        row_key = (
            _productivity_survey_edit_key_v23(
                site,
                start_date,
                end_date,
                shift,
                current_plan,
                current_category,
                current_survey,
                survey_idx,
                material_ref,
            )
        )

        row[
            "productivity_edit_key"
        ] = row_key

        row[
            "productivity_survey_editable_v23"
        ] = 1

        row[
            "productivity_survey_name_v23"
        ] = current_survey

        row[
            "productivity_survey_plan_v23"
        ] = current_plan

        row[
            "productivity_survey_category_v23"
        ] = current_category

        keys.append(
            row_key
        )

    # ========================================================
    # LOAD SAVED CHILD OVERRIDES
    # ========================================================

    saved_map = {}

    if (
        keys
        and frappe.db.exists(
            "DocType",
            "Productivity Area Override",
        )
    ):

        saved_rows = frappe.get_all(
            "Productivity Area Override",

            filters={
                "row_key": [
                    "in",
                    list(
                        set(
                            keys
                        )
                    ),
                ],
            },

            fields=[
                "row_key",
                "from_area",
                "to_area",
                "hauling_distance_m",
            ],

            limit_page_length=0,
        )

        saved_map = {
            str(
                saved.row_key
            ):
                saved

            for saved in saved_rows
        }

    # ========================================================
    # APPLY SAVED OVERRIDES
    # ========================================================

    for row in rows:

        if not (
            hasattr(
                row,
                "get",
            )
            and row.get(
                "productivity_survey_editable_v23"
            )
        ):
            continue

        row_key = str(
            row.get(
                "productivity_edit_key"
            )
            or ""
        ).strip()

        saved = saved_map.get(
            row_key
        )

        if not saved:
            continue

        row[
            "from_area"
        ] = str(
            saved.from_area
            or ""
        ).strip()

        row[
            "to_area"
        ] = str(
            saved.to_area
            or ""
        ).strip()

        distance = str(
            saved.hauling_distance_m
            or ""
        ).strip()

        if distance in (
            "0",
            "0.0",
            "0.00",
            "0.000",
        ):
            distance = ""

        row[
            "hauling_distance_m"
        ] = distance

        row[
            "productivity_survey_manual_override_v23"
        ] = 1

    parts[1] = rows

    if isinstance(
        result,
        tuple,
    ):
        return tuple(
            parts
        )

    return parts


def execute(filters=None):

    result = (
        _productivity_execute_before_survey_child_edit_v23(
            filters
        )
    )

    return (
        _productivity_apply_survey_child_edit_v23(
            result,
            filters,
        )
    )


@frappe.whitelist()
def save_productivity_survey_override_v23(
    row_key,
    site="",
    start_date="",
    end_date="",
    shift="",
    monthly_production_plan="",
    category="",
    survey_name="",
    survey_idx="",
    material="",
    from_area="",
    to_area="",
    hauling_distance_m="",
):

    if (
        frappe.session.user
        == "Guest"
    ):

        frappe.throw(
            "Please log in before editing Productivity.",
            frappe.PermissionError,
        )

    if (
        "System Manager"
        not in frappe.get_roles(
            frappe.session.user
        )
        and not frappe.has_permission(
            "Hourly Production",
            ptype="write",
        )
    ):

        frappe.throw(
            "You do not have permission to edit "
            "Productivity area information.",
            frappe.PermissionError,
        )

    site = str(
        site or ""
    ).strip()

    start_date = str(
        start_date or ""
    ).strip()

    end_date = str(
        end_date or ""
    ).strip()

    shift = str(
        shift or ""
    ).strip()

    monthly_production_plan = str(
        monthly_production_plan
        or ""
    ).strip()

    category = str(
        category or ""
    ).strip()

    survey_name = str(
        survey_name or ""
    ).strip()

    material = str(
        material or ""
    ).strip()

    survey_idx_text = str(
        survey_idx or ""
    ).strip()

    expected_key = (
        _productivity_survey_edit_key_v23(
            site,
            start_date,
            end_date,
            shift,
            monthly_production_plan,
            category,
            survey_name,
            survey_idx_text,
            material,
        )
    )

    if str(
        row_key or ""
    ).strip() != expected_key:

        frappe.throw(
            "Productivity Survey row key does not "
            "match the selected report row."
        )

    # ========================================================
    # Validate that this exact Survey row really exists.
    # ========================================================

    if not frappe.db.exists(
        "Survey",
        survey_name,
    ):

        frappe.throw(
            "Survey does not exist."
        )

    survey = frappe.get_doc(
        "Survey",
        survey_name,
    )

    exact_row = None

    for source_row in (
        survey.get(
            "surveyed_values"
        )
        or []
    ):

        if str(
            source_row.get(
                "idx"
            )
            or ""
        ).strip() != survey_idx_text:

            continue

        exact_row = (
            source_row
        )

        break

    if exact_row is None:

        frappe.throw(
            "The selected Survey material row "
            "does not exist."
        )

    actual_material_ref = str(
        exact_row.get(
            "mat_type_ref"
        )
        or ""
    ).strip()

    if (
        actual_material_ref
        != material
    ):

        frappe.throw(
            "Survey material reference does not "
            "match the selected Productivity row."
        )

    existing_name = (
        frappe.db.get_value(
            "Productivity Area Override",
            {
                "row_key":
                    expected_key,
            },
            "name",
        )
    )

    if existing_name:

        doc = frappe.get_doc(
            "Productivity Area Override",
            existing_name,
        )

    else:

        doc = frappe.get_doc({
            "doctype":
                "Productivity Area Override",

            "row_key":
                expected_key,

            "site":
                site,

            "start_date":
                start_date
                or None,

            "end_date":
                end_date
                or None,

            "shift":
                shift,

            "monthly_production_plan":
                monthly_production_plan
                or None,

            "category":
                category,

            "machine":
                (
                    "SURVEY::"
                    + survey_name
                    + "::"
                    + survey_idx_text
                ),

            "material":
                material,
        })

    doc.row_key = (
        expected_key
    )

    doc.from_area = str(
        from_area or ""
    ).strip()

    doc.to_area = str(
        to_area or ""
    ).strip()

    doc.hauling_distance_m = str(
        hauling_distance_m
        or ""
    ).strip()

    if doc.is_new():

        doc.insert(
            ignore_permissions=True
        )

    else:

        doc.save(
            ignore_permissions=True
        )

    return {
        "saved":
            True,

        "row_key":
            expected_key,

        "from_area":
            doc.from_area,

        "to_area":
            doc.to_area,

        "hauling_distance_m":
            doc.hauling_distance_m,
    }


# END KOSI_PRODUCTIVITY_SURVEY_CHILD_EDIT_V23


# ============================================================
# KOSI_PRODUCTIVITY_CHILD_PRODUCTIVITY_V24
#
# Hours and Material / Actual BCMs
#
# 1. Child Survey hours are displayed as whole hours.
#
# 2. Whole hours are allocated with a largest-remainder method
#    so the visible child hours add back to the visible parent
#    material total.
#
# 3. Child productivity is recalculated from THAT CHILD:
#
#       Child Output / Child Working Hours
#
#    It is NOT copied from the parent material total.
#
# 4. Parent Material column is blank:
#
#       Label       Material
#       Softs
#         Topsoil   Topsoil
#
# 5. Parent material-total rows are bold.
#
# Coal child output remains Metric Tonnes, therefore Coal child
# productivity remains tonnes/hour.
# ============================================================


_productivity_execute_before_child_productivity_v24 = execute


def _productivity_integer_hours_v24(
    parent_hours,
    children,
):

    parent_hours = (
        _productivity_float_v15(
            parent_hours
        )
    )

    target_hours = int(
        parent_hours
        + 0.5
    )

    if not children:

        return []

    exact_hours = [
        max(
            0.0,
            _productivity_float_v15(
                child.get(
                    "working_hours"
                )
            ),
        )
        for child in children
    ]

    floors = [
        int(
            value
        )
        for value in exact_hours
    ]

    allocated = sum(
        floors
    )

    remaining = (
        target_hours
        - allocated
    )

    fractions = [
        (
            exact_hours[index]
            - floors[index],
            index,
        )
        for index in range(
            len(children)
        )
    ]

    fractions.sort(
        key=lambda item: (
            item[0],
            -item[1],
        ),
        reverse=True,
    )

    result = list(
        floors
    )

    for _fraction, index in fractions:

        if remaining <= 0:
            break

        result[index] += 1
        remaining -= 1

    # Defensive fallback in case of unusual source data.
    if (
        remaining > 0
        and result
    ):

        result[-1] += (
            remaining
        )

    return result


def _productivity_apply_child_productivity_v24(
    result,
    filters,
):

    if not result:
        return result

    filters = frappe._dict(
        filters or {}
    )

    basis = str(
        filters.get(
            "bcm_basis"
        )
        or ""
    ).strip().lower()

    view = str(
        filters.get(
            "summary_view"
        )
        or ""
    ).strip().lower()

    if (
        not basis.startswith(
            "actual"
        )
        or view
        != "hours and material"
    ):

        return result

    parts = list(
        result
    )

    if len(parts) < 2:
        return result

    rows = list(
        parts[1]
        or []
    )

    output_rows = []

    index = 0

    while index < len(
        rows
    ):

        original = rows[
            index
        ]

        if not hasattr(
            original,
            "get",
        ):

            output_rows.append(
                original
            )

            index += 1

            continue

        row = dict(
            original
        )

        # ----------------------------------------------------
        # Find exact Survey children immediately below parent.
        # ----------------------------------------------------

        child_index = (
            index + 1
        )

        children = []

        while (
            child_index
            < len(rows)
        ):

            candidate = rows[
                child_index
            ]

            if not (
                hasattr(
                    candidate,
                    "get",
                )
                and candidate.get(
                    "productivity_survey_hours_area_v22"
                )
            ):

                break

            children.append(
                dict(
                    candidate
                )
            )

            child_index += 1

        # ----------------------------------------------------
        # MATERIAL TOTAL
        # ----------------------------------------------------

        if (
            row.get(
                "productivity_survey_parent_area_cleared_v22"
            )
            and children
        ):

            # Remove duplicate material name from Material
            # column. Label remains Softs / Coal / Hards.
            row[
                "material"
            ] = ""

            existing_style = str(
                row.get(
                    "style"
                )
                or ""
            ).strip()

            if (
                "font-weight"
                not in existing_style.lower()
            ):

                if (
                    existing_style
                    and not existing_style.endswith(
                        ";"
                    )
                ):

                    existing_style += ";"

                existing_style += (
                    "font-weight:700;"
                )

            row[
                "style"
            ] = existing_style

            row[
                "productivity_material_total_v24"
            ] = 1

            output_rows.append(
                row
            )

            # ------------------------------------------------
            # Allocate visible whole hours to children.
            # ------------------------------------------------

            visible_hours = (
                _productivity_integer_hours_v24(
                    row.get(
                        "working_hours"
                    ),
                    children,
                )
            )

            for position, child in enumerate(
                children
            ):

                exact_hours = (
                    _productivity_float_v15(
                        child.get(
                            "working_hours"
                        )
                    )
                )

                hours = (
                    visible_hours[
                        position
                    ]
                )

                output = (
                    _productivity_float_v15(
                        child.get(
                            "output"
                        )
                    )
                )

                child[
                    "productivity_exact_hours_v24"
                ] = exact_hours

                child[
                    "working_hours"
                ] = hours

                if hours > 0:

                    child[
                        "productivity"
                    ] = round(
                        output
                        / hours,
                        0,
                    )

                else:

                    child[
                        "productivity"
                    ] = 0

                child[
                    "productivity_child_recalculated_v24"
                ] = 1

                output_rows.append(
                    child
                )

            index = (
                child_index
            )

            continue

        output_rows.append(
            row
        )

        index += 1

    parts[1] = output_rows

    if isinstance(
        result,
        tuple,
    ):

        return tuple(
            parts
        )

    return parts


def execute(filters=None):

    result = (
        _productivity_execute_before_child_productivity_v24(
            filters
        )
    )

    return (
        _productivity_apply_child_productivity_v24(
            result,
            filters,
        )
    )


# END KOSI_PRODUCTIVITY_CHILD_PRODUCTIVITY_V24


# ============================================================
# KOSI_PRODUCTIVITY_EXCAVATOR_BCM_ONLY_V25
#
# HOURS AND MATERIAL / ACTUAL BCMs
#
# EXCAVATOR ONLY:
#
#     Label                visible
#     Working Hours        visible
#     Output               BCM ONLY
#     Productivity         BCM/Hr ONLY
#
#     Material             BLANK
#     From Area            BLANK
#     To Area              BLANK
#     Hauling Distance     BLANK
#     BCM/HD               BLANK
#
# Coal:
#
# V19/V21 display Metric Tonnes for detailed Coal rows.
# V25 restores Survey BCM for Excavator Coal only.
#
# ADT / Dozer behaviour remains unchanged.
#
# Exact Survey child rows remain separate.
# ============================================================


_productivity_execute_before_excavator_bcm_v25 = execute


def _productivity_survey_row_bcm_v25(
    survey_name,
    survey_idx,
    cache,
):

    survey_name = str(
        survey_name
        or ""
    ).strip()

    survey_idx = str(
        survey_idx
        or ""
    ).strip()

    cache_key = (
        survey_name,
        survey_idx,
    )

    if cache_key in cache:

        return cache[
            cache_key
        ]

    bcm = 0.0

    if not (
        survey_name
        and survey_idx
    ):

        cache[
            cache_key
        ] = bcm

        return bcm

    try:

        survey = frappe.get_doc(
            "Survey",
            survey_name,
        )

        for source_row in (
            survey.get(
                "surveyed_values"
            )
            or []
        ):

            if str(
                source_row.get(
                    "idx"
                )
                or ""
            ).strip() != survey_idx:

                continue

            bcm = (
                _productivity_float_v15(
                    source_row.get(
                        "bcm"
                    )
                )
            )

            break

    except Exception:

        bcm = 0.0

    cache[
        cache_key
    ] = bcm

    return bcm


def _productivity_allocate_bcm_hours_v25(
    parent_hours,
    outputs,
):

    parent_hours = (
        _productivity_float_v15(
            parent_hours
        )
    )

    target_hours = int(
        parent_hours
        + 0.5
    )

    outputs = [
        max(
            0.0,
            _productivity_float_v15(
                value
            ),
        )
        for value in outputs
    ]

    total_output = sum(
        outputs
    )

    if not outputs:

        return []

    if (
        target_hours <= 0
        or total_output <= 0
    ):

        return [
            0
            for value in outputs
        ]

    exact_hours = [
        parent_hours
        * (
            output
            / total_output
        )
        for output in outputs
    ]

    result = [
        int(
            value
        )
        for value in exact_hours
    ]

    allocated = sum(
        result
    )

    remaining = (
        target_hours
        - allocated
    )

    fractions = [
        (
            exact_hours[index]
            - result[index],
            index,
        )
        for index in range(
            len(result)
        )
    ]

    fractions.sort(
        key=lambda item: (
            item[0],
            -item[1],
        ),
        reverse=True,
    )

    for _fraction, index in fractions:

        if remaining <= 0:
            break

        result[index] += 1
        remaining -= 1

    if (
        remaining > 0
        and result
    ):

        result[-1] += (
            remaining
        )

    return result


def _productivity_clear_excavator_detail_v25(
    row,
):

    row[
        "material"
    ] = ""

    row[
        "from_area"
    ] = ""

    row[
        "to_area"
    ] = ""

    row[
        "hauling_distance_m"
    ] = ""

    row[
        "productivity_bcm_hd"
    ] = ""

    # Excavator is production-only in this view.
    # Remove Survey-child area editor.
    row.pop(
        "productivity_edit_key",
        None,
    )

    row.pop(
        "productivity_survey_editable_v23",
        None,
    )

    row.pop(
        "productivity_survey_manual_override_v23",
        None,
    )

    row[
        "productivity_excavator_bcm_only_v25"
    ] = 1

    return row


def _productivity_apply_excavator_bcm_v25(
    result,
    filters,
):

    if not result:
        return result

    filters = frappe._dict(
        filters or {}
    )

    basis = str(
        filters.get(
            "bcm_basis"
        )
        or ""
    ).strip().lower()

    view = str(
        filters.get(
            "summary_view"
        )
        or ""
    ).strip().lower()

    if (
        not basis.startswith(
            "actual"
        )
        or view
        != "hours and material"
    ):

        return result

    parts = list(
        result
    )

    if len(parts) < 2:

        return result

    rows = list(
        parts[1]
        or []
    )

    output_rows = []

    current_category = ""

    survey_cache = {}

    index = 0

    while index < len(
        rows
    ):

        original = rows[
            index
        ]

        if not hasattr(
            original,
            "get",
        ):

            output_rows.append(
                original
            )

            index += 1

            continue

        row = dict(
            original
        )

        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()

        if label in (
            "Excavator",
            "ADT",
            "Dozer",
        ):

            current_category = (
                label
            )

        # ----------------------------------------------------
        # Everything outside Excavator stays exactly as before.
        # ----------------------------------------------------

        if (
            current_category
            != "Excavator"
        ):

            output_rows.append(
                row
            )

            index += 1

            continue

        # ----------------------------------------------------
        # Find V22 Survey children directly below material total
        # ----------------------------------------------------

        child_index = (
            index + 1
        )

        children = []

        while (
            child_index
            < len(rows)
        ):

            candidate = rows[
                child_index
            ]

            if not (
                hasattr(
                    candidate,
                    "get",
                )
                and candidate.get(
                    "productivity_survey_hours_area_v22"
                )
            ):

                break

            children.append(
                dict(
                    candidate
                )
            )

            child_index += 1

        # ----------------------------------------------------
        # Excavator Material Total
        # ----------------------------------------------------

        if (
            row.get(
                "productivity_material_total_v24"
            )
            and children
        ):

            parent_material = (
                label
            )

            final_outputs = []

            # ------------------------------------------------
            # Determine BCM for each child.
            # ------------------------------------------------

            for child in children:

                child_output = (
                    _productivity_float_v15(
                        child.get(
                            "output"
                        )
                    )
                )

                if (
                    parent_material
                    == "Coal"
                ):

                    survey_name = str(
                        child.get(
                            "productivity_survey_name_v23"
                        )
                        or ""
                    ).strip()

                    survey_idx = (
                        child.get(
                            "productivity_survey_row_idx_v21"
                        )
                    )

                    survey_bcm = (
                        _productivity_survey_row_bcm_v25(
                            survey_name,
                            survey_idx,
                            survey_cache,
                        )
                    )

                    if survey_bcm > 0:

                        child_output = (
                            survey_bcm
                        )

                final_outputs.append(
                    child_output
                )

            # ------------------------------------------------
            # Parent material total = sum exact child BCMs.
            # ------------------------------------------------

            parent_output = sum(
                final_outputs
            )

            parent_hours = (
                _productivity_float_v15(
                    row.get(
                        "working_hours"
                    )
                )
            )

            row[
                "output"
            ] = parent_output

            if (
                "adjusted_bcm"
                in row
            ):

                row[
                    "adjusted_bcm"
                ] = parent_output

            row[
                "productivity"
            ] = (
                round(
                    parent_output
                    / parent_hours,
                    0,
                )
                if parent_hours > 0
                else 0
            )

            row[
                "productivity_output_unit_v19"
            ] = "BCM"

            row = (
                _productivity_clear_excavator_detail_v25(
                    row
                )
            )

            output_rows.append(
                row
            )

            # ------------------------------------------------
            # Allocate visible hours according to BCM.
            # ------------------------------------------------

            visible_hours = (
                _productivity_allocate_bcm_hours_v25(
                    parent_hours,
                    final_outputs,
                )
            )

            for position, child in enumerate(
                children
            ):

                output = (
                    final_outputs[
                        position
                    ]
                )

                hours = (
                    visible_hours[
                        position
                    ]
                )

                child[
                    "output"
                ] = output

                if (
                    "adjusted_bcm"
                    in child
                ):

                    child[
                        "adjusted_bcm"
                    ] = output

                child[
                    "working_hours"
                ] = hours

                child[
                    "productivity"
                ] = (
                    round(
                        output
                        / hours,
                        0,
                    )
                    if hours > 0
                    else 0
                )

                child[
                    "productivity_output_unit_v19"
                ] = "BCM"

                child[
                    "productivity_child_recalculated_v24"
                ] = 1

                child = (
                    _productivity_clear_excavator_detail_v25(
                        child
                    )
                )

                output_rows.append(
                    child
                )

            index = (
                child_index
            )

            continue

        # ----------------------------------------------------
        # Excavator category/header/other rows
        # ----------------------------------------------------

        row = (
            _productivity_clear_excavator_detail_v25(
                row
            )
        )

        output_rows.append(
            row
        )

        index += 1

    parts[1] = (
        output_rows
    )

    if isinstance(
        result,
        tuple,
    ):

        return tuple(
            parts
        )

    return parts


def execute(filters=None):

    result = (
        _productivity_execute_before_excavator_bcm_v25(
            filters
        )
    )

    return (
        _productivity_apply_excavator_bcm_v25(
            result,
            filters,
        )
    )


# END KOSI_PRODUCTIVITY_EXCAVATOR_BCM_ONLY_V25


# ============================================================
# KOSI_PRODUCTIVITY_BCM_HEADINGS_HD_V28
#
# FINAL BCM presentation rules.
#
# COLUMN HEADINGS:
#
# Actual BCMs:
#     Output Actual (BCM)
#
# Tallies BCMs:
#     Output Tallies (BCM)
#
# Always:
#     Productivity (BCM/HR)
#     Productivity (BCM/HD)
#
#
# BCM/HD FORMULA:
#
#     Average Hauling Distance
#         = (Lower + Upper) / 2
#
#     Productivity BCM/HD
#         = Output BCM / Average Hauling Distance
#
#
# Example:
#
#     Distance = 100-500
#     Average  = (100 + 500) / 2 = 300
#
#     Output   = 46,515 BCM
#
#     BCM/HD   = 46,515 / 300
#              = 155.05
#              = 155 displayed
#
#
# IMPORTANT:
# Actual output is now BCM throughout.
# Coal uses Survey BCM, not Metric Tonnes.
# ============================================================


_productivity_execute_before_bcm_headings_hd_v28 = execute


def _productivity_number_v28(
    value,
):

    try:

        return float(
            str(
                value
                or 0
            ).replace(
                ",",
                "",
            )
        )

    except (
        TypeError,
        ValueError,
    ):

        return 0.0


def _productivity_columns_v28(
    columns,
    basis,
):

    result = []

    basis = str(
        basis
        or ""
    ).strip().lower()

    if basis.startswith(
        "actual"
    ):

        output_label = (
            "Output Actual (BCM)"
        )

    elif basis.startswith(
        "tallies"
    ):

        output_label = (
            "Output Tallies (BCM)"
        )

    else:

        output_label = (
            "Output (BCM)"
        )

    for original in (
        columns
        or []
    ):

        if not hasattr(
            original,
            "get",
        ):

            result.append(
                original
            )

            continue

        column = dict(
            original
        )

        fieldname = str(
            column.get(
                "fieldname"
            )
            or ""
        ).strip()

        if fieldname in (
            "output",
            "adjusted_bcm",
        ):

            column[
                "label"
            ] = output_label

            column[
                "precision"
            ] = 0

        elif (
            fieldname
            == "productivity"
        ):

            column[
                "label"
            ] = (
                "Productivity (BCM/HR)"
            )

            column[
                "precision"
            ] = 0

        elif (
            fieldname
            == "productivity_bcm_hd"
        ):

            column[
                "label"
            ] = (
                "Productivity (BCM/HD)"
            )

            column[
                "precision"
            ] = 0

        result.append(
            column
        )

    return result


def _productivity_survey_child_bcm_v28(
    row,
    cache,
):

    survey_name = str(
        row.get(
            "productivity_survey_name_v23"
        )
        or ""
    ).strip()

    survey_idx = str(
        row.get(
            "productivity_survey_row_idx_v21"
        )
        or ""
    ).strip()

    key = (
        survey_name,
        survey_idx,
    )

    if key in cache:

        return cache[
            key
        ]

    bcm = 0.0

    if (
        survey_name
        and survey_idx
    ):

        try:

            survey = frappe.get_doc(
                "Survey",
                survey_name,
            )

            for source_row in (
                survey.get(
                    "surveyed_values"
                )
                or []
            ):

                if str(
                    source_row.get(
                        "idx"
                    )
                    or ""
                ).strip() != survey_idx:

                    continue

                bcm = (
                    _productivity_number_v28(
                        source_row.get(
                            "bcm"
                        )
                    )
                )

                break

        except Exception:

            bcm = 0.0

    cache[
        key
    ] = bcm

    return bcm


def _productivity_row_actual_bcm_v28(
    row,
    survey_cache,
):

    # --------------------------------------------------------
    # V19 saved the original BCM before displaying Coal tonnes.
    # Use that BCM whenever available.
    # --------------------------------------------------------

    coal_bcm = row.get(
        "productivity_coal_bcm_v19"
    )

    if coal_bcm not in (
        None,
        "",
    ):

        value = (
            _productivity_number_v28(
                coal_bcm
            )
        )

        if value > 0:

            return value

    # --------------------------------------------------------
    # Exact V21/V23 Survey child.
    # Coal child must come from Survey.bcm.
    # --------------------------------------------------------

    parent_material = str(
        row.get(
            "productivity_parent_material_v21"
        )
        or ""
    ).strip()

    if parent_material == "Coal":

        survey_bcm = (
            _productivity_survey_child_bcm_v28(
                row,
                survey_cache,
            )
        )

        if survey_bcm > 0:

            return survey_bcm

    return (
        _productivity_number_v28(
            row.get(
                "output"
            )
        )
    )


def _productivity_allocate_hours_v28(
    parent_hours,
    outputs,
):

    parent_hours = (
        _productivity_number_v28(
            parent_hours
        )
    )

    target = int(
        parent_hours
        + 0.5
    )

    outputs = [
        max(
            0.0,
            _productivity_number_v28(
                value
            ),
        )
        for value in outputs
    ]

    total_output = sum(
        outputs
    )

    if not outputs:

        return []

    if (
        target <= 0
        or total_output <= 0
    ):

        return [
            0
            for value in outputs
        ]

    exact = [
        parent_hours
        * (
            output
            / total_output
        )
        for output in outputs
    ]

    hours = [
        int(
            value
        )
        for value in exact
    ]

    remaining = (
        target
        - sum(
            hours
        )
    )

    fractions = [
        (
            exact[index]
            - hours[index],
            index,
        )
        for index in range(
            len(hours)
        )
    ]

    fractions.sort(
        key=lambda item: (
            item[0],
            -item[1],
        ),
        reverse=True,
    )

    for _fraction, index in fractions:

        if remaining <= 0:
            break

        hours[index] += 1
        remaining -= 1

    if (
        remaining > 0
        and hours
    ):

        hours[-1] += (
            remaining
        )

    return hours


def _productivity_apply_actual_bcm_v28(
    rows,
):

    rows = list(
        rows
        or []
    )

    survey_cache = {}

    output_rows = []

    index = 0

    while index < len(
        rows
    ):

        original = rows[
            index
        ]

        if not hasattr(
            original,
            "get",
        ):

            output_rows.append(
                original
            )

            index += 1

            continue

        row = dict(
            original
        )

        child_index = (
            index + 1
        )

        children = []

        while (
            child_index
            < len(rows)
        ):

            candidate = rows[
                child_index
            ]

            if not (
                hasattr(
                    candidate,
                    "get",
                )
                and candidate.get(
                    "productivity_survey_hours_area_v22"
                )
            ):

                break

            children.append(
                dict(
                    candidate
                )
            )

            child_index += 1

        # ----------------------------------------------------
        # Material total with exact Survey children.
        # Rebuild total + child rows in BCM.
        # ----------------------------------------------------

        if (
            row.get(
                "productivity_material_total_v24"
            )
            and children
        ):

            child_outputs = [
                _productivity_row_actual_bcm_v28(
                    child,
                    survey_cache,
                )
                for child in children
            ]

            parent_output = sum(
                child_outputs
            )

            parent_hours = (
                _productivity_number_v28(
                    row.get(
                        "working_hours"
                    )
                )
            )

            row[
                "output"
            ] = parent_output

            if (
                "adjusted_bcm"
                in row
            ):

                row[
                    "adjusted_bcm"
                ] = parent_output

            row[
                "productivity"
            ] = (
                round(
                    parent_output
                    / parent_hours,
                    0,
                )
                if parent_hours > 0
                else 0
            )

            row[
                "productivity_output_unit_v19"
            ] = "BCM"

            output_rows.append(
                row
            )

            split_hours = (
                _productivity_allocate_hours_v28(
                    parent_hours,
                    child_outputs,
                )
            )

            for position, child in enumerate(
                children
            ):

                child_output = (
                    child_outputs[
                        position
                    ]
                )

                child_hours = (
                    split_hours[
                        position
                    ]
                )

                child[
                    "output"
                ] = child_output

                if (
                    "adjusted_bcm"
                    in child
                ):

                    child[
                        "adjusted_bcm"
                    ] = child_output

                child[
                    "working_hours"
                ] = child_hours

                child[
                    "productivity"
                ] = (
                    round(
                        child_output
                        / child_hours,
                        0,
                    )
                    if child_hours > 0
                    else 0
                )

                child[
                    "productivity_output_unit_v19"
                ] = "BCM"

                output_rows.append(
                    child
                )

            index = (
                child_index
            )

            continue

        # ----------------------------------------------------
        # Other Coal rows, including Summary Per Machine.
        # V19 stored their original BCM.
        # ----------------------------------------------------

        bcm = (
            _productivity_row_actual_bcm_v28(
                row,
                survey_cache,
            )
        )

        if row.get(
            "productivity_coal_bcm_v19"
        ) not in (
            None,
            "",
        ):

            row[
                "output"
            ] = bcm

            if (
                "adjusted_bcm"
                in row
            ):

                row[
                    "adjusted_bcm"
                ] = bcm

            hours = (
                _productivity_number_v28(
                    row.get(
                        "working_hours"
                    )
                )
            )

            row[
                "productivity"
            ] = (
                round(
                    bcm
                    / hours,
                    0,
                )
                if hours > 0
                else 0
            )

            row[
                "productivity_output_unit_v19"
            ] = "BCM"

        output_rows.append(
            row
        )

        index += 1

    return output_rows


def _productivity_apply_bcm_hd_v28(
    rows,
):

    result = []

    for original in (
        rows
        or []
    ):

        if not hasattr(
            original,
            "get",
        ):

            result.append(
                original
            )

            continue

        row = dict(
            original
        )

        distance = str(
            row.get(
                "hauling_distance_m"
            )
            or ""
        ).strip()

        if not distance:

            row[
                "productivity_bcm_hd"
            ] = ""

            result.append(
                row
            )

            continue

        try:

            midpoint = (
                _productivity_distance_midpoint(
                    distance
                )
            )

        except Exception:

            midpoint = None

        output = (
            _productivity_number_v28(
                row.get(
                    "output"
                )
            )
        )

        if (
            midpoint is None
            or midpoint <= 0
            or output <= 0
        ):

            row[
                "productivity_bcm_hd"
            ] = ""

        else:

            row[
                "productivity_bcm_hd"
            ] = round(
                output
                / midpoint,
                0,
            )

        result.append(
            row
        )

    return result


def _productivity_apply_v28(
    result,
    filters,
):

    if not result:

        return result

    filters = frappe._dict(
        filters
        or {}
    )

    basis = str(
        filters.get(
            "bcm_basis"
        )
        or ""
    ).strip()

    parts = list(
        result
    )

    if len(parts) < 2:

        return result

    parts[0] = (
        _productivity_columns_v28(
            parts[0],
            basis,
        )
    )

    rows = list(
        parts[1]
        or []
    )

    if basis.lower().startswith(
        "actual"
    ):

        rows = (
            _productivity_apply_actual_bcm_v28(
                rows
            )
        )

    rows = (
        _productivity_apply_bcm_hd_v28(
            rows
        )
    )

    parts[1] = rows

    if isinstance(
        result,
        tuple,
    ):

        return tuple(
            parts
        )

    return parts


def execute(filters=None):

    result = (
        _productivity_execute_before_bcm_headings_hd_v28(
            filters
        )
    )

    return (
        _productivity_apply_v28(
            result,
            filters,
        )
    )


# END KOSI_PRODUCTIVITY_BCM_HEADINGS_HD_V28


# ============================================================
# KOSI_PRODUCTIVITY_SAVED_OVERRIDE_PERSIST_V31
#
# FINAL SAVED SURVEY OVERRIDE PERSISTENCE
#
# Problem:
# Save Override correctly writes the Survey-row override, but
# later Productivity wrappers can rebuild/modify the row during
# report refresh.
#
# V31 runs LAST.
#
# For exact editable Survey rows it re-applies:
#
#   From Area
#   To Area
#   Hauling Distance
#
# directly from Productivity Area Override.
#
# It then recalculates:
#
#   Productivity (BCM/HD)
#       = Output BCM / average hauling distance
#
# Saved values therefore remain visible after:
#
#   Save Override
#   Report refresh
#   Ctrl+Shift+R
#
# Excavator remains excluded because V25 intentionally removes
# its Survey edit key.
# ============================================================


_productivity_execute_before_saved_override_persist_v31 = execute


def _productivity_apply_saved_override_persist_v31(
    result,
    filters,
):

    if not result:
        return result

    filters = frappe._dict(
        filters
        or {}
    )

    basis = str(
        filters.get(
            "bcm_basis"
        )
        or ""
    ).strip().lower()

    view = str(
        filters.get(
            "summary_view"
        )
        or ""
    ).strip().lower()

    if (
        not basis.startswith(
            "actual"
        )
        or view
        != "hours and material"
    ):

        return result

    parts = list(
        result
    )

    if len(parts) < 2:

        return result

    rows = list(
        parts[1]
        or []
    )

    # ========================================================
    # FIND CURRENT EDITABLE SURVEY ROW KEYS
    # ========================================================

    row_keys = []

    for row in rows:

        if not hasattr(
            row,
            "get",
        ):
            continue

        row_key = str(
            row.get(
                "productivity_edit_key"
            )
            or ""
        ).strip()

        if not row_key.startswith(
            "SURVEYV23::"
        ):

            continue

        row_keys.append(
            row_key
        )

    if not row_keys:

        parts[1] = rows

        if isinstance(
            result,
            tuple,
        ):

            return tuple(
                parts
            )

        return parts

    # ========================================================
    # LOAD PERMANENT SAVED VALUES
    # ========================================================

    saved_rows = frappe.get_all(

        "Productivity Area Override",

        filters={
            "row_key": [
                "in",
                list(
                    set(
                        row_keys
                    )
                ),
            ],
        },

        fields=[
            "row_key",
            "from_area",
            "to_area",
            "hauling_distance_m",
        ],

        limit_page_length=0,
    )

    saved_map = {
        str(
            saved.row_key
        ).strip():
            saved

        for saved in saved_rows
    }

    # ========================================================
    # APPLY THEM LAST
    # ========================================================

    final_rows = []

    for original in rows:

        if not hasattr(
            original,
            "get",
        ):

            final_rows.append(
                original
            )

            continue

        row = dict(
            original
        )

        row_key = str(
            row.get(
                "productivity_edit_key"
            )
            or ""
        ).strip()

        saved = (
            saved_map.get(
                row_key
            )
        )

        if not saved:

            final_rows.append(
                row
            )

            continue

        # ----------------------------------------------------
        # RE-APPLY SAVED VALUES
        # ----------------------------------------------------

        row[
            "from_area"
        ] = str(
            saved.from_area
            or ""
        ).strip()

        row[
            "to_area"
        ] = str(
            saved.to_area
            or ""
        ).strip()

        distance = str(
            saved.hauling_distance_m
            or ""
        ).strip()

        if distance in (
            "0",
            "0.0",
            "0.00",
            "0.000",
        ):

            distance = ""

        row[
            "hauling_distance_m"
        ] = distance

        row[
            "productivity_saved_override_persist_v31"
        ] = 1

        # ----------------------------------------------------
        # BCM/HD FROM SAVED HAULING DISTANCE
        # ----------------------------------------------------

        if not distance:

            row[
                "productivity_bcm_hd"
            ] = ""

            final_rows.append(
                row
            )

            continue

        try:

            midpoint = (
                _productivity_distance_midpoint(
                    distance
                )
            )

        except Exception:

            midpoint = None

        try:

            output = float(
                str(
                    row.get(
                        "output"
                    )
                    or 0
                ).replace(
                    ",",
                    "",
                )
            )

        except (
            TypeError,
            ValueError,
        ):

            output = 0.0

        if (
            midpoint is None
            or midpoint <= 0
            or output <= 0
        ):

            row[
                "productivity_bcm_hd"
            ] = ""

        else:

            # Keep calculation at 2 decimal precision.
            row[
                "productivity_bcm_hd"
            ] = round(
                output
                / midpoint,
                2,
            )

        final_rows.append(
            row
        )

    parts[1] = (
        final_rows
    )

    if isinstance(
        result,
        tuple,
    ):

        return tuple(
            parts
        )

    return parts


def execute(filters=None):

    result = (
        _productivity_execute_before_saved_override_persist_v31(
            filters
        )
    )

    return (
        _productivity_apply_saved_override_persist_v31(
            result,
            filters,
        )
    )


# END KOSI_PRODUCTIVITY_SAVED_OVERRIDE_PERSIST_V31


# ============================================================
# KOSI_PRODUCTIVITY_SUMMARY_MACHINE_BCM_V32
#
# ACTUAL BCMs / SUMMARY PER MACHINE
#
# Problem:
#
# Older V19 logic displayed Coal material output in Metric
# Tonnes while machine totals remained BCM.
#
# This caused visible calculations such as:
#
#     Machine Total = 1,240 BCM
#
# but visible material rows:
#
#     168 + 440 + 689 = 1,297
#
# because Coal was not in the same unit.
#
#
# FINAL RULE:
#
# Summary Per Machine is BCM ONLY.
#
#     Output Actual (BCM)
#     Productivity (BCM/HR)
#
# Coal material rows restore the original BCM saved by V19.
#
# Machine Total:
#
#     sum of that machine's visible material BCM rows
#
# Machine Hours:
#
#     sum of that machine's material hours
#
# Machine Productivity:
#
#     Machine BCM / Machine Hours
#
# Hours and Material is untouched here.
# Tallies is untouched here.
# ============================================================


_productivity_execute_before_summary_machine_bcm_v32 = execute


def _productivity_num_v32(
    value,
):

    try:

        return float(
            str(
                value
                or 0
            ).replace(
                ",",
                "",
            )
        )

    except (
        TypeError,
        ValueError,
    ):

        return 0.0


def _productivity_summary_machine_bcm_v32(
    result,
    filters,
):

    if not result:
        return result

    filters = frappe._dict(
        filters
        or {}
    )

    basis = str(
        filters.get(
            "bcm_basis"
        )
        or ""
    ).strip().lower()

    view = str(
        filters.get(
            "summary_view"
        )
        or ""
    ).strip().lower()

    if (
        not basis.startswith(
            "actual"
        )
        or view
        != "summary per machine"
    ):

        return result

    parts = list(
        result
    )

    if len(parts) < 2:
        return result

    # ========================================================
    # COLUMN HEADINGS
    # ========================================================

    columns = []

    for original in (
        parts[0]
        or []
    ):

        if not hasattr(
            original,
            "get",
        ):

            columns.append(
                original
            )

            continue

        column = dict(
            original
        )

        fieldname = str(
            column.get(
                "fieldname"
            )
            or ""
        ).strip()

        if fieldname in (
            "output",
            "adjusted_bcm",
        ):

            column[
                "label"
            ] = (
                "Output Actual (BCM)"
            )

            column[
                "precision"
            ] = 0

        elif (
            fieldname
            == "productivity"
        ):

            column[
                "label"
            ] = (
                "Productivity (BCM/HR)"
            )

            column[
                "precision"
            ] = 0

        columns.append(
            column
        )

    parts[0] = columns

    # ========================================================
    # PASS 1
    #
    # Restore Coal material rows to BCM.
    # ========================================================

    rows = []

    for original in (
        parts[1]
        or []
    ):

        if not hasattr(
            original,
            "get",
        ):

            rows.append(
                original
            )

            continue

        row = dict(
            original
        )

        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()

        coal_bcm = row.get(
            "productivity_coal_bcm_v19"
        )

        if (
            material == "Coal"
            and coal_bcm not in (
                None,
                "",
            )
        ):

            bcm = (
                _productivity_num_v32(
                    coal_bcm
                )
            )

            row[
                "output"
            ] = bcm

            if (
                "adjusted_bcm"
                in row
            ):

                row[
                    "adjusted_bcm"
                ] = bcm

            hours = (
                _productivity_num_v32(
                    row.get(
                        "working_hours"
                    )
                )
            )

            row[
                "productivity"
            ] = (
                round(
                    bcm
                    / hours,
                    0,
                )
                if hours > 0
                else 0
            )

            row[
                "productivity_output_unit_v19"
            ] = "BCM"

            row[
                "productivity_summary_coal_bcm_v32"
            ] = 1

        rows.append(
            row
        )

    # ========================================================
    # PASS 2
    #
    # Gather material children by machine label.
    #
    # In Summary Per Machine:
    #
    # Machine total:
    #     label = machine
    #     material = blank
    #
    # Material children:
    #     label = same machine
    #     material = Coal / Hards / Softs / etc.
    # ========================================================

    machine_materials = {}

    for row in rows:

        if not hasattr(
            row,
            "get",
        ):
            continue

        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()

        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()

        if not (
            label
            and material
        ):

            continue

        machine_materials.setdefault(
            label,
            []
        ).append(
            row
        )

    # ========================================================
    # PASS 3
    #
    # Recalculate machine total from material children.
    # ========================================================

    final_rows = []

    for original in rows:

        if not hasattr(
            original,
            "get",
        ):

            final_rows.append(
                original
            )

            continue

        row = dict(
            original
        )

        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()

        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()

        children = (
            machine_materials.get(
                label
            )
            or []
        )

        # Machine total only:
        # same machine label has material rows,
        # but this row itself has no material.
        if (
            not material
            and children
        ):

            total_output = sum(
                _productivity_num_v32(
                    child.get(
                        "output"
                    )
                )
                for child in children
            )

            total_hours = sum(
                _productivity_num_v32(
                    child.get(
                        "working_hours"
                    )
                )
                for child in children
            )

            row[
                "output"
            ] = total_output

            if (
                "adjusted_bcm"
                in row
            ):

                row[
                    "adjusted_bcm"
                ] = total_output

            row[
                "working_hours"
            ] = total_hours

            row[
                "productivity"
            ] = (
                round(
                    total_output
                    / total_hours,
                    0,
                )
                if total_hours > 0
                else 0
            )

            row[
                "productivity_summary_machine_total_v32"
            ] = 1

        final_rows.append(
            row
        )

    parts[1] = final_rows

    if isinstance(
        result,
        tuple,
    ):

        return tuple(
            parts
        )

    return parts


def execute(filters=None):

    result = (
        _productivity_execute_before_summary_machine_bcm_v32(
            filters
        )
    )

    return (
        _productivity_summary_machine_bcm_v32(
            result,
            filters,
        )
    )


# END KOSI_PRODUCTIVITY_SUMMARY_MACHINE_BCM_V32


# ============================================================
# KOSI_PRODUCTIVITY_SUMMARY_COAL_TONS_V33
#
# ACTUAL BCMs / SUMMARY PER MACHINE ONLY
#
# FINAL DISPLAY:
#
#     Output Actual (BCM)
#     Coal Tons
#     Productivity (BCM/HR)
#
#
# Coal row:
#
#     Output Actual (BCM) = Coal BCM
#     Coal Tons           = Coal Metric Tonnes
#
#
# Example:
#
#     IS0312              BCM      Coal Tons
#
#       Coal              112      168
#       Hards             440
#       Softs             688
#                         ----
#     TOTAL             1,240      168
#
#
# IMPORTANT:
#
# - Never add tonnes into BCM.
# - Visible material BCM values are integer-rounded so their
#   visible sum equals the visible machine total exactly.
# - Coal tonnes remain in their own separate column.
# - ADT remains duplicated production as before.
# - Total Fleet still excludes ADT duplication:
#
#       Excavator + Dozer
#
# Hours and Material unchanged.
# Tallies unchanged.
# ============================================================


_productivity_execute_before_summary_coal_tons_v33 = execute


def _productivity_v33_num(
    value,
):

    try:

        return float(
            str(
                value
                or 0
            ).replace(
                ",",
                "",
            )
        )

    except (
        TypeError,
        ValueError,
    ):

        return 0.0


def _productivity_v33_visible_integers(
    values,
):

    values = [
        max(
            0.0,
            _productivity_v33_num(
                value
            ),
        )
        for value in values
    ]

    if not values:

        return []

    target = int(
        round(
            sum(
                values
            )
        )
    )

    result = [
        int(
            value
        )
        for value in values
    ]

    remaining = (
        target
        - sum(
            result
        )
    )

    fractions = [
        (
            values[index]
            - result[index],
            index,
        )
        for index in range(
            len(values)
        )
    ]

    fractions.sort(
        key=lambda item: (
            item[0],
            -item[1],
        ),
        reverse=True,
    )

    for _fraction, index in fractions:

        if remaining <= 0:
            break

        result[index] += 1
        remaining -= 1

    if (
        remaining > 0
        and result
    ):

        result[-1] += (
            remaining
        )

    return result


def _productivity_v33_section_rows(
    rows,
):

    result = []

    section = 0
    category = ""

    categories = (
        "Excavator",
        "ADT",
        "Dozer",
    )

    for index, row in enumerate(
        rows
        or []
    ):

        if not hasattr(
            row,
            "get",
        ):

            result.append({
                "index":
                    index,

                "section":
                    section,

                "category":
                    category,

                "row":
                    row,
            })

            continue

        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()

        if (
            int(
                row.get(
                    "is_monthly_plan_header"
                )
                or 0
            ) == 1
            or label.startswith(
                "MONTHLY PRODUCTION:"
            )
        ):

            section += 1
            category = ""

        elif label in categories:

            category = label

        result.append({
            "index":
                index,

            "section":
                section,

            "category":
                category,

            "row":
                row,
        })

    return result


def _productivity_v33_pre_v28_coal_tonnes(
    filters,
):

    # V28 captured the report before it converted Coal back
    # to BCM. That pre-V28 result still contains V19 tonnes.
    try:

        source = (
            _productivity_execute_before_bcm_headings_hd_v28(
                filters
            )
        )

    except Exception:

        return {}

    if not source or len(
        source
    ) < 2:

        return {}

    queues = {}

    for info in (
        _productivity_v33_section_rows(
            source[1]
            or []
        )
    ):

        row = info[
            "row"
        ]

        if not hasattr(
            row,
            "get",
        ):

            continue

        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()

        if material != "Coal":

            continue

        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()

        if not label:

            continue

        key = (
            info[
                "section"
            ],
            info[
                "category"
            ],
            label,
        )

        tonnes = (
            _productivity_v33_num(
                row.get(
                    "output"
                )
            )
        )

        queues.setdefault(
            key,
            []
        ).append(
            tonnes
        )

    return queues


def _productivity_apply_summary_coal_tons_v33(
    result,
    filters,
):

    if not result:

        return result

    filters = frappe._dict(
        filters
        or {}
    )

    basis = str(
        filters.get(
            "bcm_basis"
        )
        or ""
    ).strip().lower()

    view = str(
        filters.get(
            "summary_view"
        )
        or ""
    ).strip().lower()

    if (
        not basis.startswith(
            "actual"
        )
        or view
        != "summary per machine"
    ):

        return result

    parts = list(
        result
    )

    if len(parts) < 2:

        return result

    # ========================================================
    # COLUMNS
    # ========================================================

    columns = []

    coal_column_exists = False

    for original in (
        parts[0]
        or []
    ):

        if not hasattr(
            original,
            "get",
        ):

            columns.append(
                original
            )

            continue

        column = dict(
            original
        )

        fieldname = str(
            column.get(
                "fieldname"
            )
            or ""
        ).strip()

        if fieldname == "coal_tons":

            coal_column_exists = True

        if fieldname in (
            "output",
            "adjusted_bcm",
        ):

            column[
                "label"
            ] = (
                "Output Actual (BCM)"
            )

            column[
                "precision"
            ] = 0

        elif (
            fieldname
            == "productivity"
        ):

            column[
                "label"
            ] = (
                "Productivity (BCM/HR)"
            )

            column[
                "precision"
            ] = 0

        columns.append(
            column
        )

        if (
            fieldname
            in (
                "output",
                "adjusted_bcm",
            )
            and not coal_column_exists
        ):

            columns.append({
                "label":
                    "Coal Tons",

                "fieldname":
                    "coal_tons",

                "fieldtype":
                    "Float",

                "precision":
                    0,

                "width":
                    110,
            })

            coal_column_exists = True

    parts[
        0
    ] = columns

    # ========================================================
    # TONNES FROM PRE-V28 V19 RESULT
    # ========================================================

    tonnes_queues = (
        _productivity_v33_pre_v28_coal_tonnes(
            filters
        )
    )

    queue_positions = {}

    rows = [
        dict(
            row
        )
        if hasattr(
            row,
            "get",
        )
        else row

        for row in (
            parts[1]
            or []
        )
    ]

    annotated = (
        _productivity_v33_section_rows(
            rows
        )
    )

    # ========================================================
    # PASS 1:
    # RESTORE COAL MATERIAL ROW BCM + ADD COAL TONS
    # ========================================================

    for info in annotated:

        row = info[
            "row"
        ]

        if not hasattr(
            row,
            "get",
        ):

            continue

        row[
            "coal_tons"
        ] = ""

        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()

        if material != "Coal":

            continue

        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()

        # Original BCM saved by V19.
        coal_bcm = (
            _productivity_v33_num(
                row.get(
                    "productivity_coal_bcm_v19"
                )
            )
        )

        if coal_bcm > 0:

            row[
                "output"
            ] = coal_bcm

            if (
                "adjusted_bcm"
                in row
            ):

                row[
                    "adjusted_bcm"
                ] = coal_bcm

        key = (
            info[
                "section"
            ],
            info[
                "category"
            ],
            label,
        )

        position = (
            queue_positions.get(
                key,
                0,
            )
        )

        queue = (
            tonnes_queues.get(
                key
            )
            or []
        )

        tonnes = 0.0

        if position < len(
            queue
        ):

            tonnes = queue[
                position
            ]

            queue_positions[
                key
            ] = (
                position + 1
            )

        row[
            "coal_tons"
        ] = tonnes

        hours = (
            _productivity_v33_num(
                row.get(
                    "working_hours"
                )
            )
        )

        if (
            coal_bcm > 0
            and hours > 0
        ):

            row[
                "productivity"
            ] = round(
                coal_bcm
                / hours,
                0,
            )

        row[
            "productivity_output_unit_v19"
        ] = "BCM"

        row[
            "productivity_summary_coal_tons_v33"
        ] = 1

    # ========================================================
    # PASS 2:
    # BUILD MACHINE MATERIAL GROUPS
    # ========================================================

    groups = {}

    for info in annotated:

        row = info[
            "row"
        ]

        if not hasattr(
            row,
            "get",
        ):

            continue

        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()

        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()

        category = info[
            "category"
        ]

        if not (
            category
            and label
            and material
        ):

            continue

        key = (
            info[
                "section"
            ],
            category,
            label,
        )

        groups.setdefault(
            key,
            []
        ).append(
            row
        )

    # ========================================================
    # PASS 3:
    # VISIBLE INTEGER BCM MATERIAL VALUES
    #
    # Makes manual addition exactly equal visible machine total.
    # ========================================================

    machine_values = {}

    for key, children in groups.items():

        exact_outputs = [
            _productivity_v33_num(
                child.get(
                    "output"
                )
            )
            for child in children
        ]

        visible_outputs = (
            _productivity_v33_visible_integers(
                exact_outputs
            )
        )

        coal_tons_total = 0.0
        hours_total = 0.0

        for child, visible_output in zip(
            children,
            visible_outputs,
        ):

            child[
                "productivity_exact_bcm_v33"
            ] = (
                _productivity_v33_num(
                    child.get(
                        "output"
                    )
                )
            )

            child[
                "output"
            ] = visible_output

            if (
                "adjusted_bcm"
                in child
            ):

                child[
                    "adjusted_bcm"
                ] = visible_output

            hours = (
                _productivity_v33_num(
                    child.get(
                        "working_hours"
                    )
                )
            )

            hours_total += hours

            if hours > 0:

                child[
                    "productivity"
                ] = round(
                    visible_output
                    / hours,
                    0,
                )

            tonnes = (
                _productivity_v33_num(
                    child.get(
                        "coal_tons"
                    )
                )
            )

            coal_tons_total += tonnes

            if tonnes > 0:

                child[
                    "coal_tons"
                ] = int(
                    round(
                        tonnes
                    )
                )

            else:

                child[
                    "coal_tons"
                ] = ""

        machine_values[
            key
        ] = {
            "output":
                sum(
                    visible_outputs
                ),

            "coal_tons":
                int(
                    round(
                        coal_tons_total
                    )
                ),

            "hours":
                hours_total,
        }

    # ========================================================
    # PASS 4:
    # MACHINE TOTAL ROWS
    # ========================================================

    category_machine_values = {}

    for info in annotated:

        row = info[
            "row"
        ]

        if not hasattr(
            row,
            "get",
        ):

            continue

        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()

        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()

        category = info[
            "category"
        ]

        if (
            not category
            or not label
            or material
            or label
            in (
                "Excavator",
                "ADT",
                "Dozer",
                "Total Fleet",
            )
        ):

            continue

        key = (
            info[
                "section"
            ],
            category,
            label,
        )

        value = (
            machine_values.get(
                key
            )
        )

        if not value:

            continue

        row[
            "output"
        ] = value[
            "output"
        ]

        if (
            "adjusted_bcm"
            in row
        ):

            row[
                "adjusted_bcm"
            ] = value[
                "output"
            ]

        row[
            "coal_tons"
        ] = (
            value[
                "coal_tons"
            ]
            if value[
                "coal_tons"
            ] > 0
            else ""
        )

        hours = (
            _productivity_v33_num(
                row.get(
                    "working_hours"
                )
            )
        )

        if hours > 0:

            row[
                "productivity"
            ] = round(
                value[
                    "output"
                ]
                / hours,
                0,
            )

        row[
            "productivity_summary_machine_total_v33"
        ] = 1

        category_key = (
            info[
                "section"
            ],
            category,
        )

        category_machine_values.setdefault(
            category_key,
            []
        ).append(
            value
        )

    # ========================================================
    # PASS 5:
    # CATEGORY TOTALS
    # ========================================================

    category_totals = {}

    for (
        category_key,
        values
    ) in category_machine_values.items():

        category_totals[
            category_key
        ] = {
            "output":
                sum(
                    value[
                        "output"
                    ]
                    for value in values
                ),

            "coal_tons":
                sum(
                    value[
                        "coal_tons"
                    ]
                    for value in values
                ),
        }

    for info in annotated:

        row = info[
            "row"
        ]

        if not hasattr(
            row,
            "get",
        ):

            continue

        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()

        if label not in (
            "Excavator",
            "ADT",
            "Dozer",
        ):

            continue

        value = category_totals.get(
            (
                info[
                    "section"
                ],
                label,
            )
        )

        if not value:

            continue

        row[
            "output"
        ] = value[
            "output"
        ]

        if (
            "adjusted_bcm"
            in row
        ):

            row[
                "adjusted_bcm"
            ] = value[
                "output"
            ]

        row[
            "coal_tons"
        ] = (
            value[
                "coal_tons"
            ]
            if value[
                "coal_tons"
            ] > 0
            else ""
        )

        hours = (
            _productivity_v33_num(
                row.get(
                    "working_hours"
                )
            )
        )

        if hours > 0:

            row[
                "productivity"
            ] = round(
                value[
                    "output"
                ]
                / hours,
                0,
            )

    # ========================================================
    # PASS 6:
    # TOTAL FLEET = EXCAVATOR + DOZER
    #
    # ADT is duplicate T&S production, therefore not added.
    # ========================================================

    for info in annotated:

        row = info[
            "row"
        ]

        if not hasattr(
            row,
            "get",
        ):

            continue

        if str(
            row.get(
                "label"
            )
            or ""
        ).strip() != "Total Fleet":

            continue

        section = info[
            "section"
        ]

        excavator = (
            category_totals.get(
                (
                    section,
                    "Excavator",
                )
            )
            or {
                "output":
                    0,

                "coal_tons":
                    0,
            }
        )

        dozer = (
            category_totals.get(
                (
                    section,
                    "Dozer",
                )
            )
            or {
                "output":
                    0,

                "coal_tons":
                    0,
            }
        )

        fleet_output = (
            excavator[
                "output"
            ]
            + dozer[
                "output"
            ]
        )

        fleet_coal = (
            excavator[
                "coal_tons"
            ]
            + dozer[
                "coal_tons"
            ]
        )

        row[
            "output"
        ] = fleet_output

        if (
            "adjusted_bcm"
            in row
        ):

            row[
                "adjusted_bcm"
            ] = fleet_output

        row[
            "coal_tons"
        ] = (
            fleet_coal
            if fleet_coal > 0
            else ""
        )

        hours = (
            _productivity_v33_num(
                row.get(
                    "working_hours"
                )
            )
        )

        if hours > 0:

            row[
                "productivity"
            ] = round(
                fleet_output
                / hours,
                0,
            )

    parts[
        1
    ] = rows

    if isinstance(
        result,
        tuple,
    ):

        return tuple(
            parts
        )

    return parts


def execute(filters=None):

    result = (
        _productivity_execute_before_summary_coal_tons_v33(
            filters
        )
    )

    return (
        _productivity_apply_summary_coal_tons_v33(
            result,
            filters,
        )
    )


# END KOSI_PRODUCTIVITY_SUMMARY_COAL_TONS_V33


# ============================================================
# KOSI_PRODUCTIVITY_SUMMARY_MATERIAL_REF_V34
#
# ACTUAL BCMs / SUMMARY PER MACHINE ONLY
#
# Adds exact Survey Material Type Ref rows beneath each
# machine/material row.
#
# Example:
#
# ADT01
#   Coal
#       2COAL - LOAD & HAUL
#       4LCOAL - LOAD & HAUL
#       4LCOAL - LOAD & HAUL
#
#   Hards
#       WASTE (HARDS) - LOAD & HAUL
#       WASTE (HARDS) - LOAD & HAUL
#
#   Softs
#       TOPSOIL (SOFTS) - LOAD & HAUL
#       WASTE (SOFTS) - LOAD & HAUL
#
#
# IMPORTANT:
#
# Survey Material Type Ref rows are NOT directly linked to
# individual machine production.
#
# Therefore each machine/material parent is distributed using
# the exact Survey proportions:
#
#     Child BCM =
#         Parent Machine Material BCM
#         x Survey Ref BCM Share
#
#     Child Hours =
#         Parent Machine Material Hours
#         x Survey Ref BCM Share
#
#     Child Coal Tons =
#         Parent Machine Coal Tons
#         x Survey Ref Metric-Tonne Share
#
# Exact duplicate Survey rows remain separate by Survey IDX.
#
# Parent machine/material values are unchanged.
# Machine totals are unchanged.
#
# Child area/haul fields remain blank because Survey ref rows
# have no machine-specific route linkage.
# ============================================================


_productivity_execute_before_summary_material_ref_v34 = execute


def _productivity_v34_num(
    value,
):

    try:

        return float(
            str(
                value
                or 0
            ).replace(
                ",",
                "",
            )
        )

    except (
        TypeError,
        ValueError,
    ):

        return 0.0


def _productivity_v34_handling(
    value,
):

    import re

    normalized = re.sub(
        r"[^a-z]",
        "",
        str(
            value
            or ""
        ).lower(),
    )

    if normalized == "truckandshovel":

        return "Truck and Shovel"

    if normalized == "dozing":

        return "Dozing"

    return str(
        value
        or ""
    ).strip()


def _productivity_v34_refs(
    survey_name,
    category,
    material,
    cache,
):

    survey_name = str(
        survey_name
        or ""
    ).strip()

    category = str(
        category
        or ""
    ).strip()

    material = str(
        material
        or ""
    ).strip()

    canonical_material = (
        _productivity_material_v15(
            material,
            material,
        )
    )

    handling = (
        "Dozing"
        if category == "Dozer"
        else "Truck and Shovel"
    )

    cache_key = (
        survey_name,
        category,
        canonical_material,
    )

    if cache_key in cache:

        return cache[
            cache_key
        ]

    result = []

    if not survey_name:

        cache[
            cache_key
        ] = result

        return result

    try:

        survey = frappe.get_doc(
            "Survey",
            survey_name,
        )

    except Exception:

        cache[
            cache_key
        ] = result

        return result

    for source_row in (
        survey.get(
            "surveyed_values"
        )
        or []
    ):

        source_material = (
            _productivity_material_v15(
                source_row.get(
                    "mat_type"
                ),
                source_row.get(
                    "mat_type_ref"
                ),
            )
        )

        if (
            source_material
            != canonical_material
        ):

            continue

        source_handling = (
            _productivity_v34_handling(
                source_row.get(
                    "handling_method"
                )
            )
        )

        if (
            source_handling
            != handling
        ):

            continue

        bcm = (
            _productivity_v34_num(
                source_row.get(
                    "bcm"
                )
            )
        )

        tonnes = (
            _productivity_v34_num(
                source_row.get(
                    "metric_tonnes"
                )
            )
        )

        if (
            bcm <= 0
            and tonnes <= 0
        ):

            continue

        material_ref = str(
            source_row.get(
                "mat_type_ref"
            )
            or source_row.get(
                "mat_type"
            )
            or canonical_material
        ).strip()

        result.append({
            "idx":
                int(
                    source_row.get(
                        "idx"
                    )
                    or 0
                ),

            "material":
                canonical_material,

            "material_ref":
                material_ref,

            "bcm":
                bcm,

            "metric_tonnes":
                tonnes,

            "handling":
                source_handling,
        })

    result.sort(
        key=lambda row: (
            row[
                "idx"
            ]
        )
    )

    cache[
        cache_key
    ] = result

    return result


def _productivity_v34_integer_allocate(
    total,
    weights,
):

    total = int(
        round(
            _productivity_v34_num(
                total
            )
        )
    )

    weights = [
        max(
            0.0,
            _productivity_v34_num(
                weight
            ),
        )
        for weight in weights
    ]

    if not weights:

        return []

    weight_total = sum(
        weights
    )

    if (
        total <= 0
        or weight_total <= 0
    ):

        return [
            0
            for weight in weights
        ]

    exact = [
        total
        * (
            weight
            / weight_total
        )
        for weight in weights
    ]

    result = [
        int(
            value
        )
        for value in exact
    ]

    remaining = (
        total
        - sum(
            result
        )
    )

    fractions = [
        (
            exact[index]
            - result[index],
            index,
        )
        for index in range(
            len(result)
        )
    ]

    fractions.sort(
        key=lambda item: (
            item[0],
            -item[1],
        ),
        reverse=True,
    )

    for _fraction, index in fractions:

        if remaining <= 0:
            break

        result[index] += 1
        remaining -= 1

    if (
        remaining > 0
        and result
    ):

        result[-1] += (
            remaining
        )

    return result


def _productivity_v34_hours_allocate(
    total_hours,
    weights,
):

    total_hours = (
        _productivity_v34_num(
            total_hours
        )
    )

    weights = [
        max(
            0.0,
            _productivity_v34_num(
                weight
            ),
        )
        for weight in weights
    ]

    if not weights:

        return []

    weight_total = sum(
        weights
    )

    if (
        total_hours <= 0
        or weight_total <= 0
    ):

        return [
            0.0
            for weight in weights
        ]

    result = []

    allocated = 0.0

    for index, weight in enumerate(
        weights
    ):

        if index == (
            len(weights) - 1
        ):

            hours = round(
                total_hours
                - allocated,
                3,
            )

        else:

            hours = round(
                total_hours
                * (
                    weight
                    / weight_total
                ),
                3,
            )

            allocated += (
                hours
            )

        result.append(
            max(
                0.0,
                hours,
            )
        )

    return result


def _productivity_apply_summary_material_ref_v34(
    result,
    filters,
):

    if not result:

        return result

    filters = frappe._dict(
        filters
        or {}
    )

    basis = str(
        filters.get(
            "bcm_basis"
        )
        or ""
    ).strip().lower()

    view = str(
        filters.get(
            "summary_view"
        )
        or ""
    ).strip().lower()

    if (
        not basis.startswith(
            "actual"
        )
        or view
        != "summary per machine"
    ):

        return result

    parts = list(
        result
    )

    if len(parts) < 2:

        return result

    rows = list(
        parts[1]
        or []
    )

    output_rows = []

    current_category = ""
    current_survey = ""

    survey_cache = {}

    categories = (
        "Excavator",
        "ADT",
        "Dozer",
    )

    for original in rows:

        if not hasattr(
            original,
            "get",
        ):

            output_rows.append(
                original
            )

            continue

        row = dict(
            original
        )

        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()

        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()

        survey_marker = str(
            row.get(
                "productivity_coal_tonnes_survey_v19"
            )
            or ""
        ).strip()

        if survey_marker:

            current_survey = (
                survey_marker
            )

        if label in categories:

            current_category = (
                label
            )

        output_rows.append(
            row
        )

        # ----------------------------------------------------
        # Only machine MATERIAL rows.
        #
        # Machine total:
        #     label = machine
        #     material blank
        #
        # Machine material:
        #     label = machine
        #     material Coal / Hards / Softs
        # ----------------------------------------------------

        if not (
            current_category
            and current_survey
            and label
            and material
        ):

            continue

        if row.get(
            "productivity_summary_survey_ref_v34"
        ):

            continue

        refs = (
            _productivity_v34_refs(
                current_survey,
                current_category,
                material,
                survey_cache,
            )
        )

        if not refs:

            continue

        parent_output = (
            _productivity_v34_num(
                row.get(
                    "output"
                )
            )
        )

        parent_hours = (
            _productivity_v34_num(
                row.get(
                    "working_hours"
                )
            )
        )

        parent_coal_tons = (
            _productivity_v34_num(
                row.get(
                    "coal_tons"
                )
            )
        )

        bcm_weights = [
            ref[
                "bcm"
            ]
            for ref in refs
        ]

        child_outputs = (
            _productivity_v34_integer_allocate(
                parent_output,
                bcm_weights,
            )
        )

        child_hours = (
            _productivity_v34_hours_allocate(
                parent_hours,
                bcm_weights,
            )
        )

        if (
            _productivity_material_v15(
                material,
                material,
            )
            == "Coal"
        ):

            tonne_weights = [
                ref[
                    "metric_tonnes"
                ]
                for ref in refs
            ]

            child_tonnes = (
                _productivity_v34_integer_allocate(
                    parent_coal_tons,
                    tonne_weights,
                )
            )

        else:

            child_tonnes = [
                0
                for ref in refs
            ]

        try:

            parent_indent = int(
                row.get(
                    "indent"
                )
                or 1
            )

        except Exception:

            parent_indent = 1

        child_indent = (
            parent_indent + 1
        )

        for index, ref in enumerate(
            refs
        ):

            output = (
                child_outputs[
                    index
                ]
            )

            hours = (
                child_hours[
                    index
                ]
            )

            coal_tons = (
                child_tonnes[
                    index
                ]
            )

            productivity = (
                round(
                    output
                    / hours,
                    0,
                )
                if hours > 0
                else 0
            )

            child = {
                "label":
                    ref[
                        "material_ref"
                    ],

                "working_hours":
                    hours,

                "output":
                    output,

                "coal_tons":
                    (
                        coal_tons
                        if coal_tons > 0
                        else ""
                    ),

                "productivity":
                    productivity,

                "productivity_bcm_hd":
                    "",

                "material":
                    ref[
                        "material_ref"
                    ],

                "from_area":
                    "",

                "to_area":
                    "",

                "hauling_distance_m":
                    "",

                "indent":
                    child_indent,

                "style":
                    "color:#555;",

                "productivity_summary_survey_ref_v34":
                    1,

                "productivity_summary_parent_machine_v34":
                    label,

                "productivity_summary_parent_material_v34":
                    material,

                "productivity_summary_category_v34":
                    current_category,

                "productivity_summary_survey_name_v34":
                    current_survey,

                "productivity_summary_survey_idx_v34":
                    ref[
                        "idx"
                    ],

                "productivity_summary_allocation_v34":
                    "Proportional Survey Material Type Ref allocation",
            }

            output_rows.append(
                child
            )

    parts[
        1
    ] = output_rows

    if isinstance(
        result,
        tuple,
    ):

        return tuple(
            parts
        )

    return parts


def execute(filters=None):

    result = (
        _productivity_execute_before_summary_material_ref_v34(
            filters
        )
    )

    return (
        _productivity_apply_summary_material_ref_v34(
            result,
            filters,
        )
    )


# END KOSI_PRODUCTIVITY_SUMMARY_MATERIAL_REF_V34


# ============================================================
# KOSI_PRODUCTIVITY_SUMMARY_CAPTURED_DETAIL_V36
#
# SUMMARY PER MACHINE
#
# Machine
#   Broad Material
#       Captured Geo / Material Layer
#
# Example:
#
# ADT01
#   Coal
#       Ramp 2 - 3 2#Coal
#       Ramp 1 - 2 2#Coal
#
# Source:
# Hourly Production -> Truck Loads
#
# Actual captured fields:
#   asset_name_shoval
#   asset_name_truck
#   mat_type
#   geo_mat_layer_truck
#   mining_areas_trucks
#   bcms
#
# NO Survey material allocation to individual machines.
# ============================================================


_productivity_execute_before_summary_captured_detail_v36 = execute


def _productivity_v36_num(value):

    try:

        return float(
            str(
                value
                or 0
            ).replace(
                ",",
                "",
            )
        )

    except (
        TypeError,
        ValueError,
    ):

        return 0.0


def _productivity_v36_selected_plans(filters):

    import json

    raw = (
        filters.get(
            "monthly_production_plans"
        )
        or filters.get(
            "monthly_production_plan"
        )
        or filters.get(
            "monthly_production_planning"
        )
        or []
    )

    if isinstance(
        raw,
        (
            list,
            tuple,
            set,
        ),
    ):

        values = list(
            raw
        )

    elif isinstance(
        raw,
        str,
    ):

        text = raw.strip()

        if not text:

            values = []

        elif text.startswith(
            "["
        ):

            try:

                parsed = json.loads(
                    text
                )

                values = (
                    parsed
                    if isinstance(
                        parsed,
                        list,
                    )
                    else [
                        parsed
                    ]
                )

            except Exception:

                values = [
                    text
                ]

        else:

            values = [
                text
            ]

    else:

        values = [
            raw
        ] if raw else []

    return [
        str(
            value
            or ""
        ).strip()

        for value in values

        if str(
            value
            or ""
        ).strip()
    ]


def _productivity_v36_plan_ranges(plans):

    result = {}

    for plan in plans:

        values = frappe.db.get_value(
            "Monthly Production Planning",
            plan,
            [
                "prod_month_start_date",
                "prod_month_end_date",
            ],
            as_dict=True,
        )

        if not values:

            continue

        start = values.get(
            "prod_month_start_date"
        )

        end = values.get(
            "prod_month_end_date"
        )

        if (
            start
            and end
        ):

            result[
                (
                    str(
                        start
                    ),
                    str(
                        end
                    ),
                )
            ] = plan

    return result


def _productivity_v36_plan_from_header(
    label,
    plans,
    plan_ranges,
):

    import re
    from datetime import datetime

    if len(
        plans
    ) == 1:

        return plans[
            0
        ]

    label = str(
        label
        or ""
    ).strip()

    match = re.search(
        r"(\d{2}-\d{2}-\d{4})"
        r"\s+to\s+"
        r"(\d{2}-\d{2}-\d{4})",
        label,
    )

    if not match:

        return ""

    try:

        start = datetime.strptime(
            match.group(
                1
            ),
            "%d-%m-%Y",
        ).date()

        end = datetime.strptime(
            match.group(
                2
            ),
            "%d-%m-%Y",
        ).date()

    except Exception:

        return ""

    return (
        plan_ranges.get(
            (
                str(
                    start
                ),
                str(
                    end
                ),
            )
        )
        or ""
    )


def _productivity_v36_source(
    filters,
):

    start_date = (
        filters.get(
            "start_date"
        )
        or filters.get(
            "from_date"
        )
    )

    end_date = (
        filters.get(
            "end_date"
        )
        or filters.get(
            "to_date"
        )
    )

    site = str(
        filters.get(
            "site"
        )
        or filters.get(
            "location"
        )
        or ""
    ).strip()

    shift = str(
        filters.get(
            "shift"
        )
        or ""
    ).strip()

    plans = (
        _productivity_v36_selected_plans(
            filters
        )
    )

    if not (
        start_date
        and end_date
    ):

        return (
            {},
            plans,
        )

    conditions = [
        "hp.prod_date BETWEEN %(v36_start)s AND %(v36_end)s",
        "hp.docstatus < 2",
    ]

    values = {
        "v36_start":
            start_date,

        "v36_end":
            end_date,
    }

    if site:

        conditions.append(
            "hp.location = %(v36_site)s"
        )

        values[
            "v36_site"
        ] = site

    if shift:

        conditions.append(
            "hp.shift = %(v36_shift)s"
        )

        values[
            "v36_shift"
        ] = shift

    if plans:

        placeholders = []

        for index, plan in enumerate(
            plans
        ):

            key = (
                f"v36_plan_{index}"
            )

            placeholders.append(
                f"%({key})s"
            )

            values[
                key
            ] = plan

        conditions.append(
            "hp.month_prod_planning IN ("
            + ", ".join(
                placeholders
            )
            + ")"
        )

    where_sql = (
        " AND ".join(
            conditions
        )
    )

    rows = frappe.db.sql(
        f"""
        SELECT
            hp.month_prod_planning AS plan,

            tl.asset_name_shoval AS excavator,

            tl.asset_name_truck AS adt,

            COALESCE(
                NULLIF(
                    TRIM(tl.mat_type),
                    ''
                ),
                'Unassigned'
            ) AS material,

            COALESCE(
                NULLIF(
                    TRIM(tl.geo_mat_layer_truck),
                    ''
                ),
                'Unassigned'
            ) AS material_detail,

            COALESCE(
                NULLIF(
                    TRIM(tl.mining_areas_trucks),
                    ''
                ),
                ''
            ) AS from_area,

            SUM(
                COALESCE(
                    tl.bcms,
                    0
                )
            ) AS captured_bcm

        FROM `tabHourly Production` hp

        INNER JOIN `tabTruck Loads` tl
            ON tl.parent = hp.name

        WHERE
            {where_sql}

        GROUP BY
            hp.month_prod_planning,

            tl.asset_name_shoval,

            tl.asset_name_truck,

            COALESCE(
                NULLIF(
                    TRIM(tl.mat_type),
                    ''
                ),
                'Unassigned'
            ),

            COALESCE(
                NULLIF(
                    TRIM(tl.geo_mat_layer_truck),
                    ''
                ),
                'Unassigned'
            ),

            COALESCE(
                NULLIF(
                    TRIM(tl.mining_areas_trucks),
                    ''
                ),
                ''
            )

        ORDER BY
            hp.month_prod_planning,
            material,
            material_detail
        """,
        values,
        as_dict=True,
    )

    result = {}

    def add(
        plan,
        category,
        machine,
        material,
        detail,
        area,
        bcm,
    ):

        plan = str(
            plan
            or ""
        ).strip()

        category = str(
            category
            or ""
        ).strip()

        machine = str(
            machine
            or ""
        ).strip()

        material = str(
            material
            or ""
        ).strip()

        detail = str(
            detail
            or ""
        ).strip()

        area = str(
            area
            or ""
        ).strip()

        bcm = (
            _productivity_v36_num(
                bcm
            )
        )

        if not (
            category
            and machine
            and material
            and detail
        ):

            return

        if bcm <= 0:

            return

        key = (
            plan,
            category,
            machine,
            material,
        )

        detail_map = (
            result.setdefault(
                key,
                {},
            )
        )

        target = (
            detail_map.setdefault(
                detail,
                {
                    "bcm":
                        0.0,

                    "areas":
                        set(),
                },
            )
        )

        target[
            "bcm"
        ] += bcm

        if area:

            target[
                "areas"
            ].add(
                area
            )

    for source_row in rows:

        if str(
            source_row.excavator
            or ""
        ).strip():

            add(
                source_row.plan,
                "Excavator",
                source_row.excavator,
                source_row.material,
                source_row.material_detail,
                source_row.from_area,
                source_row.captured_bcm,
            )

        if str(
            source_row.adt
            or ""
        ).strip():

            add(
                source_row.plan,
                "ADT",
                source_row.adt,
                source_row.material,
                source_row.material_detail,
                source_row.from_area,
                source_row.captured_bcm,
            )

    return (
        result,
        plans,
    )


def _productivity_v36_get_details(
    source,
    plans,
    current_plan,
    category,
    machine,
    material,
):

    current_plan = str(
        current_plan
        or ""
    ).strip()

    category = str(
        category
        or ""
    ).strip()

    machine = str(
        machine
        or ""
    ).strip()

    material = str(
        material
        or ""
    ).strip()

    if not (
        category
        and machine
        and material
    ):

        return {}

    if current_plan:

        exact = source.get(
            (
                current_plan,
                category,
                machine,
                material,
            )
        )

        if exact:

            return exact

    if len(
        plans
    ) == 1:

        return (
            source.get(
                (
                    plans[
                        0
                    ],
                    category,
                    machine,
                    material,
                )
            )
            or {}
        )

    # Never combine different selected monthly plans into
    # one machine/material breakdown.
    if len(
        plans
    ) > 1:

        return {}

    merged = {}

    for (
        _plan,
        source_category,
        source_machine,
        source_material,
    ), detail_map in source.items():

        if (
            source_category
            != category
            or source_machine
            != machine
            or source_material
            != material
        ):

            continue

        for detail, data in (
            detail_map.items()
        ):

            target = (
                merged.setdefault(
                    detail,
                    {
                        "bcm":
                            0.0,

                        "areas":
                            set(),
                    },
                )
            )

            target[
                "bcm"
            ] += (
                _productivity_v36_num(
                    data.get(
                        "bcm"
                    )
                )
            )

            target[
                "areas"
            ].update(
                data.get(
                    "areas"
                )
                or set()
            )

    return merged


def _productivity_v36_split_hours(
    total_hours,
    weights,
):

    total_hours = (
        _productivity_v36_num(
            total_hours
        )
    )

    weights = [
        max(
            0.0,
            _productivity_v36_num(
                value
            ),
        )
        for value in weights
    ]

    if not weights:

        return []

    total_weight = sum(
        weights
    )

    if (
        total_hours <= 0
        or total_weight <= 0
    ):

        return [
            0.0
            for _ in weights
        ]

    result = []

    allocated = 0.0

    for index, weight in enumerate(
        weights
    ):

        if index == (
            len(
                weights
            )
            - 1
        ):

            value = round(
                total_hours
                - allocated,
                3,
            )

        else:

            value = round(
                total_hours
                * weight
                / total_weight,
                3,
            )

            allocated += (
                value
            )

        result.append(
            max(
                0.0,
                value,
            )
        )

    return result


def _productivity_apply_summary_captured_detail_v36(
    result,
    filters,
):

    if not result:

        return result

    filters = frappe._dict(
        filters
        or {}
    )

    basis = str(
        filters.get(
            "bcm_basis"
        )
        or ""
    ).strip().lower()

    view = str(
        filters.get(
            "summary_view"
        )
        or ""
    ).strip().lower()

    if (
        not basis.startswith(
            "actual"
        )
        or view
        != "summary per machine"
    ):

        return result

    parts = list(
        result
    )

    if len(
        parts
    ) < 2:

        return result

    source, plans = (
        _productivity_v36_source(
            filters
        )
    )

    if not source:

        return result

    plan_ranges = (
        _productivity_v36_plan_ranges(
            plans
        )
    )

    output_rows = []

    current_category = ""

    current_plan = (
        plans[
            0
        ]
        if len(
            plans
        ) == 1
        else ""
    )

    for original in (
        parts[
            1
        ]
        or []
    ):

        if not hasattr(
            original,
            "get",
        ):

            output_rows.append(
                original
            )

            continue

        row = dict(
            original
        )

        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()

        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()

        if label.startswith(
            "MONTHLY PRODUCTION:"
        ):

            current_category = ""

            resolved_plan = (
                _productivity_v36_plan_from_header(
                    label,
                    plans,
                    plan_ranges,
                )
            )

            if resolved_plan:

                current_plan = (
                    resolved_plan
                )

            output_rows.append(
                row
            )

            continue

        if label in (
            "Excavator",
            "ADT",
            "Dozer",
        ):

            current_category = (
                label
            )

            output_rows.append(
                row
            )

            continue

        output_rows.append(
            row
        )

        # Dozer already uses captured dozer geo material
        # in the existing machine/material logic.
        if current_category not in (
            "Excavator",
            "ADT",
        ):

            continue

        # Machine total row.
        if not material:

            continue

        machine = label

        details = (
            _productivity_v36_get_details(
                source,
                plans,
                current_plan,
                current_category,
                machine,
                material,
            )
        )

        if not details:

            continue

        ordered_details = sorted(
            details.items(),
            key=lambda item:
                item[
                    0
                ].lower(),
        )

        weights = [
            _productivity_v36_num(
                data.get(
                    "bcm"
                )
            )
            for _detail, data
            in ordered_details
        ]

        if sum(
            weights
        ) <= 0:

            continue

        child_hours = (
            _productivity_v36_split_hours(
                row.get(
                    "working_hours"
                ),
                weights,
            )
        )

        row[
            "productivity_summary_material_parent_v36"
        ] = 1

        output_rows[
            -1
        ] = row

        try:

            parent_indent = int(
                row.get(
                    "indent"
                )
                or 1
            )

        except Exception:

            parent_indent = 1

        for index, (
            detail,
            detail_data,
        ) in enumerate(
            ordered_details
        ):

            # Actual captured BCM for this exact
            # Geo / Material Layer.
            bcm = int(
                round(
                    _productivity_v36_num(
                        detail_data.get(
                            "bcm"
                        )
                    )
                )
            )

            hours = (
                child_hours[
                    index
                ]
            )

            productivity = (
                round(
                    bcm
                    / hours,
                    0,
                )
                if hours > 0
                else 0
            )

            areas = sorted(
                {
                    str(
                        area
                        or ""
                    ).strip()
                    for area in (
                        detail_data.get(
                            "areas"
                        )
                        or set()
                    )
                    if str(
                        area
                        or ""
                    ).strip()
                }
            )

            child = {
                "label":
                    detail,

                "working_hours":
                    hours,

                "output":
                    bcm,

                "productivity":
                    productivity,

                "productivity_bcm_hd":
                    "",

                "material":
                    detail,

                "from_area":
                    " / ".join(
                        areas
                    ),

                "to_area":
                    "",

                "hauling_distance_m":
                    "",

                "indent":
                    parent_indent
                    + 1,

                "style":
                    "color:#555;",

                "productivity_summary_captured_detail_v36":
                    1,

                "productivity_summary_parent_machine_v36":
                    machine,

                "productivity_summary_parent_material_v36":
                    material,

                "productivity_summary_category_v36":
                    current_category,

                "productivity_summary_plan_v36":
                    current_plan,

                "productivity_summary_source_v36":
                    "Truck Loads.geo_mat_layer_truck",
            }

            output_rows.append(
                child
            )

    parts[
        1
    ] = output_rows

    if isinstance(
        result,
        tuple,
    ):

        return tuple(
            parts
        )

    return parts


def execute(filters=None):

    result = (
        _productivity_execute_before_summary_captured_detail_v36(
            filters
        )
    )

    return (
        _productivity_apply_summary_captured_detail_v36(
            result,
            filters,
        )
    )


# END KOSI_PRODUCTIVITY_SUMMARY_CAPTURED_DETAIL_V36


# ============================================================
# KOSI_PRODUCTIVITY_SUMMARY_CAPTURED_TOTALS_V37
#
# SUMMARY PER MACHINE / ACTUAL BCMs
#
# V36 added the exact captured Truck Loads material detail.
#
# V37 now makes the Summary Per Machine parent values reconcile
# to that captured system data.
#
#
# Example:
#
# ADT01                         20,129 BCM
#     Coal                         112 BCM
#         captured detail          112 BCM
#
#     Hards                        192 BCM
#         captured detail          192 BCM
#
#     Softs                     19,825 BCM
#         captured detail       19,825 BCM
#
#
# RULES:
#
# Material Parent Output =
#     Sum of captured V36 detail BCM
#
# Machine Output =
#     Sum of machine material parent BCM
#
# Category Output =
#     Sum of machine outputs
#
# Total Fleet =
#     Excavator + Dozer
#
# ADT is NOT added again to Total Fleet.
#
#
# Working Hours:
#
# Existing Pre-Use based hours stay unchanged.
#
#
# Productivity:
#
#     BCM / Working Hours
#
#
# Productivity BCM/HD:
#
#     BCM / midpoint of Hauling Distance
#
# Example:
#     500-1000 => midpoint 750
#
#
# Hours and Material unchanged.
# Tallies unchanged.
# ============================================================


_productivity_execute_before_summary_captured_totals_v37 = execute


def _productivity_v37_num(value):

    try:

        return float(
            str(
                value
                or 0
            ).replace(
                ",",
                "",
            )
        )

    except (
        TypeError,
        ValueError,
    ):

        return 0.0


def _productivity_v37_distance_midpoint(value):

    import re

    text = str(
        value
        or ""
    ).strip()

    if not text:

        return 0.0

    numbers = re.findall(
        r"\d+(?:\.\d+)?",
        text,
    )

    values = [
        float(
            number
        )
        for number in numbers
    ]

    if len(
        values
    ) >= 2:

        return (
            values[
                0
            ]
            + values[
                1
            ]
        ) / 2.0

    if len(
        values
    ) == 1:

        return values[
            0
        ]

    return 0.0


def _productivity_v37_set_output(
    row,
    output,
    update_hd=False,
):

    output = int(
        round(
            _productivity_v37_num(
                output
            )
        )
    )

    row[
        "output"
    ] = output

    if (
        "adjusted_bcm"
        in row
    ):

        row[
            "adjusted_bcm"
        ] = output

    hours = (
        _productivity_v37_num(
            row.get(
                "working_hours"
            )
        )
    )

    row[
        "productivity"
    ] = (
        round(
            output
            / hours,
            0,
        )
        if hours > 0
        else 0
    )

    if update_hd:

        distance = (
            _productivity_v37_distance_midpoint(
                row.get(
                    "hauling_distance_m"
                )
            )
        )

        if distance > 0:

            row[
                "productivity_bcm_hd"
            ] = round(
                output
                / distance,
                2,
            )

        else:

            row[
                "productivity_bcm_hd"
            ] = ""

    row[
        "productivity_summary_captured_totals_v37"
    ] = 1


def _productivity_v37_annotations(rows):

    result = []

    section = 0
    category = ""

    categories = (
        "Excavator",
        "ADT",
        "Dozer",
    )

    for index, row in enumerate(
        rows
        or []
    ):

        if not hasattr(
            row,
            "get",
        ):

            result.append({
                "index":
                    index,

                "section":
                    section,

                "category":
                    category,

                "row":
                    row,
            })

            continue

        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()

        if (
            int(
                row.get(
                    "is_monthly_plan_header"
                )
                or 0
            ) == 1
            or label.startswith(
                "MONTHLY PRODUCTION:"
            )
        ):

            section += 1
            category = ""

        elif label in categories:

            category = label

        result.append({
            "index":
                index,

            "section":
                section,

            "category":
                category,

            "row":
                row,
        })

    return result


def _productivity_apply_summary_captured_totals_v37(
    result,
    filters,
):

    if not result:

        return result

    filters = frappe._dict(
        filters
        or {}
    )

    basis = str(
        filters.get(
            "bcm_basis"
        )
        or ""
    ).strip().lower()

    view = str(
        filters.get(
            "summary_view"
        )
        or ""
    ).strip().lower()

    if (
        not basis.startswith(
            "actual"
        )
        or view
        != "summary per machine"
    ):

        return result

    parts = list(
        result
    )

    if len(
        parts
    ) < 2:

        return result

    rows = [
        dict(
            row
        )
        if hasattr(
            row,
            "get",
        )
        else row

        for row in (
            parts[
                1
            ]
            or []
        )
    ]

    annotated = (
        _productivity_v37_annotations(
            rows
        )
    )

    # ========================================================
    # 1. EXACT CAPTURED DETAIL TOTALS
    # ========================================================

    detail_totals = {}

    for info in annotated:

        row = info[
            "row"
        ]

        if not hasattr(
            row,
            "get",
        ):

            continue

        if not row.get(
            "productivity_summary_captured_detail_v36"
        ):

            continue

        category = str(
            row.get(
                "productivity_summary_category_v36"
            )
            or info[
                "category"
            ]
            or ""
        ).strip()

        machine = str(
            row.get(
                "productivity_summary_parent_machine_v36"
            )
            or ""
        ).strip()

        material = str(
            row.get(
                "productivity_summary_parent_material_v36"
            )
            or ""
        ).strip()

        if not (
            category
            and machine
            and material
        ):

            continue

        key = (
            info[
                "section"
            ],
            category,
            machine,
            material,
        )

        detail_totals[
            key
        ] = (
            detail_totals.get(
                key,
                0
            )
            + int(
                round(
                    _productivity_v37_num(
                        row.get(
                            "output"
                        )
                    )
                )
            )
        )

    # ========================================================
    # 2. MATERIAL PARENT = CAPTURED DETAIL SUM
    # ========================================================

    for info in annotated:

        row = info[
            "row"
        ]

        if not hasattr(
            row,
            "get",
        ):

            continue

        if row.get(
            "productivity_summary_captured_detail_v36"
        ):

            continue

        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()

        if not material:

            continue

        category = str(
            info[
                "category"
            ]
            or ""
        ).strip()

        machine = str(
            row.get(
                "label"
            )
            or ""
        ).strip()

        key = (
            info[
                "section"
            ],
            category,
            machine,
            material,
        )

        if key not in detail_totals:

            continue

        _productivity_v37_set_output(
            row,
            detail_totals[
                key
            ],
            update_hd=True,
        )

        row[
            "productivity_summary_material_parent_v36"
        ] = 1

    # ========================================================
    # 3. MACHINE TOTAL = SUM OF MATERIAL PARENTS
    # ========================================================

    machine_material_totals = {}

    for info in annotated:

        row = info[
            "row"
        ]

        if not hasattr(
            row,
            "get",
        ):

            continue

        if row.get(
            "productivity_summary_captured_detail_v36"
        ):

            continue

        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()

        if not material:

            continue

        category = str(
            info[
                "category"
            ]
            or ""
        ).strip()

        machine = str(
            row.get(
                "label"
            )
            or ""
        ).strip()

        if not (
            category
            and machine
        ):

            continue

        key = (
            info[
                "section"
            ],
            category,
            machine,
        )

        machine_material_totals[
            key
        ] = (
            machine_material_totals.get(
                key,
                0
            )
            + int(
                round(
                    _productivity_v37_num(
                        row.get(
                            "output"
                        )
                    )
                )
            )
        )

    machine_totals = {}

    categories = (
        "Excavator",
        "ADT",
        "Dozer",
    )

    for info in annotated:

        row = info[
            "row"
        ]

        if not hasattr(
            row,
            "get",
        ):

            continue

        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()

        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()

        category = str(
            info[
                "category"
            ]
            or ""
        ).strip()

        if (
            material
            or not category
            or label in categories
            or label == "Total Fleet"
            or label.startswith(
                "MONTHLY PRODUCTION:"
            )
        ):

            continue

        key = (
            info[
                "section"
            ],
            category,
            label,
        )

        if key not in machine_material_totals:

            continue

        output = (
            machine_material_totals[
                key
            ]
        )

        _productivity_v37_set_output(
            row,
            output,
            update_hd=False,
        )

        row[
            "productivity_summary_machine_total_v37"
        ] = 1

        machine_totals[
            key
        ] = output

    # ========================================================
    # 4. CATEGORY = SUM OF MACHINE TOTALS
    # ========================================================

    category_totals = {}

    for (
        section,
        category,
        machine,
    ), output in machine_totals.items():

        key = (
            section,
            category,
        )

        category_totals[
            key
        ] = (
            category_totals.get(
                key,
                0
            )
            + output
        )

    for info in annotated:

        row = info[
            "row"
        ]

        if not hasattr(
            row,
            "get",
        ):

            continue

        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()

        if label not in categories:

            continue

        key = (
            info[
                "section"
            ],
            label,
        )

        if key not in category_totals:

            continue

        _productivity_v37_set_output(
            row,
            category_totals[
                key
            ],
            update_hd=False,
        )

        row[
            "productivity_summary_category_total_v37"
        ] = 1

    # ========================================================
    # 5. TOTAL FLEET = EXCAVATOR + DOZER
    #
    # ADT duplicates Truck + Shovel production and therefore
    # must not be added again.
    # ========================================================

    for info in annotated:

        row = info[
            "row"
        ]

        if not hasattr(
            row,
            "get",
        ):

            continue

        if str(
            row.get(
                "label"
            )
            or ""
        ).strip() != "Total Fleet":

            continue

        section = info[
            "section"
        ]

        excavator = (
            category_totals.get(
                (
                    section,
                    "Excavator",
                ),
                0,
            )
        )

        dozer = (
            category_totals.get(
                (
                    section,
                    "Dozer",
                ),
                0,
            )
        )

        _productivity_v37_set_output(
            row,
            excavator
            + dozer,
            update_hd=False,
        )

        row[
            "productivity_summary_total_fleet_v37"
        ] = 1

    parts[
        1
    ] = rows

    if isinstance(
        result,
        tuple,
    ):

        return tuple(
            parts
        )

    return parts


def execute(filters=None):

    result = (
        _productivity_execute_before_summary_captured_totals_v37(
            filters
        )
    )

    return (
        _productivity_apply_summary_captured_totals_v37(
            result,
            filters,
        )
    )


# END KOSI_PRODUCTIVITY_SUMMARY_CAPTURED_TOTALS_V37


# ============================================================
# KOSI_PRODUCTIVITY_SUMMARY_TREE_V38
#
# DISPLAY HIERARCHY ONLY
#
# ACTUAL BCMs / Summary Per Machine:
#
# Category
#     Machine
#         Broad Material
#             Captured Material Detail
#
# Example:
#
# ADT
#     ADT01
#         Coal
#             4 - 2Seam Coal
#         Hards
#             1 - Overburden
#         Softs
#             3 - Softs
#
# IMPORTANT:
#
# - NO BCM calculation changes.
# - NO Working Hours changes.
# - NO Productivity calculation changes.
# - NO Survey allocation.
# - NO override/save-key changes.
#
# V36 and V37 values remain the source.
# ============================================================


_productivity_execute_before_summary_tree_v38 = execute


def _productivity_apply_summary_tree_v38(
    result,
    filters,
):

    if not result:

        return result

    filters = frappe._dict(
        filters
        or {}
    )

    basis = str(
        filters.get(
            "bcm_basis"
        )
        or ""
    ).strip().lower()

    view = str(
        filters.get(
            "summary_view"
        )
        or ""
    ).strip().lower()

    if (
        not basis.startswith(
            "actual"
        )
        or view
        != "summary per machine"
    ):

        return result

    parts = list(
        result
    )

    if len(
        parts
    ) < 2:

        return result

    rows = []

    current_category = ""

    categories = (
        "Excavator",
        "ADT",
        "Dozer",
    )


    for original in (
        parts[
            1
        ]
        or []
    ):

        if not hasattr(
            original,
            "get",
        ):

            rows.append(
                original
            )

            continue


        row = dict(
            original
        )


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()


        # ----------------------------------------------------
        # MONTHLY HEADER
        # ----------------------------------------------------

        if (
            int(
                row.get(
                    "is_monthly_plan_header"
                )
                or 0
            ) == 1
            or label.startswith(
                "MONTHLY PRODUCTION:"
            )
        ):

            current_category = ""

            row[
                "indent"
            ] = 0

            row[
                "productivity_summary_tree_level_v38"
            ] = "month"

            rows.append(
                row
            )

            continue


        # ----------------------------------------------------
        # CATEGORY
        #
        # ADT / Excavator / Dozer
        # ----------------------------------------------------

        if label in categories:

            current_category = (
                label
            )

            row[
                "indent"
            ] = 0

            row[
                "productivity_summary_tree_level_v38"
            ] = "category"

            rows.append(
                row
            )

            continue


        # ----------------------------------------------------
        # TOTAL FLEET
        # ----------------------------------------------------

        if label == "Total Fleet":

            current_category = ""

            row[
                "indent"
            ] = 0

            row[
                "productivity_summary_tree_level_v38"
            ] = "fleet_total"

            rows.append(
                row
            )

            continue


        # ----------------------------------------------------
        # EXACT CAPTURED DETAIL
        #
        # Example:
        #     4 - 2Seam Coal
        # ----------------------------------------------------

        if row.get(
            "productivity_summary_captured_detail_v36"
        ):

            row[
                "indent"
            ] = 3

            row[
                "productivity_summary_tree_level_v38"
            ] = "material_detail"

            rows.append(
                row
            )

            continue


        # ----------------------------------------------------
        # BROAD MATERIAL PARENT
        #
        # Example:
        #     Coal
        #     Hards
        #     Softs
        # ----------------------------------------------------

        if (
            material
            and current_category
        ):

            row[
                "indent"
            ] = 2

            row[
                "productivity_summary_material_parent_v36"
            ] = 1

            row[
                "productivity_summary_tree_level_v38"
            ] = "material"

            rows.append(
                row
            )

            continue


        # ----------------------------------------------------
        # MACHINE TOTAL
        #
        # Example:
        #     ADT01
        #     IS0601
        #     EX01
        # ----------------------------------------------------

        if (
            current_category
            and label
            and not material
        ):

            row[
                "indent"
            ] = 1

            row[
                "productivity_summary_tree_machine_v38"
            ] = 1

            row[
                "productivity_summary_tree_level_v38"
            ] = "machine"

            rows.append(
                row
            )

            continue


        rows.append(
            row
        )


    parts[
        1
    ] = rows


    if isinstance(
        result,
        tuple,
    ):

        return tuple(
            parts
        )


    return parts


def execute(filters=None):

    result = (
        _productivity_execute_before_summary_tree_v38(
            filters
        )
    )


    return (
        _productivity_apply_summary_tree_v38(
            result,
            filters,
        )
    )


# END KOSI_PRODUCTIVITY_SUMMARY_TREE_V38


# ============================================================
# KOSI_PRODUCTIVITY_ADT_TREE_ONLY_V39
#
# ACTUAL BCMs / Summary Per Machine
#
# Material hierarchy must appear ONLY below ADT machines.
#
# Excavator:
#     Keep category row.
#     Keep machine total rows.
#     Remove material parent rows.
#     Remove captured material detail rows.
#
# ADT:
#     Keep machine total.
#     Keep Coal / Hards / Softs.
#     Keep captured detail underneath material.
#
# Dozer:
#     Keep existing machine display unchanged.
#
# IMPORTANT:
# V37 totals/calculations remain unchanged.
# This patch only filters the final displayed rows.
# ============================================================


_productivity_execute_before_adt_tree_only_v39 = execute


def _productivity_apply_adt_tree_only_v39(
    result,
    filters,
):

    if not result:
        return result

    filters = frappe._dict(
        filters
        or {}
    )

    basis = str(
        filters.get(
            "bcm_basis"
        )
        or ""
    ).strip().lower()

    view = str(
        filters.get(
            "summary_view"
        )
        or ""
    ).strip().lower()

    if (
        not basis.startswith(
            "actual"
        )
        or view
        != "summary per machine"
    ):
        return result

    parts = list(
        result
    )

    if len(parts) < 2:
        return result

    output_rows = []

    current_category = ""

    categories = (
        "Excavator",
        "ADT",
        "Dozer",
    )


    for original in (
        parts[1]
        or []
    ):

        if not hasattr(
            original,
            "get",
        ):

            output_rows.append(
                original
            )

            continue


        row = dict(
            original
        )


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()


        # ---------------------------------------------
        # MONTHLY HEADER
        # ---------------------------------------------

        if label.startswith(
            "MONTHLY PRODUCTION:"
        ):

            current_category = ""

            output_rows.append(
                row
            )

            continue


        # ---------------------------------------------
        # CATEGORY ROW
        # ---------------------------------------------

        if label in categories:

            current_category = label

            output_rows.append(
                row
            )

            continue


        # ---------------------------------------------
        # TOTAL FLEET
        # ---------------------------------------------

        if label == "Total Fleet":

            current_category = ""

            output_rows.append(
                row
            )

            continue


        # =============================================
        # EXCAVATOR
        #
        # KEEP MACHINE TOTAL ONLY.
        # =============================================

        if current_category == "Excavator":

            # Exact material-detail child from V36.
            if row.get(
                "productivity_summary_captured_detail_v36"
            ):

                continue


            # Broad material row:
            # Coal / Hards / Softs etc.
            if material:

                continue


            # Machine total.
            row[
                "indent"
            ] = 1

            row[
                "productivity_summary_tree_level_v38"
            ] = "machine"

            output_rows.append(
                row
            )

            continue


        # =============================================
        # ADT
        #
        # KEEP COMPLETE TREE.
        # =============================================

        if current_category == "ADT":

            output_rows.append(
                row
            )

            continue


        # =============================================
        # DOZER / OTHER
        #
        # LEAVE EXISTING DISPLAY UNCHANGED.
        # =============================================

        output_rows.append(
            row
        )


    parts[1] = output_rows


    if isinstance(
        result,
        tuple,
    ):

        return tuple(
            parts
        )


    return parts


def execute(filters=None):

    result = (
        _productivity_execute_before_adt_tree_only_v39(
            filters
        )
    )

    return (
        _productivity_apply_adt_tree_only_v39(
            result,
            filters,
        )
    )


# END KOSI_PRODUCTIVITY_ADT_TREE_ONLY_V39


# KOSI_PRODUCTIVITY_SUMMARY_CLEANUP_V40
try:
    _KOSI_EXECUTE_BEFORE_V40 = execute
except NameError:
    _KOSI_EXECUTE_BEFORE_V40 = None

def _kosi_v40_get_summary_view(filters):
    if not filters:
        return ""
    for key in ("summary_view", "view_mode", "report_view"):
        value = filters.get(key)
        if value:
            return str(value).strip()
    return ""

def _kosi_v40_is_summary_per_machine(filters):
    return _kosi_v40_get_summary_view(filters).lower() == "summary per machine"

def _kosi_v40_blank_machine_row_fields(row):
    row["material"] = ""
    row["from_area"] = ""
    row["to_area"] = ""
    row["hauling_distance_m"] = ""
    row["coal_tons"] = ""
    return row

def _kosi_v40_clean_summary_rows(data, filters):
    if not _kosi_v40_is_summary_per_machine(filters):
        return data

    cleaned = []

    for row in data or []:
        if not isinstance(row, dict):
            cleaned.append(row)
            continue

        label = str(row.get("label") or "").strip()
        material = str(row.get("material") or "").strip()
        indent = int(row.get("indent") or 0)

        # Keep title row
        if label.startswith("MONTHLY PRODUCTION"):
            row["coal_tons"] = ""
            cleaned.append(row)
            continue

        # Category totals e.g. ADT / Excavator / Dozer
        if indent == 0:
            row["coal_tons"] = ""
            cleaned.append(row)
            continue

        # Machine rows e.g. ADT01 / IS0601
        if indent == 1:
            row = _kosi_v40_blank_machine_row_fields(row)
            cleaned.append(row)
            continue

        # First material row under machine e.g. Coal / Hards / Softs
        if indent == 2:
            if material.lower() != "coal":
                row["coal_tons"] = ""
            cleaned.append(row)
            continue

        # Remove deeper detail rows under material
        # e.g. 4 - 2Seam Coal / Ramp 2 - 3 2#Coal / 1 - Overburden / Topsoil Dump
        continue

    return cleaned

if _KOSI_EXECUTE_BEFORE_V40:
    def execute(filters=None):
        result = _KOSI_EXECUTE_BEFORE_V40(filters)

        if not isinstance(result, (list, tuple)) or len(result) < 2:
            return result

        result_list = list(result)
        result_list[1] = _kosi_v40_clean_summary_rows(result_list[1], filters or {})

        if isinstance(result, tuple):
            return tuple(result_list)
        return result_list

# END KOSI_PRODUCTIVITY_SUMMARY_CLEANUP_V40


# ============================================================
# KOSI_PRODUCTIVITY_SUMMARY_MACHINE_ONLY_V41
#
# SUMMARY PER MACHINE
#
# Final display:
#
# Excavator
#     EX01
#     IS0312
#     IS0330
#
# ADT
#     ADT01
#     ADT02
#     ADT03
#
# Dozer
#     IS0335
#     IS0336
#
#
# REMOVE FROM SUMMARY PER MACHINE:
#
# - Coal
# - Hards
# - Softs
# - Material detail rows
# - Coal Tons column
#
#
# IMPORTANT:
#
# - Machine total BCM remains unchanged.
# - Machine Working Hours remain unchanged.
# - Machine Productivity remains unchanged.
# - Hours and Material remains unchanged.
# - Tallies calculations remain unchanged.
# - No Survey calculation changes.
# - No override/save-key changes.
# ============================================================


_productivity_execute_before_summary_machine_only_v41 = execute


def _productivity_v41_is_summary_per_machine(
    filters,
):

    filters = frappe._dict(
        filters
        or {}
    )

    return (
        str(
            filters.get(
                "summary_view"
            )
            or ""
        ).strip().lower()
        == "summary per machine"
    )


def _productivity_v41_clean_columns(
    columns,
):

    cleaned = []

    for column in (
        columns
        or []
    ):

        if not hasattr(
            column,
            "get",
        ):

            cleaned.append(
                column
            )

            continue

        fieldname = str(
            column.get(
                "fieldname"
            )
            or ""
        ).strip().lower()

        label = str(
            column.get(
                "label"
            )
            or ""
        ).strip().lower()


        if (
            fieldname
            == "coal_tons"
            or label
            == "coal tons"
        ):

            continue


        cleaned.append(
            column
        )


    return cleaned


def _productivity_v41_clean_rows(
    rows,
):

    cleaned = []

    categories = (
        "Excavator",
        "ADT",
        "Dozer",
    )


    for original in (
        rows
        or []
    ):

        if not hasattr(
            original,
            "get",
        ):

            cleaned.append(
                original
            )

            continue


        row = dict(
            original
        )


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()


        # ----------------------------------------------------
        # MONTHLY PRODUCTION HEADER
        # ----------------------------------------------------

        if (
            int(
                row.get(
                    "is_monthly_plan_header"
                )
                or 0
            ) == 1
            or label.startswith(
                "MONTHLY PRODUCTION:"
            )
        ):

            row[
                "coal_tons"
            ] = ""

            cleaned.append(
                row
            )

            continue


        # ----------------------------------------------------
        # CATEGORY TOTAL
        # ----------------------------------------------------

        if label in categories:

            row[
                "coal_tons"
            ] = ""

            row[
                "material"
            ] = ""

            row[
                "from_area"
            ] = ""

            row[
                "to_area"
            ] = ""

            row[
                "hauling_distance_m"
            ] = ""

            cleaned.append(
                row
            )

            continue


        # ----------------------------------------------------
        # TOTAL FLEET
        # ----------------------------------------------------

        if label == "Total Fleet":

            row[
                "coal_tons"
            ] = ""

            row[
                "material"
            ] = ""

            row[
                "from_area"
            ] = ""

            row[
                "to_area"
            ] = ""

            row[
                "hauling_distance_m"
            ] = ""

            cleaned.append(
                row
            )

            continue


        # ----------------------------------------------------
        # REMOVE ALL MATERIAL / DETAIL ROWS
        #
        # This catches:
        #
        # Coal
        # Hards
        # Softs
        # Ramp 2 - 3 2#Coal
        # Ramp 1 Overburden
        # Topsoil Dump
        # etc.
        # ----------------------------------------------------

        if material:

            continue


        if row.get(
            "productivity_summary_captured_detail_v36"
        ):

            continue


        # ----------------------------------------------------
        # KEEP MACHINE TOTAL ONLY
        # ----------------------------------------------------

        if label:

            row[
                "material"
            ] = ""

            row[
                "from_area"
            ] = ""

            row[
                "to_area"
            ] = ""

            row[
                "hauling_distance_m"
            ] = ""

            row[
                "coal_tons"
            ] = ""

            row[
                "indent"
            ] = 1

            row[
                "productivity_summary_tree_level_v38"
            ] = "machine"

            cleaned.append(
                row
            )


    return cleaned


def _productivity_apply_summary_machine_only_v41(
    result,
    filters,
):

    if not result:

        return result


    if not (
        _productivity_v41_is_summary_per_machine(
            filters
        )
    ):

        return result


    parts = list(
        result
    )


    if len(
        parts
    ) >= 1:

        parts[
            0
        ] = (
            _productivity_v41_clean_columns(
                parts[
                    0
                ]
            )
        )


    if len(
        parts
    ) >= 2:

        parts[
            1
        ] = (
            _productivity_v41_clean_rows(
                parts[
                    1
                ]
            )
        )


    if isinstance(
        result,
        tuple,
    ):

        return tuple(
            parts
        )


    return parts


def execute(filters=None):

    result = (
        _productivity_execute_before_summary_machine_only_v41(
            filters
        )
    )


    return (
        _productivity_apply_summary_machine_only_v41(
            result,
            filters,
        )
    )


# END KOSI_PRODUCTIVITY_SUMMARY_MACHINE_ONLY_V41


# ============================================================
# KOSI_PRODUCTIVITY_ADT_MATERIAL_ONLY_V42
#
# FINAL SUMMARY PER MACHINE DISPLAY
#
# Excavator:
#     Machine totals only
#
# ADT:
#     Machine total
#         Coal
#         Hards
#         Softs
#
# Dozer:
#     Machine totals only
#
# REMOVE:
#
# - Coal Tons column from Summary Per Machine
# - Geo / Material detail rows
# - Ramp detail rows
# - Overburden detail rows
# - Topsoil detail rows
# - Seam detail rows
#
# KEEP Hours and Material unchanged.
#
# IMPORTANT:
# We intentionally bypass V41 because V41 removed ALL
# ADT material rows.
# ============================================================


try:

    _productivity_execute_before_adt_material_only_v42 = (
        _productivity_execute_before_summary_machine_only_v41
    )

except NameError:

    _productivity_execute_before_adt_material_only_v42 = execute


def _productivity_v42_is_target(
    filters,
):

    filters = frappe._dict(
        filters
        or {}
    )

    basis = str(
        filters.get(
            "bcm_basis"
        )
        or ""
    ).strip().lower()

    view = str(
        filters.get(
            "summary_view"
        )
        or ""
    ).strip().lower()

    return (
        basis.startswith(
            "actual"
        )
        and view
        == "summary per machine"
    )


def _productivity_v42_columns(
    columns,
):

    cleaned = []

    for column in (
        columns
        or []
    ):

        if not hasattr(
            column,
            "get",
        ):

            cleaned.append(
                column
            )

            continue


        fieldname = str(
            column.get(
                "fieldname"
            )
            or ""
        ).strip().lower()


        label = str(
            column.get(
                "label"
            )
            or ""
        ).strip().lower()


        if (
            fieldname
            == "coal_tons"
            or label
            == "coal tons"
        ):

            continue


        cleaned.append(
            column
        )


    return cleaned


def _productivity_v42_rows(
    rows,
):

    output = []

    current_category = ""

    categories = (
        "Excavator",
        "ADT",
        "Dozer",
    )

    broad_adt_materials = {
        "coal",
        "hards",
        "softs",
    }


    for original in (
        rows
        or []
    ):

        if not hasattr(
            original,
            "get",
        ):

            output.append(
                original
            )

            continue


        row = dict(
            original
        )


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()


        # ----------------------------------------------------
        # MONTHLY HEADER
        # ----------------------------------------------------

        if (
            int(
                row.get(
                    "is_monthly_plan_header"
                )
                or 0
            ) == 1
            or label.startswith(
                "MONTHLY PRODUCTION:"
            )
        ):

            current_category = ""

            row[
                "coal_tons"
            ] = ""

            output.append(
                row
            )

            continue


        # ----------------------------------------------------
        # CATEGORY
        # ----------------------------------------------------

        if label in categories:

            current_category = (
                label
            )

            row[
                "indent"
            ] = 0

            row[
                "coal_tons"
            ] = ""

            row[
                "material"
            ] = ""

            output.append(
                row
            )

            continue


        # ----------------------------------------------------
        # TOTAL FLEET
        # ----------------------------------------------------

        if label == "Total Fleet":

            current_category = ""

            row[
                "indent"
            ] = 0

            row[
                "coal_tons"
            ] = ""

            row[
                "material"
            ] = ""

            output.append(
                row
            )

            continue


        # ====================================================
        # EXCAVATOR
        #
        # MACHINE TOTAL ONLY
        # ====================================================

        if current_category == "Excavator":

            if material:

                continue


            if row.get(
                "productivity_summary_captured_detail_v36"
            ):

                continue


            if label:

                row[
                    "indent"
                ] = 1

                row[
                    "material"
                ] = ""

                row[
                    "from_area"
                ] = ""

                row[
                    "to_area"
                ] = ""

                row[
                    "hauling_distance_m"
                ] = ""

                row[
                    "coal_tons"
                ] = ""

                row[
                    "productivity_summary_tree_level_v38"
                ] = "machine"

                output.append(
                    row
                )


            continue


        # ====================================================
        # ADT
        #
        # MACHINE + BROAD MATERIAL ONLY
        # ====================================================

        if current_category == "ADT":

            # Remove V36 exact captured child rows.
            if row.get(
                "productivity_summary_captured_detail_v36"
            ):

                continue


            # -----------------------------------------------
            # ADT MATERIAL
            #
            # Keep ONLY:
            # Coal
            # Hards
            # Softs
            # -----------------------------------------------

            if material:

                material_lower = (
                    material.lower()
                )


                if (
                    material_lower
                    not in broad_adt_materials
                ):

                    continue


                row[
                    "indent"
                ] = 2

                row[
                    "coal_tons"
                ] = ""

                row[
                    "productivity_summary_tree_level_v38"
                ] = "material"

                row[
                    "productivity_summary_material_parent_v36"
                ] = 1

                output.append(
                    row
                )

                continue


            # -----------------------------------------------
            # ADT MACHINE TOTAL
            # -----------------------------------------------

            if label:

                row[
                    "indent"
                ] = 1

                row[
                    "material"
                ] = ""

                row[
                    "from_area"
                ] = ""

                row[
                    "to_area"
                ] = ""

                row[
                    "hauling_distance_m"
                ] = ""

                row[
                    "coal_tons"
                ] = ""

                row[
                    "productivity_summary_tree_level_v38"
                ] = "machine"

                output.append(
                    row
                )


            continue


        # ====================================================
        # DOZER
        #
        # MACHINE TOTAL ONLY
        # ====================================================

        if current_category == "Dozer":

            if material:

                continue


            if row.get(
                "productivity_summary_captured_detail_v36"
            ):

                continue


            if label:

                row[
                    "indent"
                ] = 1

                row[
                    "material"
                ] = ""

                row[
                    "from_area"
                ] = ""

                row[
                    "to_area"
                ] = ""

                row[
                    "hauling_distance_m"
                ] = ""

                row[
                    "coal_tons"
                ] = ""

                row[
                    "productivity_summary_tree_level_v38"
                ] = "machine"

                output.append(
                    row
                )


            continue


        output.append(
            row
        )


    return output


def _productivity_apply_adt_material_only_v42(
    result,
    filters,
):

    if not result:

        return result


    if not (
        _productivity_v42_is_target(
            filters
        )
    ):

        return result


    parts = list(
        result
    )


    if len(
        parts
    ) >= 1:

        parts[
            0
        ] = (
            _productivity_v42_columns(
                parts[
                    0
                ]
            )
        )


    if len(
        parts
    ) >= 2:

        parts[
            1
        ] = (
            _productivity_v42_rows(
                parts[
                    1
                ]
            )
        )


    if isinstance(
        result,
        tuple,
    ):

        return tuple(
            parts
        )


    return parts


def execute(filters=None):

    result = (
        _productivity_execute_before_adt_material_only_v42(
            filters
        )
    )


    return (
        _productivity_apply_adt_material_only_v42(
            result,
            filters,
        )
    )


# END KOSI_PRODUCTIVITY_ADT_MATERIAL_ONLY_V42


# ============================================================
# KOSI_PRODUCTIVITY_SUMMARY_MACHINE_ROWS_ONLY_V43
#
# ACTUAL BCMs / Summary Per Machine
#
# Keep ONLY:
#
# Category
#     Machine
#
# Example:
#
# ADT
#     ADT01
#     ADT02
#     ADT03
#
# REMOVE:
#
#     Coal
#     Hards
#     Softs
#     all deeper material detail rows
#
# Hours and Material remains unchanged.
# Tallies remains unchanged.
# Existing machine totals remain unchanged.
# ============================================================


_productivity_execute_before_summary_machine_rows_only_v43 = execute


def _productivity_apply_summary_machine_rows_only_v43(
    result,
    filters,
):

    if not result:

        return result

    filters = frappe._dict(
        filters
        or {}
    )

    basis = str(
        filters.get(
            "bcm_basis"
        )
        or ""
    ).strip().lower()

    view = str(
        filters.get(
            "summary_view"
        )
        or ""
    ).strip().lower()

    if (
        not basis.startswith(
            "actual"
        )
        or view
        != "summary per machine"
    ):

        return result

    parts = list(
        result
    )

    if len(
        parts
    ) < 2:

        return result

    cleaned = []

    for original in (
        parts[
            1
        ]
        or []
    ):

        if not hasattr(
            original,
            "get",
        ):

            cleaned.append(
                original
            )

            continue

        row = dict(
            original
        )

        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()

        # Remove Coal / Hards / Softs rows.
        if material:

            continue

        # Remove any remaining captured detail rows.
        if row.get(
            "productivity_summary_captured_detail_v36"
        ):

            continue

        cleaned.append(
            row
        )

    parts[
        1
    ] = cleaned

    if isinstance(
        result,
        tuple,
    ):

        return tuple(
            parts
        )

    return parts


def execute(filters=None):

    result = (
        _productivity_execute_before_summary_machine_rows_only_v43(
            filters
        )
    )

    return (
        _productivity_apply_summary_machine_rows_only_v43(
            result,
            filters,
        )
    )


# END KOSI_PRODUCTIVITY_SUMMARY_MACHINE_ROWS_ONLY_V43


# ============================================================
# KOSI_PRODUCTIVITY_MACHINE_MATERIAL_COLUMN_V44
#
# ACTUAL BCMs / SUMMARY PER MACHINE
#
# ADT fleet rows only:
#
# ADT01   ...   Material = Coal / Hards / Softs
#
# REMOVE material child rows from display.
#
# Excavator and Dozer remain machine totals only.
#
# IMPORTANT:
# - No BCM calculation changes.
# - No Working Hours changes.
# - No Productivity changes.
# - Hours and Material unchanged.
# ============================================================


try:

    # Bypass V43 because we need to read the broad ADT
    # material rows before removing them.
    _productivity_execute_before_machine_material_column_v44 = (
        _productivity_execute_before_summary_machine_rows_only_v43
    )

except NameError:

    _productivity_execute_before_machine_material_column_v44 = execute


def _productivity_v44_is_target(filters):

    filters = frappe._dict(
        filters
        or {}
    )

    basis = str(
        filters.get(
            "bcm_basis"
        )
        or ""
    ).strip().lower()

    view = str(
        filters.get(
            "summary_view"
        )
        or ""
    ).strip().lower()

    return (
        basis.startswith(
            "actual"
        )
        and view
        == "summary per machine"
    )


def _productivity_v44_rows(rows):

    rows = [
        dict(row)
        if hasattr(
            row,
            "get",
        )
        else row

        for row in (
            rows
            or []
        )
    ]

    categories = (
        "Excavator",
        "ADT",
        "Dozer",
    )

    allowed_materials = (
        "Coal",
        "Hards",
        "Softs",
    )

    # --------------------------------------------------------
    # PASS 1:
    # Find which broad materials belong to each ADT machine.
    # --------------------------------------------------------

    machine_materials = {}

    current_category = ""
    current_machine = ""
    section = 0


    for row in rows:

        if not hasattr(
            row,
            "get",
        ):

            continue


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()


        if label.startswith(
            "MONTHLY PRODUCTION:"
        ):

            section += 1
            current_category = ""
            current_machine = ""

            continue


        if label in categories:

            current_category = label
            current_machine = ""

            continue


        if label == "Total Fleet":

            current_category = ""
            current_machine = ""

            continue


        if current_category != "ADT":

            continue


        # Machine row
        if (
            not material
            and not row.get(
                "productivity_summary_captured_detail_v36"
            )
            and label
        ):

            current_machine = label

            machine_materials.setdefault(
                (
                    section,
                    current_machine,
                ),
                [],
            )

            continue


        # Broad material row under current ADT.
        if (
            current_machine
            and material in allowed_materials
        ):

            values = machine_materials.setdefault(
                (
                    section,
                    current_machine,
                ),
                [],
            )

            if material not in values:

                values.append(
                    material
                )


    # --------------------------------------------------------
    # PASS 2:
    # Keep only categories + machines.
    # Put material list onto ADT machine row.
    # --------------------------------------------------------

    output = []

    current_category = ""
    section = 0


    for row in rows:

        if not hasattr(
            row,
            "get",
        ):

            output.append(
                row
            )

            continue


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()


        if label.startswith(
            "MONTHLY PRODUCTION:"
        ):

            section += 1
            current_category = ""

            output.append(
                row
            )

            continue


        if label in categories:

            current_category = label

            row[
                "material"
            ] = ""

            output.append(
                row
            )

            continue


        if label == "Total Fleet":

            current_category = ""

            row[
                "material"
            ] = ""

            output.append(
                row
            )

            continue


        # Remove all material/detail child rows.
        if material:

            continue


        if row.get(
            "productivity_summary_captured_detail_v36"
        ):

            continue


        if not label:

            continue


        # Machine total row.
        row[
            "indent"
        ] = 1

        row[
            "from_area"
        ] = ""

        row[
            "to_area"
        ] = ""

        row[
            "hauling_distance_m"
        ] = ""

        row[
            "coal_tons"
        ] = ""


        if current_category == "ADT":

            materials = machine_materials.get(
                (
                    section,
                    label,
                ),
                [],
            )

            row[
                "material"
            ] = " / ".join(
                materials
            )

            row[
                "productivity_summary_machine_material_list_v44"
            ] = 1

        else:

            row[
                "material"
            ] = ""


        output.append(
            row
        )


    return output


def _productivity_apply_machine_material_column_v44(
    result,
    filters,
):

    if not result:

        return result


    if not (
        _productivity_v44_is_target(
            filters
        )
    ):

        return result


    parts = list(
        result
    )


    if len(
        parts
    ) >= 2:

        parts[
            1
        ] = (
            _productivity_v44_rows(
                parts[
                    1
                ]
            )
        )


    if isinstance(
        result,
        tuple,
    ):

        return tuple(
            parts
        )


    return parts


def execute(filters=None):

    result = (
        _productivity_execute_before_machine_material_column_v44(
            filters
        )
    )


    return (
        _productivity_apply_machine_material_column_v44(
            result,
            filters,
        )
    )


# END KOSI_PRODUCTIVITY_MACHINE_MATERIAL_COLUMN_V44


# ============================================================
# KOSI_PRODUCTIVITY_ADT_MATERIAL_ROWS_V45
#
# ACTUAL BCMs / SUMMARY PER MACHINE
#
# FINAL ADT FORMAT:
#
# ADT01      442    20,129     46      [blank]
# ADT01        3       112     40      Coal
# ADT01        5       192     42      Hards
# ADT01      435    19,825     46      Softs
#
# IMPORTANT:
#
# - One material per row.
# - Do NOT combine Coal / Hards / Softs.
# - Do NOT display detailed Ramp / Seam / Overburden rows.
# - Excavator remains machine totals only.
# - Dozer remains machine totals only.
# - Hours and Material unchanged.
# - V37 machine/output calculations unchanged.
# ============================================================


try:

    # V44 deliberately saved V42 as its source.
    # V42 already has exactly what we need:
    #
    # - Excavator machine totals only
    # - ADT machine + broad material rows
    # - No deep material-detail rows
    # - Dozer machine totals only
    #
    # So bypass V43/V44 presentation and use that clean result.
    _productivity_execute_before_adt_material_rows_v45 = (
        _productivity_execute_before_machine_material_column_v44
    )

except NameError:

    _productivity_execute_before_adt_material_rows_v45 = execute


def _productivity_v45_is_target(
    filters,
):

    filters = frappe._dict(
        filters
        or {}
    )

    basis = str(
        filters.get(
            "bcm_basis"
        )
        or ""
    ).strip().lower()

    view = str(
        filters.get(
            "summary_view"
        )
        or ""
    ).strip().lower()

    return (
        basis.startswith(
            "actual"
        )
        and view
        == "summary per machine"
    )


def _productivity_v45_rows(
    rows,
):

    output = []

    current_category = ""

    categories = (
        "Excavator",
        "ADT",
        "Dozer",
    )

    allowed_adt_materials = {
        "coal",
        "hards",
        "softs",
    }


    for original in (
        rows
        or []
    ):

        if not hasattr(
            original,
            "get",
        ):

            output.append(
                original
            )

            continue


        row = dict(
            original
        )


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()


        # ----------------------------------------------------
        # MONTHLY HEADER
        # ----------------------------------------------------

        if (
            int(
                row.get(
                    "is_monthly_plan_header"
                )
                or 0
            ) == 1
            or label.startswith(
                "MONTHLY PRODUCTION:"
            )
        ):

            current_category = ""

            output.append(
                row
            )

            continue


        # ----------------------------------------------------
        # CATEGORY
        # ----------------------------------------------------

        if label in categories:

            current_category = label

            row[
                "material"
            ] = ""

            output.append(
                row
            )

            continue


        # ----------------------------------------------------
        # TOTAL FLEET
        # ----------------------------------------------------

        if label == "Total Fleet":

            current_category = ""

            row[
                "material"
            ] = ""

            output.append(
                row
            )

            continue


        # ====================================================
        # EXCAVATOR
        #
        # MACHINE TOTAL ONLY
        # ====================================================

        if current_category == "Excavator":

            if material:

                continue


            if row.get(
                "productivity_summary_captured_detail_v36"
            ):

                continue


            row[
                "material"
            ] = ""

            row[
                "from_area"
            ] = ""

            row[
                "to_area"
            ] = ""

            row[
                "hauling_distance_m"
            ] = ""

            row[
                "indent"
            ] = 1

            row[
                "productivity_summary_tree_level_v38"
            ] = "machine"

            output.append(
                row
            )

            continue


        # ====================================================
        # ADT
        # ====================================================

        if current_category == "ADT":

            # No detailed Ramp / seam / overburden children.
            if row.get(
                "productivity_summary_captured_detail_v36"
            ):

                continue


            # ------------------------------------------------
            # BROAD MATERIAL ROW
            #
            # Keep each material as its OWN row.
            # Backend label remains the machine name.
            # ------------------------------------------------

            if material:

                if (
                    material.lower()
                    not in allowed_adt_materials
                ):

                    continue


                row[
                    "indent"
                ] = 1

                # Prevent old V35/V36/V38 material-parent
                # formatters from replacing the Label with
                # "Coal", "Hards" or "Softs".
                row[
                    "productivity_summary_material_parent_v36"
                ] = 0

                row[
                    "productivity_summary_tree_level_v38"
                ] = "adt_material_row_v45"

                row[
                    "productivity_summary_adt_material_row_v45"
                ] = 1

                output.append(
                    row
                )

                continue


            # ------------------------------------------------
            # MACHINE TOTAL
            # ------------------------------------------------

            if label:

                row[
                    "material"
                ] = ""

                row[
                    "from_area"
                ] = ""

                row[
                    "to_area"
                ] = ""

                row[
                    "hauling_distance_m"
                ] = ""

                row[
                    "indent"
                ] = 1

                row[
                    "productivity_summary_tree_level_v38"
                ] = "machine"

                row[
                    "productivity_summary_adt_machine_total_v45"
                ] = 1

                output.append(
                    row
                )


            continue


        # ====================================================
        # DOZER
        #
        # MACHINE TOTAL ONLY
        # ====================================================

        if current_category == "Dozer":

            if material:

                continue


            if row.get(
                "productivity_summary_captured_detail_v36"
            ):

                continue


            row[
                "material"
            ] = ""

            row[
                "from_area"
            ] = ""

            row[
                "to_area"
            ] = ""

            row[
                "hauling_distance_m"
            ] = ""

            row[
                "indent"
            ] = 1

            row[
                "productivity_summary_tree_level_v38"
            ] = "machine"

            output.append(
                row
            )

            continue


        output.append(
            row
        )


    return output


def _productivity_apply_adt_material_rows_v45(
    result,
    filters,
):

    if not result:

        return result


    if not (
        _productivity_v45_is_target(
            filters
        )
    ):

        return result


    parts = list(
        result
    )


    if len(
        parts
    ) >= 2:

        parts[
            1
        ] = (
            _productivity_v45_rows(
                parts[
                    1
                ]
            )
        )


    if isinstance(
        result,
        tuple,
    ):

        return tuple(
            parts
        )


    return parts


def execute(filters=None):

    result = (
        _productivity_execute_before_adt_material_rows_v45(
            filters
        )
    )


    return (
        _productivity_apply_adt_material_rows_v45(
            result,
            filters,
        )
    )


# END KOSI_PRODUCTIVITY_ADT_MATERIAL_ROWS_V45


# ============================================================
# KOSI_PRODUCTIVITY_ADT_DOZER_MATERIAL_ROWS_V47
#
# ACTUAL BCMs / SUMMARY PER MACHINE
#
# FINAL DISPLAY:
#
# Excavator
#     Machine total only
#
# ADT
#     Machine total
#         one row per Material
#
# Dozer
#     Machine total
#         one row per Material
#
# Material rows:
#
#     Label column = blank visually
#     Material column = actual material
#
# NO deep:
#     Ramp / Seam / Overburden / Topsoil detail children
#
# Hours and Material remains unchanged.
#
# V37 calculations remain unchanged.
# ============================================================


try:

    # V41 saved the V40 execute function here.
    #
    # V40 gives us:
    # - V37 corrected calculations
    # - Excavator machine only after V39
    # - machine rows
    # - first material level
    # - deeper material-detail rows already removed
    #
    # This bypasses V41-V46 display experiments.
    _productivity_execute_before_adt_dozer_material_rows_v47 = (
        _productivity_execute_before_summary_machine_only_v41
    )

except NameError:

    try:

        _productivity_execute_before_adt_dozer_material_rows_v47 = (
            _KOSI_EXECUTE_BEFORE_V40
        )

    except NameError:

        _productivity_execute_before_adt_dozer_material_rows_v47 = execute


def _productivity_v47_is_target(
    filters,
):

    filters = frappe._dict(
        filters
        or {}
    )

    basis = str(
        filters.get(
            "bcm_basis"
        )
        or ""
    ).strip().lower()

    view = str(
        filters.get(
            "summary_view"
        )
        or ""
    ).strip().lower()

    return (
        basis.startswith(
            "actual"
        )
        and view
        == "summary per machine"
    )


def _productivity_v47_columns(
    columns,
):

    result = []

    for column in (
        columns
        or []
    ):

        if not hasattr(
            column,
            "get",
        ):

            result.append(
                column
            )

            continue


        fieldname = str(
            column.get(
                "fieldname"
            )
            or ""
        ).strip().lower()


        label = str(
            column.get(
                "label"
            )
            or ""
        ).strip().lower()


        # Coal Tons is not required in Summary Per Machine.
        if (
            fieldname == "coal_tons"
            or label == "coal tons"
        ):

            continue


        result.append(
            column
        )


    return result


def _productivity_v47_rows(
    rows,
):

    result = []

    current_category = ""

    categories = (
        "Excavator",
        "ADT",
        "Dozer",
    )


    for original in (
        rows
        or []
    ):

        if not hasattr(
            original,
            "get",
        ):

            result.append(
                original
            )

            continue


        row = dict(
            original
        )


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()


        # ----------------------------------------------------
        # MONTH HEADER
        # ----------------------------------------------------

        if (
            int(
                row.get(
                    "is_monthly_plan_header"
                )
                or 0
            ) == 1
            or label.startswith(
                "MONTHLY PRODUCTION:"
            )
        ):

            current_category = ""

            row[
                "coal_tons"
            ] = ""

            result.append(
                row
            )

            continue


        # ----------------------------------------------------
        # CATEGORY
        # ----------------------------------------------------

        if label in categories:

            current_category = label

            row[
                "material"
            ] = ""

            row[
                "coal_tons"
            ] = ""

            row[
                "indent"
            ] = 0

            result.append(
                row
            )

            continue


        # ----------------------------------------------------
        # TOTAL FLEET
        # ----------------------------------------------------

        if label == "Total Fleet":

            current_category = ""

            row[
                "material"
            ] = ""

            row[
                "coal_tons"
            ] = ""

            row[
                "indent"
            ] = 0

            result.append(
                row
            )

            continue


        # ====================================================
        # EXCAVATOR
        #
        # MACHINE TOTAL ONLY
        # ====================================================

        if current_category == "Excavator":

            if material:

                continue


            if not label:

                continue


            row[
                "material"
            ] = ""

            row[
                "from_area"
            ] = ""

            row[
                "to_area"
            ] = ""

            row[
                "hauling_distance_m"
            ] = ""

            row[
                "coal_tons"
            ] = ""

            row[
                "indent"
            ] = 1

            row[
                "productivity_summary_tree_level_v38"
            ] = "machine"

            result.append(
                row
            )

            continue


        # ====================================================
        # ADT + DOZER
        # ====================================================

        if current_category in (
            "ADT",
            "Dozer",
        ):

            # -----------------------------------------------
            # MATERIAL ROW
            # -----------------------------------------------

            if material:

                row[
                    "indent"
                ] = 1

                row[
                    "coal_tons"
                ] = ""

                # Disable old material-parent formatting.
                row[
                    "productivity_summary_material_parent_v36"
                ] = 0

                row[
                    "productivity_summary_tree_level_v38"
                ] = (
                    "equipment_material_v47"
                )

                row[
                    "productivity_summary_equipment_material_v47"
                ] = 1

                row[
                    "productivity_summary_equipment_category_v47"
                ] = current_category

                result.append(
                    row
                )

                continue


            # -----------------------------------------------
            # MACHINE TOTAL
            # -----------------------------------------------

            if label:

                row[
                    "material"
                ] = ""

                row[
                    "from_area"
                ] = ""

                row[
                    "to_area"
                ] = ""

                row[
                    "hauling_distance_m"
                ] = ""

                row[
                    "coal_tons"
                ] = ""

                row[
                    "indent"
                ] = 1

                row[
                    "productivity_summary_tree_level_v38"
                ] = "machine"

                result.append(
                    row
                )


            continue


        result.append(
            row
        )


    return result


def _productivity_apply_adt_dozer_material_rows_v47(
    result,
    filters,
):

    if not result:

        return result


    if not (
        _productivity_v47_is_target(
            filters
        )
    ):

        return result


    parts = list(
        result
    )


    if len(
        parts
    ) >= 1:

        parts[
            0
        ] = (
            _productivity_v47_columns(
                parts[
                    0
                ]
            )
        )


    if len(
        parts
    ) >= 2:

        parts[
            1
        ] = (
            _productivity_v47_rows(
                parts[
                    1
                ]
            )
        )


    if isinstance(
        result,
        tuple,
    ):

        return tuple(
            parts
        )


    return parts


def execute(filters=None):

    result = (
        _productivity_execute_before_adt_dozer_material_rows_v47(
            filters
        )
    )


    return (
        _productivity_apply_adt_dozer_material_rows_v47(
            result,
            filters,
        )
    )


# END KOSI_PRODUCTIVITY_ADT_DOZER_MATERIAL_ROWS_V47


# ============================================================
# KOSI_PRODUCTIVITY_SUMMARY_REMOVE_AREA_HD_V48
#
# ACTUAL BCMs / SUMMARY PER MACHINE ONLY
#
# REMOVE COLUMNS:
#
# - Productivity (BCM/HD)
# - From Area
# - To Area
# - Hauling Distance (M)
#
# This removes values such as:
#
# - 0.15
# - Ramp 1
# - editable To Area
# - editable Hauling Distance
#
# KEEP:
#
# - Label
# - Working Hours
# - Output
# - Productivity BCM/HR
# - Material
#
# Hours and Material remains unchanged.
# ============================================================


_productivity_execute_before_summary_remove_area_hd_v48 = execute


def _productivity_v48_is_target(filters):

    filters = frappe._dict(
        filters
        or {}
    )

    basis = str(
        filters.get(
            "bcm_basis"
        )
        or ""
    ).strip().lower()

    view = str(
        filters.get(
            "summary_view"
        )
        or ""
    ).strip().lower()

    return (
        basis.startswith(
            "actual"
        )
        and view
        == "summary per machine"
    )


def _productivity_v48_columns(columns):

    remove_fields = {
        "productivity_bcm_hd",
        "from_area",
        "to_area",
        "hauling_distance_m",
    }

    cleaned = []

    for column in (
        columns
        or []
    ):

        if not hasattr(
            column,
            "get",
        ):

            cleaned.append(
                column
            )

            continue

        fieldname = str(
            column.get(
                "fieldname"
            )
            or ""
        ).strip()

        if fieldname in remove_fields:

            continue

        cleaned.append(
            column
        )

    return cleaned


def _productivity_v48_rows(rows):

    cleaned = []

    for original in (
        rows
        or []
    ):

        if not hasattr(
            original,
            "get",
        ):

            cleaned.append(
                original
            )

            continue

        row = dict(
            original
        )

        # Blank hidden fields as well so PDF / export /
        # snapshots cannot show stale area or HD values.
        row[
            "productivity_bcm_hd"
        ] = ""

        row[
            "from_area"
        ] = ""

        row[
            "to_area"
        ] = ""

        row[
            "hauling_distance_m"
        ] = ""

        row[
            "productivity_summary_remove_area_hd_v48"
        ] = 1

        cleaned.append(
            row
        )

    return cleaned


def _productivity_apply_summary_remove_area_hd_v48(
    result,
    filters,
):

    if not result:

        return result

    if not (
        _productivity_v48_is_target(
            filters
        )
    ):

        return result

    parts = list(
        result
    )

    if len(
        parts
    ) >= 1:

        parts[
            0
        ] = (
            _productivity_v48_columns(
                parts[
                    0
                ]
            )
        )

    if len(
        parts
    ) >= 2:

        parts[
            1
        ] = (
            _productivity_v48_rows(
                parts[
                    1
                ]
            )
        )

    if isinstance(
        result,
        tuple,
    ):

        return tuple(
            parts
        )

    return parts


def execute(filters=None):

    result = (
        _productivity_execute_before_summary_remove_area_hd_v48(
            filters
        )
    )

    return (
        _productivity_apply_summary_remove_area_hd_v48(
            result,
            filters,
        )
    )


# END KOSI_PRODUCTIVITY_SUMMARY_REMOVE_AREA_HD_V48


# ============================================================
# KOSI_PRODUCTIVITY_RESTORE_AREA_V49
#
# SUMMARY PER MACHINE
#
# PUT BACK:
#
# - From Area
# - To Area
# - Hauling Distance (M)
#
# KEEP REMOVED:
#
# - Productivity (BCM/HD)
#
# We intentionally bypass V48 because V48 blanked the
# Area / To Area / Hauling Distance values.
# ============================================================


try:

    _productivity_execute_before_restore_area_v49 = (
        _productivity_execute_before_summary_remove_area_hd_v48
    )

except NameError:

    _productivity_execute_before_restore_area_v49 = execute


def _productivity_v49_is_target(filters):

    filters = frappe._dict(
        filters or {}
    )

    basis = str(
        filters.get("bcm_basis")
        or ""
    ).strip().lower()

    view = str(
        filters.get("summary_view")
        or ""
    ).strip().lower()

    return (
        basis.startswith("actual")
        and view == "summary per machine"
    )


def _productivity_v49_columns(columns):

    cleaned = []

    for column in (
        columns
        or []
    ):

        if not hasattr(
            column,
            "get",
        ):

            cleaned.append(
                column
            )

            continue

        fieldname = str(
            column.get(
                "fieldname"
            )
            or ""
        ).strip()

        # Keep only BCM/HD hidden.
        if fieldname == "productivity_bcm_hd":

            continue

        cleaned.append(
            column
        )

    return cleaned


def _productivity_apply_restore_area_v49(
    result,
    filters,
):

    if not result:

        return result

    if not (
        _productivity_v49_is_target(
            filters
        )
    ):

        return result

    parts = list(
        result
    )

    if len(parts) >= 1:

        parts[0] = (
            _productivity_v49_columns(
                parts[0]
            )
        )

    # IMPORTANT:
    # Do not blank any row values here.
    #
    # V47 values for:
    # from_area
    # to_area
    # hauling_distance_m
    #
    # remain available.

    if isinstance(
        result,
        tuple,
    ):

        return tuple(
            parts
        )

    return parts


def execute(filters=None):

    result = (
        _productivity_execute_before_restore_area_v49(
            filters
        )
    )

    return (
        _productivity_apply_restore_area_v49(
            result,
            filters,
        )
    )


# END KOSI_PRODUCTIVITY_RESTORE_AREA_V49

# ============================================================
# KOSI_PRODUCTIVITY_SUMMARY_RESTORE_BCMHD_MOVE_AREA_V50
#
# SUMMARY PER MACHINE
#
# GOAL:
# - Restore Productivity (BCM/HD) column
# - For ADT and Dozer:
#     * Keep BCM/HD on parent material rows
#     * Blank From Area / To Area / Hauling Distance on parent material rows
#     * Show Material / From Area / To Area / Hauling Distance on breakdown rows
#
# TARGET VIEW:
#   bcm_basis = Actual BCMs
#   summary_view = Summary Per Machine
# ============================================================

try:
    _productivity_execute_before_summary_restore_bcmhd_move_area_v50 = execute
except NameError:
    _productivity_execute_before_summary_restore_bcmhd_move_area_v50 = None


def _productivity_v50_is_target(filters):
    filters = frappe._dict(filters or {})
    basis = str(filters.get("bcm_basis") or "").strip().lower()
    view = str(filters.get("summary_view") or "").strip().lower()

    return basis.startswith("actual") and view == "summary per machine"


def _productivity_v50_restore_bcmhd_column(columns):
    columns = list(columns or [])

    has_bcm_hd = False
    productivity_index = None

    for idx, column in enumerate(columns):
        if not hasattr(column, "get"):
            continue

        fieldname = str(column.get("fieldname") or "").strip()

        if fieldname == "productivity_bcm_hd":
            has_bcm_hd = True

        if fieldname == "productivity":
            productivity_index = idx

    if not has_bcm_hd:
        new_col = frappe._dict({
            "label": "Productivity (BCM/HD)",
            "fieldname": "productivity_bcm_hd",
            "fieldtype": "Float",
            "width": 150,
            "precision": 2,
        })

        if productivity_index is not None:
            columns.insert(productivity_index + 1, new_col)
        else:
            columns.append(new_col)

    return columns


def _productivity_v50_apply_row_logic(rows):
    rows = list(rows or [])

    current_category = ""
    current_machine = None
    current_parent_material = None

    for row in rows:
        if not isinstance(row, dict):
            continue

        indent = int(row.get("indent") or 0)
        label = str(row.get("label") or "").strip()

        if indent == 0:
            current_category = label
            current_machine = None
            current_parent_material = None
            continue

        if current_category not in ("ADT", "Dozer"):
            continue

        # Machine total row (ADT01 / IS0601 / Dozer etc.)
        if indent == 1:
            current_machine = label
            current_parent_material = None

            row["material"] = row.get("material") or ""
            row["from_area"] = row.get("from_area") or ""
            row["to_area"] = row.get("to_area") or ""
            row["hauling_distance_m"] = row.get("hauling_distance_m") or ""
            continue

        # Parent material row (Coal / Hards / Softs / etc.)
        if indent == 2:
            current_parent_material = {
                "label": label,
                "from_area": row.get("from_area") or "",
                "to_area": row.get("to_area") or "",
                "hauling_distance_m": row.get("hauling_distance_m") or "",
            }

            # show material name on parent row
            row["material"] = label

            # keep BCM/HD here, but blank area columns here
            row["from_area"] = ""
            row["to_area"] = ""
            row["hauling_distance_m"] = ""
            continue

        # Breakdown rows under parent material
        if indent >= 3 and current_parent_material:
            if not row.get("material"):
                row["material"] = label

            if not row.get("from_area"):
                row["from_area"] = current_parent_material.get("from_area") or ""

            if not row.get("to_area"):
                row["to_area"] = current_parent_material.get("to_area") or ""

            if not row.get("hauling_distance_m"):
                row["hauling_distance_m"] = current_parent_material.get("hauling_distance_m") or ""

    return rows


def _productivity_v50_apply(result, filters):
    if not result:
        return result

    if not _productivity_v50_is_target(filters):
        return result

    parts = list(result)

    if len(parts) >= 1:
        parts[0] = _productivity_v50_restore_bcmhd_column(parts[0])

    if len(parts) >= 2:
        parts[1] = _productivity_v50_apply_row_logic(parts[1])

    if isinstance(result, tuple):
        return tuple(parts)

    return parts


def execute(filters=None):
    result = _productivity_execute_before_summary_restore_bcmhd_move_area_v50(filters)
    return _productivity_v50_apply(result, filters)

# END KOSI_PRODUCTIVITY_SUMMARY_RESTORE_BCMHD_MOVE_AREA_V50


# ============================================================
# KOSI_PRODUCTIVITY_AREA_ON_BREAKDOWN_V51
#
# ACTUAL BCMs / SUMMARY PER MACHINE
#
# ADT + DOZER
#
# BROAD MATERIAL:
#
#     Coal
#     Hards
#     Softs
#
# KEEP:
#     Productivity (BCM/HD)
#
# BLANK:
#     From Area
#     To Area
#     Hauling Distance
#
#
# MATERIAL BREAKDOWN:
#
#     Ramp 2 - 3 2#Coal
#     Ramp 1 - 2 2#Coal
#     Ramp 1 Overburden
#     etc.
#
# SHOW:
#     From Area
#     To Area
#     Hauling Distance
#
# BLANK:
#     Productivity (BCM/HD)
#
# Do not depend on indent because later patches flatten
# material rows to indent 1.
# ============================================================


_productivity_execute_before_area_on_breakdown_v51 = execute


def _productivity_v51_is_target(filters):

    filters = frappe._dict(
        filters
        or {}
    )

    basis = str(
        filters.get(
            "bcm_basis"
        )
        or ""
    ).strip().lower()

    view = str(
        filters.get(
            "summary_view"
        )
        or ""
    ).strip().lower()

    return (
        basis.startswith(
            "actual"
        )
        and view
        == "summary per machine"
    )


def _productivity_v51_rows(rows):

    rows = [
        dict(row)
        if hasattr(
            row,
            "get",
        )
        else row

        for row in (
            rows
            or []
        )
    ]

    categories = (
        "Excavator",
        "ADT",
        "Dozer",
    )

    broad_materials = {
        "coal",
        "hards",
        "softs",
    }

    current_category = ""
    current_machine = ""
    current_parent = None


    for row in rows:

        if not hasattr(
            row,
            "get",
        ):

            continue


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()


        # ----------------------------------------------------
        # MONTH / CATEGORY / TOTAL FLEET
        # ----------------------------------------------------

        if label.startswith(
            "MONTHLY PRODUCTION:"
        ):

            current_category = ""
            current_machine = ""
            current_parent = None

            continue


        if label in categories:

            current_category = label
            current_machine = ""
            current_parent = None

            continue


        if label == "Total Fleet":

            current_category = ""
            current_machine = ""
            current_parent = None

            continue


        if current_category not in (
            "ADT",
            "Dozer",
        ):

            continue


        # ----------------------------------------------------
        # MACHINE TOTAL
        #
        # No material value.
        # ----------------------------------------------------

        if (
            not material
            and label
        ):

            current_machine = label
            current_parent = None

            row[
                "from_area"
            ] = ""

            row[
                "to_area"
            ] = ""

            row[
                "hauling_distance_m"
            ] = ""

            row[
                "productivity_bcm_hd"
            ] = ""

            continue


        if not material:

            continue


        material_lower = (
            material.lower()
        )


        # ----------------------------------------------------
        # BROAD MATERIAL PARENT
        #
        # Coal / Hards / Softs
        #
        # Save area values first, then blank them from parent.
        # BCM/HD remains on this row.
        # ----------------------------------------------------

        if material_lower in broad_materials:

            current_parent = {
                "machine":
                    current_machine,

                "material":
                    material,

                "from_area":
                    str(
                        row.get(
                            "from_area"
                        )
                        or ""
                    ).strip(),

                "to_area":
                    str(
                        row.get(
                            "to_area"
                        )
                        or ""
                    ).strip(),

                "hauling_distance_m":
                    str(
                        row.get(
                            "hauling_distance_m"
                        )
                        or ""
                    ).strip(),
            }


            row[
                "productivity_summary_area_parent_v51"
            ] = 1


            row[
                "from_area"
            ] = ""

            row[
                "to_area"
            ] = ""

            row[
                "hauling_distance_m"
            ] = ""


            # DO NOT clear productivity_bcm_hd here.
            # Coal/Hards/Softs keeps BCM/HD.

            continue


        # ----------------------------------------------------
        # MATERIAL BREAKDOWN
        #
        # Put area data here.
        # ----------------------------------------------------

        if current_parent:

            # Use breakdown's own captured From Area first.
            # Fall back to parent value only when blank.

            child_from = str(
                row.get(
                    "from_area"
                )
                or ""
            ).strip()


            child_to = str(
                row.get(
                    "to_area"
                )
                or ""
            ).strip()


            child_distance = str(
                row.get(
                    "hauling_distance_m"
                )
                or ""
            ).strip()


            if not child_from:

                child_from = (
                    current_parent[
                        "from_area"
                    ]
                )


            if not child_to:

                child_to = (
                    current_parent[
                        "to_area"
                    ]
                )


            if not child_distance:

                child_distance = (
                    current_parent[
                        "hauling_distance_m"
                    ]
                )


            row[
                "from_area"
            ] = child_from

            row[
                "to_area"
            ] = child_to

            row[
                "hauling_distance_m"
            ] = child_distance


            # BCM/HD belongs on the broad material parent,
            # not the breakdown.
            row[
                "productivity_bcm_hd"
            ] = ""


            row[
                "productivity_summary_area_detail_v51"
            ] = 1


            row[
                "productivity_summary_area_parent_material_v51"
            ] = (
                current_parent[
                    "material"
                ]
            )


    return rows


def _productivity_apply_area_on_breakdown_v51(
    result,
    filters,
):

    if not result:

        return result


    if not (
        _productivity_v51_is_target(
            filters
        )
    ):

        return result


    parts = list(
        result
    )


    if len(
        parts
    ) >= 2:

        parts[
            1
        ] = (
            _productivity_v51_rows(
                parts[
                    1
                ]
            )
        )


    if isinstance(
        result,
        tuple,
    ):

        return tuple(
            parts
        )


    return parts


def execute(filters=None):

    result = (
        _productivity_execute_before_area_on_breakdown_v51(
            filters
        )
    )


    return (
        _productivity_apply_area_on_breakdown_v51(
            result,
            filters,
        )
    )


# END KOSI_PRODUCTIVITY_AREA_ON_BREAKDOWN_V51


# ============================================================
# KOSI_PRODUCTIVITY_BREAKDOWN_AREA_EDIT_V52
#
# ACTUAL BCMs / SUMMARY PER MACHINE
#
# Material breakdown rows become permanently editable:
#
#     From Area
#     To Area
#     Hauling Distance (M)
#
# Example:
#
# Coal
#     Ramp 2 - 3 2#Coal
#         From = Ramp 1
#         To = RAMP 2
#         Distance = 500-1000
#
# Parent Coal/Hards/Softs remains blank for Area/To/Distance.
#
# Existing save_productivity_area_override() is reused.
# ============================================================


_productivity_execute_before_breakdown_area_edit_v52 = execute


def _productivity_v52_plans(filters):

    import json

    raw = (
        filters.get(
            "monthly_production_plans"
        )
        or filters.get(
            "monthly_production_plan"
        )
        or filters.get(
            "monthly_production_planning"
        )
        or []
    )

    if isinstance(
        raw,
        (
            list,
            tuple,
            set,
        ),
    ):

        values = list(
            raw
        )

    elif isinstance(
        raw,
        str,
    ):

        text = raw.strip()

        if not text:

            values = []

        elif text.startswith(
            "["
        ):

            try:

                parsed = json.loads(
                    text
                )

                values = (
                    parsed
                    if isinstance(
                        parsed,
                        list,
                    )
                    else [
                        parsed
                    ]
                )

            except Exception:

                values = [
                    text
                ]

        else:

            values = [
                text
            ]

    else:

        values = [
            raw
        ] if raw else []


    return [
        str(
            value
            or ""
        ).strip()

        for value in values

        if str(
            value
            or ""
        ).strip()
    ]


def _productivity_v52_is_target(
    filters,
):

    filters = frappe._dict(
        filters
        or {}
    )

    basis = str(
        filters.get(
            "bcm_basis"
        )
        or ""
    ).strip().lower()

    view = str(
        filters.get(
            "summary_view"
        )
        or ""
    ).strip().lower()

    return (
        basis.startswith(
            "actual"
        )
        and view
        == "summary per machine"
    )


def _productivity_v52_prepare_rows(
    rows,
    filters,
):

    filters = frappe._dict(
        filters
        or {}
    )

    site = str(
        filters.get(
            "site"
        )
        or filters.get(
            "location"
        )
        or ""
    ).strip()

    start_date = (
        filters.get(
            "start_date"
        )
        or filters.get(
            "from_date"
        )
        or ""
    )

    end_date = (
        filters.get(
            "end_date"
        )
        or filters.get(
            "to_date"
        )
        or ""
    )

    shift = str(
        filters.get(
            "shift"
        )
        or ""
    ).strip()

    plans = (
        _productivity_v52_plans(
            filters
        )
    )

    current_plan = (
        plans[
            0
        ]
        if len(
            plans
        ) == 1
        else ""
    )

    current_category = ""
    current_machine = ""

    editable_rows = []

    categories = (
        "Excavator",
        "ADT",
        "Dozer",
    )


    for row in (
        rows
        or []
    ):

        if not hasattr(
            row,
            "get",
        ):

            continue


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()


        # ----------------------------------------------------
        # MONTH HEADER
        # ----------------------------------------------------

        if label.startswith(
            "MONTHLY PRODUCTION:"
        ):

            current_category = ""
            current_machine = ""

            continue


        # ----------------------------------------------------
        # CATEGORY
        # ----------------------------------------------------

        if label in categories:

            current_category = label
            current_machine = ""

            continue


        if label == "Total Fleet":

            current_category = ""
            current_machine = ""

            continue


        if current_category not in (
            "ADT",
            "Dozer",
        ):

            continue


        # ----------------------------------------------------
        # MACHINE TOTAL
        # ----------------------------------------------------

        if (
            not material
            and label
        ):

            current_machine = label

            continue


        # ----------------------------------------------------
        # ONLY V51 BREAKDOWN ROWS
        # ----------------------------------------------------

        if not row.get(
            "productivity_summary_area_detail_v51"
        ):

            continue


        if not (
            current_machine
            and material
        ):

            continue


        row_plan = str(
            row.get(
                "productivity_summary_plan_v36"
            )
            or row.get(
                "productivity_summary_plan_v47"
            )
            or current_plan
            or ""
        ).strip()


        # Exact detail row key:
        #
        # category = ADT / Dozer
        # machine  = ADT01 / machine fleet number
        # material = Ramp 2 - 3 2#Coal / Ramp 1 Overburden etc.
        base_key = (
            _productivity_manual_edit_key(
                site,
                start_date,
                end_date,
                shift,
                row_plan,
                current_category,
                current_machine,
                material,
            )
        )


        row[
            "row_key"
        ] = base_key


        row[
            "productivity_summary_area_edit_v52"
        ] = 1


        row[
            "productivity_v52_site"
        ] = site


        row[
            "productivity_v52_start_date"
        ] = str(
            start_date
            or ""
        )


        row[
            "productivity_v52_end_date"
        ] = str(
            end_date
            or ""
        )


        row[
            "productivity_v52_shift"
        ] = shift


        row[
            "productivity_v52_plan"
        ] = row_plan


        row[
            "productivity_v52_category"
        ] = current_category


        row[
            "productivity_v52_machine"
        ] = current_machine


        row[
            "productivity_v52_material"
        ] = material


        editable_rows.append(
            row
        )


    # --------------------------------------------------------
    # APPLY EXISTING PERMANENT OVERRIDES
    #
    # This makes saved values return after browser refresh.
    # --------------------------------------------------------

    row_keys = [
        str(
            row.get(
                "row_key"
            )
            or ""
        ).strip()

        for row in editable_rows

        if str(
            row.get(
                "row_key"
            )
            or ""
        ).strip()
    ]


    if not row_keys:

        return rows


    saved_rows = frappe.get_all(
        "Productivity Area Override",
        filters={
            "row_key": [
                "in",
                row_keys,
            ]
        },
        fields=[
            "row_key",
            "from_area",
            "to_area",
            "hauling_distance_m",
        ],
        limit_page_length=0,
    )


    saved_map = {
        str(
            item.row_key
            or ""
        ).strip():
            item

        for item in saved_rows
    }


    for row in editable_rows:

        row_key = str(
            row.get(
                "row_key"
            )
            or ""
        ).strip()


        saved = saved_map.get(
            row_key
        )


        if not saved:

            continue


        row[
            "from_area"
        ] = str(
            saved.from_area
            or ""
        ).strip()


        row[
            "to_area"
        ] = str(
            saved.to_area
            or ""
        ).strip()


        row[
            "hauling_distance_m"
        ] = str(
            saved.hauling_distance_m
            or ""
        ).strip()


    return rows


def _productivity_apply_breakdown_area_edit_v52(
    result,
    filters,
):

    if not result:

        return result


    if not (
        _productivity_v52_is_target(
            filters
        )
    ):

        return result


    parts = list(
        result
    )


    if len(
        parts
    ) >= 2:

        parts[
            1
        ] = (
            _productivity_v52_prepare_rows(
                parts[
                    1
                ],
                filters,
            )
        )


    if isinstance(
        result,
        tuple,
    ):

        return tuple(
            parts
        )


    return parts


def execute(filters=None):

    result = (
        _productivity_execute_before_breakdown_area_edit_v52(
            filters
        )
    )


    return (
        _productivity_apply_breakdown_area_edit_v52(
            result,
            filters,
        )
    )


# END KOSI_PRODUCTIVITY_BREAKDOWN_AREA_EDIT_V52


# ============================================================
# KOSI_PRODUCTIVITY_MATERIAL_HEADING_V53
#
# ACTUAL BCMs / SUMMARY PER MACHINE
#
# Broad material rows:
#
#     Coal
#     Hards
#     Softs
#
# become heading rows.
#
# REMOVE BCM/HD VALUE FROM THESE ROWS ONLY.
#
# Keep:
#     Working Hours
#     Output
#     Productivity BCM/HR
#     Material
#
# Breakdown rows remain unchanged.
# ============================================================


_productivity_execute_before_material_heading_v53 = execute


def _productivity_v53_is_target(
    filters,
):

    filters = frappe._dict(
        filters
        or {}
    )

    basis = str(
        filters.get(
            "bcm_basis"
        )
        or ""
    ).strip().lower()

    view = str(
        filters.get(
            "summary_view"
        )
        or ""
    ).strip().lower()

    return (
        basis.startswith(
            "actual"
        )
        and view
        == "summary per machine"
    )


def _productivity_v53_rows(
    rows,
):

    broad_materials = {
        "coal",
        "hards",
        "softs",
    }


    for row in (
        rows
        or []
    ):

        if not hasattr(
            row,
            "get",
        ):

            continue


        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()


        if (
            material.lower()
            in broad_materials
        ):

            # Remove 0.15 / 0.64 / etc.
            row[
                "productivity_bcm_hd"
            ] = ""


            row[
                "productivity_material_heading_v53"
            ] = 1


    return rows


def _productivity_apply_material_heading_v53(
    result,
    filters,
):

    if not result:

        return result


    if not (
        _productivity_v53_is_target(
            filters
        )
    ):

        return result


    parts = list(
        result
    )


    if len(
        parts
    ) >= 2:

        parts[
            1
        ] = (
            _productivity_v53_rows(
                parts[
                    1
                ]
            )
        )


    if isinstance(
        result,
        tuple,
    ):

        return tuple(
            parts
        )


    return parts


def execute(filters=None):

    result = (
        _productivity_execute_before_material_heading_v53(
            filters
        )
    )


    return (
        _productivity_apply_material_heading_v53(
            result,
            filters,
        )
    )


# END KOSI_PRODUCTIVITY_MATERIAL_HEADING_V53


# ============================================================
# KOSI_PRODUCTIVITY_MANUAL_TO_HD_V54
#
# ACTUAL BCMs / SUMMARY PER MACHINE
#
# RULE:
#
# From Area:
#     May come from captured/system source.
#
# To Area:
#     MUST NOT be automatically fetched.
#     Blank until user captures it.
#
# Hauling Distance (M):
#     MUST NOT be automatically fetched.
#     Blank until user captures it.
#
# If the user already saved a Productivity Area Override,
# restore that saved To Area / Hauling Distance.
#
# Summary Per Machine only.
# Hours and Material is untouched.
# ============================================================


_productivity_execute_before_manual_to_hd_v54 = execute


def _productivity_v54_is_target(
    filters,
):

    filters = frappe._dict(
        filters
        or {}
    )

    basis = str(
        filters.get(
            "bcm_basis"
        )
        or ""
    ).strip().lower()

    view = str(
        filters.get(
            "summary_view"
        )
        or ""
    ).strip().lower()

    return (
        basis.startswith(
            "actual"
        )
        and view
        == "summary per machine"
    )


def _productivity_v54_saved_override_map(
    rows,
):

    candidate_keys = []

    row_candidates = {}


    for row in (
        rows
        or []
    ):

        if not hasattr(
            row,
            "get",
        ):

            continue


        if not row.get(
            "productivity_summary_area_edit_v52"
        ):

            continue


        base_key = str(
            row.get(
                "row_key"
            )
            or ""
        ).strip()


        if not base_key:

            continue


        keys = [
            base_key,
        ]


        # Current system keeps Summary Per Machine overrides
        # separate from Hours and Material.
        try:

            scoped_key = (
                _productivity_view_scope_key_v8(
                    "Summary Per Machine",
                    base_key,
                )
            )

        except Exception:

            scoped_key = (
                "VIEW::Summary Per Machine::"
                + base_key
            )


        if (
            scoped_key
            and scoped_key
            not in keys
        ):

            keys.append(
                scoped_key
            )


        row_candidates[
            base_key
        ] = keys


        for key in keys:

            if (
                key
                and key not in candidate_keys
            ):

                candidate_keys.append(
                    key
                )


    if not candidate_keys:

        return {}


    saved_rows = frappe.get_all(
        "Productivity Area Override",
        filters={
            "row_key": [
                "in",
                candidate_keys,
            ]
        },
        fields=[
            "name",
            "row_key",
            "from_area",
            "to_area",
            "hauling_distance_m",
            "modified",
        ],
        order_by="modified asc",
        limit_page_length=0,
    )


    by_key = {
        str(
            item.row_key
            or ""
        ).strip():
            item

        for item in saved_rows
    }


    result = {}


    for base_key, keys in (
        row_candidates.items()
    ):

        selected = None


        # Prefer the report-view-specific override.
        for key in reversed(
            keys
        ):

            if key in by_key:

                selected = (
                    by_key[
                        key
                    ]
                )

                break


        if selected:

            result[
                base_key
            ] = selected


    return result


def _productivity_v54_apply_rows(
    rows,
):

    rows = [
        dict(
            row
        )
        if hasattr(
            row,
            "get",
        )
        else row

        for row in (
            rows
            or []
        )
    ]


    saved_map = (
        _productivity_v54_saved_override_map(
            rows
        )
    )


    for row in rows:

        if not hasattr(
            row,
            "get",
        ):

            continue


        # ====================================================
        # BROAD MATERIAL HEADINGS
        #
        # Coal / Hards / Softs:
        #
        # No To Area
        # No Hauling Distance
        # ====================================================

        if row.get(
            "productivity_material_heading_v53"
        ):

            row[
                "to_area"
            ] = ""

            row[
                "hauling_distance_m"
            ] = ""

            continue


        # ====================================================
        # BREAKDOWN ROW
        #
        # From Area remains as supplied by the system.
        #
        # To Area + Hauling Distance:
        #
        #     use SAVED USER OVERRIDE
        #     otherwise BLANK.
        # ====================================================

        if not row.get(
            "productivity_summary_area_edit_v52"
        ):

            continue


        base_key = str(
            row.get(
                "row_key"
            )
            or ""
        ).strip()


        saved = (
            saved_map.get(
                base_key
            )
        )


        if saved:

            row[
                "to_area"
            ] = str(
                saved.to_area
                or ""
            ).strip()


            row[
                "hauling_distance_m"
            ] = str(
                saved.hauling_distance_m
                or ""
            ).strip()


            row[
                "productivity_manual_to_hd_saved_v54"
            ] = 1


        else:

            # CRITICAL V54 RULE:
            #
            # Never inherit these from:
            #
            # - parent material
            # - Hourly Production
            # - area map
            # - another machine
            # - another material
            # - another report view

            row[
                "to_area"
            ] = ""


            row[
                "hauling_distance_m"
            ] = ""


            row[
                "productivity_manual_to_hd_saved_v54"
            ] = 0


        row[
            "productivity_manual_to_hd_v54"
        ] = 1


    return rows


def _productivity_apply_manual_to_hd_v54(
    result,
    filters,
):

    if not result:

        return result


    if not (
        _productivity_v54_is_target(
            filters
        )
    ):

        return result


    parts = list(
        result
    )


    if len(
        parts
    ) >= 2:

        parts[
            1
        ] = (
            _productivity_v54_apply_rows(
                parts[
                    1
                ]
            )
        )


    if isinstance(
        result,
        tuple,
    ):

        return tuple(
            parts
        )


    return parts


def execute(filters=None):

    result = (
        _productivity_execute_before_manual_to_hd_v54(
            filters
        )
    )


    return (
        _productivity_apply_manual_to_hd_v54(
            result,
            filters,
        )
    )


# END KOSI_PRODUCTIVITY_MANUAL_TO_HD_V54


# ============================================================
# KOSI_PRODUCTIVITY_DOZER_BREAKDOWN_V55
#
# ACTUAL BCMs / SUMMARY PER MACHINE
#
# DOZER FORMAT:
#
# IS0338
#
#     1 - Overburden          <- bold heading
#         Midburden Dozing    <- editable route row
#
#     2 - Midburden           <- bold heading
#         Midburden Dozing    <- editable route row
#
#
# HEADING ROW:
#
# - Material bold
# - BCM/HD blank
# - From Area blank
# - To Area blank
# - Hauling Distance blank
#
#
# BREAKDOWN ROW:
#
# - From Area comes from system / parent material
# - To Area USER CAPTURE ONLY
# - Hauling Distance USER CAPTURE ONLY
# - saved permanently in Summary Per Machine scope
#
#
# IMPORTANT:
#
# Dozer detail names can repeat.
#
# Example IS0338:
#
#     1 - Overburden :: Midburden Dozing
#     2 - Midburden  :: Midburden Dozing
#
# Therefore the SAVE material key contains BOTH the
# parent material and detail material to avoid collisions.
#
# Hours and Material is untouched.
# ADT is untouched.
# Excavator is untouched.
# ============================================================


_productivity_execute_before_dozer_breakdown_v55 = execute


def _productivity_v55_is_target(filters):

    filters = frappe._dict(
        filters
        or {}
    )

    basis = str(
        filters.get(
            "bcm_basis"
        )
        or ""
    ).strip().lower()

    view = str(
        filters.get(
            "summary_view"
        )
        or ""
    ).strip().lower()

    return (
        basis.startswith(
            "actual"
        )
        and view
        == "summary per machine"
    )


def _productivity_v55_plans(filters):

    import json

    raw = (
        filters.get(
            "monthly_production_plans"
        )
        or filters.get(
            "monthly_production_plan"
        )
        or []
    )


    if isinstance(
        raw,
        (
            list,
            tuple,
            set,
        ),
    ):

        values = list(
            raw
        )


    elif isinstance(
        raw,
        str,
    ):

        text = raw.strip()

        if not text:

            values = []

        elif text.startswith(
            "["
        ):

            try:

                parsed = json.loads(
                    text
                )

                values = (
                    parsed
                    if isinstance(
                        parsed,
                        list,
                    )
                    else [
                        parsed
                    ]
                )

            except Exception:

                values = [
                    text
                ]

        else:

            values = [
                text
            ]


    else:

        values = [
            raw
        ] if raw else []


    return [
        str(
            value
            or ""
        ).strip()

        for value in values

        if str(
            value
            or ""
        ).strip()
    ]


def _productivity_v55_prepare_dozer_rows(
    rows,
    filters,
):

    filters = frappe._dict(
        filters
        or {}
    )


    rows = [
        dict(
            row
        )
        if hasattr(
            row,
            "get",
        )
        else row

        for row in (
            rows
            or []
        )
    ]


    site = str(
        filters.get(
            "site"
        )
        or filters.get(
            "location"
        )
        or ""
    ).strip()


    start_date = (
        filters.get(
            "start_date"
        )
        or filters.get(
            "from_date"
        )
        or ""
    )


    end_date = (
        filters.get(
            "end_date"
        )
        or filters.get(
            "to_date"
        )
        or ""
    )


    shift = str(
        filters.get(
            "shift"
        )
        or ""
    ).strip()


    plans = (
        _productivity_v55_plans(
            filters
        )
    )


    default_plan = (
        plans[
            0
        ]
        if len(
            plans
        ) == 1
        else ""
    )


    current_category = ""
    current_machine = ""

    current_parent_material = ""
    current_parent_from_area = ""

    editable_rows = []


    for row in rows:

        if not hasattr(
            row,
            "get",
        ):

            continue


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()


        # ----------------------------------------------------
        # MONTH HEADER
        # ----------------------------------------------------

        if label.startswith(
            "MONTHLY PRODUCTION:"
        ):

            current_category = ""
            current_machine = ""
            current_parent_material = ""
            current_parent_from_area = ""

            continue


        # ----------------------------------------------------
        # CATEGORY
        # ----------------------------------------------------

        if label in (
            "Excavator",
            "ADT",
            "Dozer",
        ):

            current_category = label

            current_machine = ""
            current_parent_material = ""
            current_parent_from_area = ""

            continue


        if label == "Total Fleet":

            current_category = ""
            current_machine = ""
            current_parent_material = ""
            current_parent_from_area = ""

            continue


        if current_category != "Dozer":

            continue


        # ----------------------------------------------------
        # MACHINE TOTAL
        # ----------------------------------------------------

        if (
            not material
            and label
        ):

            current_machine = label

            current_parent_material = ""
            current_parent_from_area = ""

            row[
                "from_area"
            ] = ""

            row[
                "to_area"
            ] = ""

            row[
                "hauling_distance_m"
            ] = ""

            row[
                "productivity_bcm_hd"
            ] = ""

            continue


        if not material:

            continue


        material_lower = (
            material.lower()
        )


        # ----------------------------------------------------
        # DOZER DETAIL ROW
        #
        # Examples:
        #
        # Midburden Dozing
        # Production Dozing
        #
        # Also honour an existing V36 detail marker.
        # ----------------------------------------------------

        is_detail = (
            bool(
                row.get(
                    "productivity_summary_captured_detail_v36"
                )
            )
            or "dozing"
            in material_lower
        )


        # ====================================================
        # PARENT MATERIAL
        #
        # 1 - Overburden
        # 2 - Midburden
        # etc.
        # ====================================================

        if not is_detail:

            current_parent_material = (
                material
            )


            current_parent_from_area = str(
                row.get(
                    "from_area"
                )
                or ""
            ).strip()


            row[
                "productivity_dozer_heading_v55"
            ] = 1


            row[
                "productivity_bcm_hd"
            ] = ""


            row[
                "from_area"
            ] = ""


            row[
                "to_area"
            ] = ""


            row[
                "hauling_distance_m"
            ] = ""


            continue


        # ====================================================
        # DETAIL ROW
        # ====================================================

        if not (
            current_machine
            and current_parent_material
        ):

            continue


        # Important:
        # Two different parent materials can both have a
        # "Midburden Dozing" child.
        #
        # Therefore use a unique SAVE material identity.

        save_material = (
            current_parent_material
            + " :: "
            + material
        )


        row_plan = str(
            row.get(
                "productivity_summary_plan_v36"
            )
            or row.get(
                "productivity_summary_plan_v47"
            )
            or default_plan
            or ""
        ).strip()


        base_key = (
            _productivity_manual_edit_key(
                site,
                start_date,
                end_date,
                shift,
                row_plan,
                "Dozer",
                current_machine,
                save_material,
            )
        )


        scoped_key = (
            _productivity_view_scope_key_v8(
                "Summary Per Machine",
                base_key,
            )
        )


        row[
            "row_key"
        ] = scoped_key


        row[
            "productivity_dozer_detail_v55"
        ] = 1


        row[
            "productivity_bcm_hd"
        ] = ""


        # System From Area.
        row[
            "from_area"
        ] = (
            current_parent_from_area
        )


        # User capture only.
        row[
            "to_area"
        ] = ""


        row[
            "hauling_distance_m"
        ] = ""


        row[
            "productivity_v55_site"
        ] = site


        row[
            "productivity_v55_start_date"
        ] = str(
            start_date
            or ""
        )


        row[
            "productivity_v55_end_date"
        ] = str(
            end_date
            or ""
        )


        row[
            "productivity_v55_shift"
        ] = shift


        row[
            "productivity_v55_plan"
        ] = row_plan


        row[
            "productivity_v55_category"
        ] = "Dozer"


        row[
            "productivity_v55_machine"
        ] = current_machine


        row[
            "productivity_v55_parent_material"
        ] = current_parent_material


        row[
            "productivity_v55_display_material"
        ] = material


        row[
            "productivity_v55_save_material"
        ] = save_material


        editable_rows.append(
            row
        )


    # ========================================================
    # RESTORE SAVED USER OVERRIDES
    # ========================================================

    keys = [
        str(
            row.get(
                "row_key"
            )
            or ""
        ).strip()

        for row in editable_rows

        if str(
            row.get(
                "row_key"
            )
            or ""
        ).strip()
    ]


    if not keys:

        return rows


    saved = frappe.get_all(
        "Productivity Area Override",
        filters={
            "row_key": [
                "in",
                keys,
            ]
        },
        fields=[
            "row_key",
            "from_area",
            "to_area",
            "hauling_distance_m",
        ],
        limit_page_length=0,
    )


    saved_map = {
        str(
            item.row_key
            or ""
        ).strip():
            item

        for item in saved
    }


    for row in editable_rows:

        key = str(
            row.get(
                "row_key"
            )
            or ""
        ).strip()


        override = (
            saved_map.get(
                key
            )
        )


        if not override:

            continue


        # From Area may also be manually corrected by user.
        row[
            "from_area"
        ] = str(
            override.from_area
            or ""
        ).strip()


        # User captured only.
        row[
            "to_area"
        ] = str(
            override.to_area
            or ""
        ).strip()


        row[
            "hauling_distance_m"
        ] = str(
            override.hauling_distance_m
            or ""
        ).strip()


        row[
            "productivity_dozer_saved_v55"
        ] = 1


    return rows


def _productivity_apply_dozer_breakdown_v55(
    result,
    filters,
):

    if not result:

        return result


    if not (
        _productivity_v55_is_target(
            filters
        )
    ):

        return result


    parts = list(
        result
    )


    if len(
        parts
    ) >= 2:

        parts[
            1
        ] = (
            _productivity_v55_prepare_dozer_rows(
                parts[
                    1
                ],
                filters,
            )
        )


    if isinstance(
        result,
        tuple,
    ):

        return tuple(
            parts
        )


    return parts


def execute(filters=None):

    result = (
        _productivity_execute_before_dozer_breakdown_v55(
            filters
        )
    )


    return (
        _productivity_apply_dozer_breakdown_v55(
            result,
            filters,
        )
    )


# END KOSI_PRODUCTIVITY_DOZER_BREAKDOWN_V55


# ============================================================
# KOSI_PRODUCTIVITY_DOZER_ROW_CALC_V56
#
# ACTUAL BCMs / SUMMARY PER MACHINE / DOZER
#
# Each Dozer row calculates its OWN BCM/HR:
#
#     Productivity BCM/HR
#         =
#     Output BCM / Working Hours
#
# Applies to:
#
# - Dozer machine total
# - Dozer material heading
# - Dozer material detail
#
# Examples:
#
# 28 BCM / 3 Hours
#     = 9.33
#     = 9 BCM/HR displayed
#
# 3071 BCM / 364 Hours
#     = 8.44
#     = 8 BCM/HR displayed
#
# Keep 0 decimal display.
#
# No Output or Hours are changed.
# ============================================================


_productivity_execute_before_dozer_row_calc_v56 = execute


def _productivity_v56_float(value):

    try:

        return float(
            value
            or 0
        )

    except Exception:

        return 0.0


def _productivity_v56_is_target(filters):

    filters = frappe._dict(
        filters
        or {}
    )

    basis = str(
        filters.get(
            "bcm_basis"
        )
        or ""
    ).strip().lower()

    view = str(
        filters.get(
            "summary_view"
        )
        or ""
    ).strip().lower()

    return (
        basis.startswith(
            "actual"
        )
        and view
        == "summary per machine"
    )


def _productivity_v56_apply_rows(rows):

    current_category = ""

    categories = (
        "Excavator",
        "ADT",
        "Dozer",
    )


    for row in (
        rows
        or []
    ):

        if not hasattr(
            row,
            "get",
        ):

            continue


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        if label.startswith(
            "MONTHLY PRODUCTION:"
        ):

            current_category = ""

            continue


        if label in categories:

            current_category = label

            continue


        if label == "Total Fleet":

            current_category = ""

            continue


        if current_category != "Dozer":

            continue


        hours = (
            _productivity_v56_float(
                row.get(
                    "working_hours"
                )
            )
        )


        output = (
            _productivity_v56_float(
                row.get(
                    "output"
                )
            )
        )


        if hours > 0:

            productivity = round(
                output / hours
            )

        else:

            productivity = 0


        row[
            "productivity"
        ] = productivity


        row[
            "productivity_dozer_row_calc_v56"
        ] = 1


    return rows


def _productivity_v56_apply_columns(columns):

    for column in (
        columns
        or []
    ):

        if not hasattr(
            column,
            "get",
        ):

            continue


        if (
            str(
                column.get(
                    "fieldname"
                )
                or ""
            ).strip()
            == "productivity"
        ):

            column[
                "precision"
            ] = 0


    return columns


def _productivity_apply_dozer_row_calc_v56(
    result,
    filters,
):

    if not result:

        return result


    if not (
        _productivity_v56_is_target(
            filters
        )
    ):

        return result


    parts = list(
        result
    )


    if len(
        parts
    ) >= 1:

        parts[
            0
        ] = (
            _productivity_v56_apply_columns(
                parts[
                    0
                ]
            )
        )


    if len(
        parts
    ) >= 2:

        parts[
            1
        ] = (
            _productivity_v56_apply_rows(
                parts[
                    1
                ]
            )
        )


    if isinstance(
        result,
        tuple,
    ):

        return tuple(
            parts
        )


    return parts


def execute(filters=None):

    result = (
        _productivity_execute_before_dozer_row_calc_v56(
            filters
        )
    )


    return (
        _productivity_apply_dozer_row_calc_v56(
            result,
            filters,
        )
    )


# END KOSI_PRODUCTIVITY_DOZER_ROW_CALC_V56


# ============================================================
# KOSI_PRODUCTIVITY_BREAKDOWN_BCM_HD_V57
#
# ACTUAL BCMs / SUMMARY PER MACHINE
#
# Productivity BCM/HD belongs on the BREAKDOWN ROW.
#
# Formula:
#
#     Average Hauling Distance
#         =
#     (Range Start + Range End) / 2
#
#     Productivity BCM/HD
#         =
#     Output BCM / Average Hauling Distance
#
#
# Example:
#
# Output:
#     112 BCM
#
# Hauling Distance:
#     500-1000
#
# Average:
#     750
#
# BCM/HD:
#     112 / 750
#     = 0.15
#
#
# Rules:
#
# - ADT breakdown rows = calculate
# - Dozer breakdown rows = calculate
#
# - Coal/Hards/Softs heading = blank BCM/HD
# - Dozer material heading = blank BCM/HD
# - Machine total = blank BCM/HD
#
# - User-captured Hauling Distance drives calculation.
# - Blank distance = blank BCM/HD.
# - 2 decimal display.
#
# BCM/HR is NOT changed here.
# ============================================================


_productivity_execute_before_breakdown_bcm_hd_v57 = execute


def _productivity_v57_float(value):

    try:

        return float(
            value
            or 0
        )

    except Exception:

        return 0.0


def _productivity_v57_distance(
    value,
):

    import re


    text = str(
        value
        or ""
    ).strip()


    if not text:

        return 0.0


    # Allow:
    #
    # 500
    # 500-1000
    # 500 - 1000
    # 500 to 1000
    # 500–1000
    # 500—1000

    text = (
        text
        .replace(
            "–",
            "-",
        )
        .replace(
            "—",
            "-",
        )
        .replace(
            ",",
            "",
        )
    )


    numbers = re.findall(
        r"\d+(?:\.\d+)?",
        text,
    )


    if not numbers:

        return 0.0


    values = [
        float(
            number
        )

        for number in numbers
    ]


    if len(
        values
    ) >= 2:

        start = values[
            0
        ]

        end = values[
            1
        ]


        if (
            start <= 0
            or end <= 0
        ):

            return 0.0


        return (
            start + end
        ) / 2.0


    distance = values[
        0
    ]


    if distance <= 0:

        return 0.0


    return distance


def _productivity_v57_is_target(
    filters,
):

    filters = frappe._dict(
        filters
        or {}
    )


    basis = str(
        filters.get(
            "bcm_basis"
        )
        or ""
    ).strip().lower()


    view = str(
        filters.get(
            "summary_view"
        )
        or ""
    ).strip().lower()


    return (
        basis.startswith(
            "actual"
        )
        and view
        == "summary per machine"
    )


def _productivity_v57_columns(
    columns,
):

    for column in (
        columns
        or []
    ):

        if not hasattr(
            column,
            "get",
        ):

            continue


        if (
            str(
                column.get(
                    "fieldname"
                )
                or ""
            ).strip()
            == "productivity_bcm_hd"
        ):

            column[
                "precision"
            ] = 2


    return columns


def _productivity_v57_rows(
    rows,
):

    for row in (
        rows
        or []
    ):

        if not hasattr(
            row,
            "get",
        ):

            continue


        # ====================================================
        # HEADINGS
        #
        # Never calculate BCM/HD here.
        # ====================================================

        if (
            row.get(
                "productivity_material_heading_v53"
            )
            or row.get(
                "productivity_dozer_heading_v55"
            )
        ):

            row[
                "productivity_bcm_hd"
            ] = ""

            continue


        # ====================================================
        # TARGET BREAKDOWN ROWS
        #
        # ADT:
        #     V51/V52 breakdown
        #
        # DOZER:
        #     V55 detail
        # ====================================================

        is_adt_detail = bool(
            row.get(
                "productivity_summary_area_detail_v51"
            )
            or row.get(
                "productivity_summary_area_edit_v52"
            )
        )


        is_dozer_detail = bool(
            row.get(
                "productivity_dozer_detail_v55"
            )
        )


        if not (
            is_adt_detail
            or is_dozer_detail
        ):

            continue


        output = (
            _productivity_v57_float(
                row.get(
                    "output"
                )
            )
        )


        average_distance = (
            _productivity_v57_distance(
                row.get(
                    "hauling_distance_m"
                )
            )
        )


        if (
            output > 0
            and average_distance > 0
        ):

            bcm_hd = round(
                output
                / average_distance,
                2,
            )

        else:

            bcm_hd = ""


        row[
            "productivity_bcm_hd"
        ] = bcm_hd


        row[
            "productivity_average_hd_v57"
        ] = (
            round(
                average_distance,
                2,
            )
            if average_distance > 0
            else ""
        )


        row[
            "productivity_breakdown_bcm_hd_v57"
        ] = 1


    return rows


def _productivity_apply_breakdown_bcm_hd_v57(
    result,
    filters,
):

    if not result:

        return result


    if not (
        _productivity_v57_is_target(
            filters
        )
    ):

        return result


    parts = list(
        result
    )


    if len(
        parts
    ) >= 1:

        parts[
            0
        ] = (
            _productivity_v57_columns(
                parts[
                    0
                ]
            )
        )


    if len(
        parts
    ) >= 2:

        parts[
            1
        ] = (
            _productivity_v57_rows(
                parts[
                    1
                ]
            )
        )


    if isinstance(
        result,
        tuple,
    ):

        return tuple(
            parts
        )


    return parts


def execute(filters=None):

    result = (
        _productivity_execute_before_breakdown_bcm_hd_v57(
            filters
        )
    )


    return (
        _productivity_apply_breakdown_bcm_hd_v57(
            result,
            filters,
        )
    )


# END KOSI_PRODUCTIVITY_BREAKDOWN_BCM_HD_V57


# ============================================================
# KOSI_PRODUCTIVITY_HOURS_HIDE_COAL_HARDS_LABELS_V58
#
# HOURS AND MATERIAL ONLY
#
# Keep:
#
#     Coal
#     Hards
#
# as headings.
#
# Hide the LABEL wording of their child rows:
#
# Coal
#     Ramp 2 - 3 2#Coal     -> label visually blank
#     Ramp 1 - 2 2#Coal     -> label visually blank
#
# Hards
#     Ramp 1 Overburden     -> label visually blank
#     Ramp 3 Overburden     -> label visually blank
#
# IMPORTANT:
#
# The rows are NOT deleted.
#
# Working Hours
# Output
# Productivity
# BCM/HD
# Material
# From Area
# To Area
# Hauling Distance
#
# all remain unchanged.
#
# Softs / Topsoil Dump is untouched.
# Summary Per Machine is untouched.
# ============================================================


_productivity_execute_before_hours_hide_labels_v58 = execute


def _productivity_v58_is_target(
    filters,
):

    filters = frappe._dict(
        filters
        or {}
    )

    view = str(
        filters.get(
            "summary_view"
        )
        or ""
    ).strip().lower()

    return (
        view
        == "hours and material"
    )


def _productivity_v58_indent(
    row,
):

    try:

        return int(
            row.get(
                "indent"
            )
            or 0
        )

    except Exception:

        return 0


def _productivity_v58_mark_rows(
    rows,
):

    active_parent = ""
    parent_indent = None


    for row in (
        rows
        or []
    ):

        if not hasattr(
            row,
            "get",
        ):

            continue


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        indent = (
            _productivity_v58_indent(
                row
            )
        )


        # ----------------------------------------------------
        # MONTH/CATEGORY BREAK
        # ----------------------------------------------------

        if (
            label.startswith(
                "MONTHLY PRODUCTION:"
            )
            or label in (
                "Excavator",
                "ADT",
                "Dozer",
                "Total Fleet",
            )
        ):

            active_parent = ""
            parent_indent = None

            continue


        # ----------------------------------------------------
        # TARGET PARENT
        # ----------------------------------------------------

        if label in (
            "Coal",
            "Hards",
        ):

            active_parent = label
            parent_indent = indent

            row[
                "productivity_hours_material_heading_v58"
            ] = 1

            continue


        # ----------------------------------------------------
        # ANOTHER BROAD MATERIAL ENDS TARGET
        # ----------------------------------------------------

        if label in (
            "Softs",
        ):

            active_parent = ""
            parent_indent = None

            continue


        # ----------------------------------------------------
        # CHILD UNDER COAL/HARDS
        # ----------------------------------------------------

        if (
            active_parent
            and parent_indent is not None
            and indent > parent_indent
        ):

            row[
                "productivity_hours_hide_label_v58"
            ] = 1

            row[
                "productivity_hours_parent_v58"
            ] = active_parent

            continue


        # ----------------------------------------------------
        # SAME/HIGHER LEVEL MEANS WE LEFT THE PARENT
        # ----------------------------------------------------

        if (
            active_parent
            and parent_indent is not None
            and indent <= parent_indent
        ):

            active_parent = ""
            parent_indent = None


    return rows


def _productivity_apply_hours_hide_labels_v58(
    result,
    filters,
):

    if not result:

        return result


    if not (
        _productivity_v58_is_target(
            filters
        )
    ):

        return result


    parts = list(
        result
    )


    if len(
        parts
    ) >= 2:

        parts[
            1
        ] = (
            _productivity_v58_mark_rows(
                parts[
                    1
                ]
            )
        )


    if isinstance(
        result,
        tuple,
    ):

        return tuple(
            parts
        )


    return parts


def execute(filters=None):

    result = (
        _productivity_execute_before_hours_hide_labels_v58(
            filters
        )
    )


    return (
        _productivity_apply_hours_hide_labels_v58(
            result,
            filters,
        )
    )


# END KOSI_PRODUCTIVITY_HOURS_HIDE_COAL_HARDS_LABELS_V58


# ============================================================
# KOSI_PRODUCTIVITY_HOURS_HIDE_SOFTS_DOZER_LABELS_V59
#
# HOURS AND MATERIAL ONLY
#
# COMPLETE CLEAN LABEL STRUCTURE:
#
# Softs
#     Topsoil Dump        -> label visually blank
#
# Dozer
#     2 - Midburden
#         Midburden Dozing -> label visually blank
#
#     1 - Overburden
#         Midburden Dozing -> label visually blank
#
#
# V58 already handles:
#
# Coal
#     coal detail labels -> blank
#
# Hards
#     hards detail labels -> blank
#
#
# IMPORTANT:
#
# ONLY Label wording is hidden.
#
# Rows remain completely intact:
#
# - Working Hours
# - Output
# - Productivity BCM/HR
# - Productivity BCM/HD
# - Material
# - From Area
# - To Area
# - Hauling Distance
#
# Summary Per Machine untouched.
# ============================================================


_productivity_execute_before_hours_hide_softs_dozer_v59 = execute


def _productivity_v59_is_target(
    filters,
):

    filters = frappe._dict(
        filters
        or {}
    )

    view = str(
        filters.get(
            "summary_view"
        )
        or ""
    ).strip().lower()

    return (
        view
        == "hours and material"
    )


def _productivity_v59_indent(
    row,
):

    try:

        return int(
            float(
                row.get(
                    "indent"
                )
                or 0
            )
        )

    except Exception:

        return 0


def _productivity_v59_mark_rows(
    rows,
):

    current_category = ""

    softs_active = False
    softs_indent = None

    dozer_parent_active = False
    dozer_parent_indent = None
    dozer_parent_label = ""


    for row in (
        rows
        or []
    ):

        if not hasattr(
            row,
            "get",
        ):

            continue


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        indent = (
            _productivity_v59_indent(
                row
            )
        )


        # ====================================================
        # MONTH HEADER
        # ====================================================

        if label.startswith(
            "MONTHLY PRODUCTION:"
        ):

            current_category = ""

            softs_active = False
            softs_indent = None

            dozer_parent_active = False
            dozer_parent_indent = None
            dozer_parent_label = ""

            continue


        # ====================================================
        # CATEGORY
        # ====================================================

        if label in (
            "Excavator",
            "ADT",
            "Dozer",
        ):

            current_category = label

            softs_active = False
            softs_indent = None

            dozer_parent_active = False
            dozer_parent_indent = None
            dozer_parent_label = ""

            continue


        if label == "Total Fleet":

            current_category = ""

            softs_active = False
            softs_indent = None

            dozer_parent_active = False
            dozer_parent_indent = None
            dozer_parent_label = ""

            continue


        # ====================================================
        # SOFTS
        #
        # Hide child label, e.g.:
        #
        # Softs
        #     Topsoil Dump -> blank label
        # ====================================================

        if current_category in (
            "Excavator",
            "ADT",
        ):

            if label == "Softs":

                softs_active = True
                softs_indent = indent

                continue


            if (
                softs_active
                and softs_indent is not None
                and indent > softs_indent
            ):

                row[
                    "productivity_hours_hide_label_v59"
                ] = 1

                row[
                    "productivity_hours_hidden_type_v59"
                ] = "Softs Child"

                continue


            if (
                softs_active
                and softs_indent is not None
                and indent <= softs_indent
            ):

                softs_active = False
                softs_indent = None


        # ====================================================
        # DOZER
        #
        # Keep first-level material headings:
        #
        # 2 - Midburden
        # 1 - Overburden
        #
        # Hide their children:
        #
        # Midburden Dozing
        # ====================================================

        if current_category == "Dozer":

            # First material heading beneath Dozer.
            if (
                label
                and (
                    not dozer_parent_active
                    or dozer_parent_indent is None
                    or indent <= dozer_parent_indent
                )
            ):

                dozer_parent_active = True
                dozer_parent_indent = indent
                dozer_parent_label = label

                continue


            # Child underneath Dozer material heading.
            if (
                dozer_parent_active
                and dozer_parent_indent is not None
                and indent > dozer_parent_indent
            ):

                row[
                    "productivity_hours_hide_label_v59"
                ] = 1

                row[
                    "productivity_hours_hidden_type_v59"
                ] = "Dozer Child"

                row[
                    "productivity_hours_dozer_parent_v59"
                ] = dozer_parent_label

                continue


    return rows


def _productivity_apply_hours_hide_softs_dozer_v59(
    result,
    filters,
):

    if not result:

        return result


    if not (
        _productivity_v59_is_target(
            filters
        )
    ):

        return result


    parts = list(
        result
    )


    if len(
        parts
    ) >= 2:

        parts[
            1
        ] = (
            _productivity_v59_mark_rows(
                parts[
                    1
                ]
            )
        )


    if isinstance(
        result,
        tuple,
    ):

        return tuple(
            parts
        )


    return parts


def execute(filters=None):

    result = (
        _productivity_execute_before_hours_hide_softs_dozer_v59(
            filters
        )
    )


    return (
        _productivity_apply_hours_hide_softs_dozer_v59(
            result,
            filters,
        )
    )


# END KOSI_PRODUCTIVITY_HOURS_HIDE_SOFTS_DOZER_LABELS_V59


# ============================================================
# KOSI_PRODUCTIVITY_TALLIES_BOTH_VIEWS_V60
#
# TALLIES BCMs
#
# Apply final Productivity behaviour to BOTH:
#
#   1. Summary Per Machine
#   2. Hours and Material
#
# IMPORTANT:
#
# Output remains TALLIES BCM.
# We do NOT substitute Survey Actual BCM.
#
#
# COMMON CALCULATIONS:
#
# Productivity BCM/HR
#     =
# Output Tallies BCM / Working Hours
#
#
# Productivity BCM/HD
#     =
# Output Tallies BCM / Average Hauling Distance
#
#
# Average hauling distance:
#
# 500-1000
#     =
# (500 + 1000) / 2
#     =
# 750
#
#
# SUMMARY PER MACHINE:
#
# - Excavator machine totals
# - ADT breakdown structure
# - Dozer breakdown structure
# - bold headings
# - From Area
# - manual To Area
# - manual Hauling Distance
# - BCM/HD from captured distance
#
#
# HOURS AND MATERIAL:
#
# - same cleaned heading structure
# - child Label wording hidden
# - Material column remains
# - area fields remain
# - BCM/HR calculated per row
# - BCM/HD calculated from row distance
#
# ============================================================


_productivity_execute_before_tallies_both_views_v60 = execute


def _productivity_v60_float(value):

    try:

        return float(
            value
            or 0
        )

    except Exception:

        return 0.0


def _productivity_v60_basis(filters):

    filters = frappe._dict(
        filters
        or {}
    )

    return str(
        filters.get(
            "bcm_basis"
        )
        or ""
    ).strip().lower()


def _productivity_v60_view(filters):

    filters = frappe._dict(
        filters
        or {}
    )

    return str(
        filters.get(
            "summary_view"
        )
        or ""
    ).strip().lower()


def _productivity_v60_is_tallies(filters):

    return (
        _productivity_v60_basis(
            filters
        ).startswith(
            "tallies"
        )
    )


def _productivity_v60_columns(
    columns,
):

    columns = list(
        columns
        or []
    )


    # Make sure BCM/HD exists.
    columns = (
        _productivity_v50_restore_bcmhd_column(
            columns
        )
    )


    # Tallies does not require Coal Tons.
    cleaned = []


    for column in columns:

        if not hasattr(
            column,
            "get",
        ):

            cleaned.append(
                column
            )

            continue


        fieldname = str(
            column.get(
                "fieldname"
            )
            or ""
        ).strip()


        if fieldname == "coal_tons":

            continue


        if fieldname == "output":

            column[
                "label"
            ] = "Output Tallies (BCM)"


        elif fieldname == "productivity":

            column[
                "label"
            ] = "Productivity (BCM/HR)"

            column[
                "precision"
            ] = 0


        elif fieldname == "productivity_bcm_hd":

            column[
                "label"
            ] = "Productivity (BCM/HD)"

            column[
                "precision"
            ] = 2


        cleaned.append(
            column
        )


    return cleaned


def _productivity_v60_recalculate_bcm_hr(
    rows,
):

    for row in (
        rows
        or []
    ):

        if not hasattr(
            row,
            "get",
        ):

            continue


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        if label.startswith(
            "MONTHLY PRODUCTION:"
        ):

            continue


        hours = (
            _productivity_v60_float(
                row.get(
                    "working_hours"
                )
            )
        )


        output = (
            _productivity_v60_float(
                row.get(
                    "output"
                )
            )
        )


        if hours > 0:

            row[
                "productivity"
            ] = round(
                output / hours
            )


        elif output > 0:

            row[
                "productivity"
            ] = 0


        row[
            "productivity_tallies_bcm_hr_v60"
        ] = 1


    return rows


def _productivity_v60_hours_labels(
    rows,
):

    current_category = ""

    active_parent = ""
    active_indent = None


    for row in (
        rows
        or []
    ):

        if not hasattr(
            row,
            "get",
        ):

            continue


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        try:

            indent = int(
                float(
                    row.get(
                        "indent"
                    )
                    or 0
                )
            )

        except Exception:

            indent = 0


        if label.startswith(
            "MONTHLY PRODUCTION:"
        ):

            current_category = ""
            active_parent = ""
            active_indent = None

            continue


        if label in (
            "Excavator",
            "ADT",
            "Dozer",
        ):

            current_category = label
            active_parent = ""
            active_indent = None

            continue


        if label == "Total Fleet":

            current_category = ""
            active_parent = ""
            active_indent = None

            continue


        # ====================================================
        # EXCAVATOR / ADT
        #
        # Keep:
        # Coal
        # Hards
        # Softs
        #
        # Hide their child Label wording.
        # ====================================================

        if current_category in (
            "Excavator",
            "ADT",
        ):

            if label in (
                "Coal",
                "Hards",
                "Softs",
            ):

                active_parent = label
                active_indent = indent

                row[
                    "productivity_tallies_heading_v60"
                ] = 1

                continue


            if (
                active_parent
                and active_indent is not None
                and indent > active_indent
            ):

                row[
                    "productivity_hours_hide_label_v58"
                ] = 1

                row[
                    "productivity_hours_hide_label_v59"
                ] = 1

                row[
                    "productivity_tallies_hidden_label_v60"
                ] = 1

                continue


            if (
                active_parent
                and active_indent is not None
                and indent <= active_indent
            ):

                active_parent = ""
                active_indent = None


        # ====================================================
        # DOZER
        #
        # Keep:
        # 2 - Midburden
        # 1 - Overburden
        #
        # Hide:
        # Midburden Dozing
        # ====================================================

        elif current_category == "Dozer":

            if (
                not active_parent
                or active_indent is None
                or indent <= active_indent
            ):

                active_parent = label
                active_indent = indent

                row[
                    "productivity_tallies_heading_v60"
                ] = 1

                continue


            if indent > active_indent:

                row[
                    "productivity_hours_hide_label_v59"
                ] = 1

                row[
                    "productivity_tallies_hidden_label_v60"
                ] = 1

                continue


    return rows


def _productivity_v60_hours_bcm_hd(
    rows,
):

    heading_labels = {
        "coal",
        "hards",
        "softs",
        "1 - overburden",
        "2 - midburden",
    }


    for row in (
        rows
        or []
    ):

        if not hasattr(
            row,
            "get",
        ):

            continue


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        label_lower = (
            label.lower()
        )


        # Parent heading: BCM/HD blank.
        if (
            row.get(
                "productivity_tallies_heading_v60"
            )
            or label_lower in heading_labels
        ):

            row[
                "productivity_bcm_hd"
            ] = ""

            continue


        distance_text = str(
            row.get(
                "hauling_distance_m"
            )
            or ""
        ).strip()


        if not distance_text:

            row[
                "productivity_bcm_hd"
            ] = ""

            continue


        average_distance = (
            _productivity_v57_distance(
                distance_text
            )
        )


        output = (
            _productivity_v60_float(
                row.get(
                    "output"
                )
            )
        )


        if (
            output > 0
            and average_distance > 0
        ):

            row[
                "productivity_bcm_hd"
            ] = round(
                output
                / average_distance,
                2,
            )


        else:

            row[
                "productivity_bcm_hd"
            ] = ""


        row[
            "productivity_tallies_bcm_hd_v60"
        ] = 1


    return rows


def _productivity_v60_summary_rows(
    rows,
    filters,
):

    # If an earlier Summary Tallies patch has already created
    # the final structure, don't duplicate it.
    already_processed = any(
        row.get(
            "productivity_tallies_calc_v60"
        )
        for row in (
            rows
            or []
        )
        if hasattr(
            row,
            "get",
        )
    )


    if not already_processed:

        # ADT / Dozer material hierarchy.
        rows = (
            _productivity_v47_rows(
                rows
            )
        )


        # Area/detail relationship.
        rows = (
            _productivity_v51_rows(
                rows
            )
        )


        # ADT editable detail.
        rows = (
            _productivity_v52_prepare_rows(
                rows,
                filters,
            )
        )


        # Coal/Hards/Softs headings.
        rows = (
            _productivity_v53_rows(
                rows
            )
        )


        # Manual To Area / HD.
        rows = (
            _productivity_v54_apply_rows(
                rows
            )
        )


        # Dozer hierarchy/editing.
        rows = (
            _productivity_v55_prepare_dozer_rows(
                rows,
                filters,
            )
        )


    # All Tallies rows calculate BCM/HR from their own values.
    rows = (
        _productivity_v60_recalculate_bcm_hr(
            rows
        )
    )


    # Dozer calculation markers.
    rows = (
        _productivity_v56_apply_rows(
            rows
        )
    )


    # ADT / Dozer breakdown BCM/HD.
    rows = (
        _productivity_v57_rows(
            rows
        )
    )


    for row in (
        rows
        or []
    ):

        if hasattr(
            row,
            "get",
        ):

            row[
                "productivity_tallies_summary_v60"
            ] = 1


    return rows


def _productivity_v60_hours_rows(
    rows,
):

    # Clean label layout.
    rows = (
        _productivity_v60_hours_labels(
            rows
        )
    )


    # BCM/HR from each Tallies row.
    rows = (
        _productivity_v60_recalculate_bcm_hr(
            rows
        )
    )


    # BCM/HD from user-captured hauling distance.
    rows = (
        _productivity_v60_hours_bcm_hd(
            rows
        )
    )


    for row in (
        rows
        or []
    ):

        if hasattr(
            row,
            "get",
        ):

            row[
                "productivity_tallies_hours_v60"
            ] = 1


    return rows


def _productivity_apply_tallies_both_views_v60(
    result,
    filters,
):

    if not result:

        return result


    if not (
        _productivity_v60_is_tallies(
            filters
        )
    ):

        return result


    view = (
        _productivity_v60_view(
            filters
        )
    )


    if view not in (
        "summary per machine",
        "hours and material",
    ):

        return result


    parts = list(
        result
    )


    if len(
        parts
    ) >= 1:

        parts[
            0
        ] = (
            _productivity_v60_columns(
                parts[
                    0
                ]
            )
        )


    if len(
        parts
    ) >= 2:

        if view == "summary per machine":

            parts[
                1
            ] = (
                _productivity_v60_summary_rows(
                    parts[
                        1
                    ],
                    filters,
                )
            )


        elif view == "hours and material":

            parts[
                1
            ] = (
                _productivity_v60_hours_rows(
                    parts[
                        1
                    ]
                )
            )


    if isinstance(
        result,
        tuple,
    ):

        return tuple(
            parts
        )


    return parts


def execute(filters=None):

    result = (
        _productivity_execute_before_tallies_both_views_v60(
            filters
        )
    )


    return (
        _productivity_apply_tallies_both_views_v60(
            result,
            filters,
        )
    )


# END KOSI_PRODUCTIVITY_TALLIES_BOTH_VIEWS_V60


# ============================================================
# KOSI_PRODUCTIVITY_TALLIES_MATCH_ACTUAL_V61
#
# TARGET:
#
# Tallies BCMs
#     +
# Summary Per Machine
#
#
# GOAL:
#
# Make Tallies Summary Per Machine LOOK and BEHAVE exactly
# like the finished Actual BCMs Summary Per Machine.
#
#
# IMPORTANT:
#
# Actual result is used ONLY as the structural template.
#
# Tallies remains the production source for:
#
# - category totals
# - machine totals
# - broad material totals
#
#
# Where Tallies already contains an exact detail row,
# its Tallies values replace the template values as well.
#
#
# We retain from the finished Actual layout:
#
# - ADT breakdown structure
# - Dozer breakdown structure
# - bold material headings
# - editable route rows
# - From Area display
# - To Area editing
# - Hauling Distance editing
# - saved overrides
# - row markers required by existing JS
#
#
# Productivity:
#
# BCM/HR = Tallies Output / Working Hours
#
#
# BCM/HD = Tallies Output / Average Hauling Distance
#
#
# This patch does NOT affect Actual BCMs.
# This patch does NOT affect Hours and Material.
# ============================================================


import copy


_productivity_execute_before_tallies_match_actual_v61 = execute


def _productivity_v61_is_target(
    filters,
):

    filters = frappe._dict(
        filters
        or {}
    )


    basis = str(
        filters.get(
            "bcm_basis"
        )
        or ""
    ).strip().lower()


    view = str(
        filters.get(
            "summary_view"
        )
        or ""
    ).strip().lower()


    return (
        basis.startswith(
            "tallies"
        )
        and view
        == "summary per machine"
    )


def _productivity_v61_number(
    value,
):

    try:

        return float(
            value
            or 0
        )

    except Exception:

        return 0.0


def _productivity_v61_build_maps(
    rows,
):

    categories = (
        "Excavator",
        "ADT",
        "Dozer",
    )


    category_map = {}
    machine_map = {}
    material_map = {}

    total_fleet = None

    current_category = ""
    current_machine = ""


    for row in (
        rows
        or []
    ):

        if not hasattr(
            row,
            "get",
        ):

            continue


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()


        # ----------------------------------------------------
        # MONTH HEADER
        # ----------------------------------------------------

        if label.startswith(
            "MONTHLY PRODUCTION:"
        ):

            current_category = ""
            current_machine = ""

            continue


        # ----------------------------------------------------
        # CATEGORY
        # ----------------------------------------------------

        if label in categories:

            current_category = label
            current_machine = ""

            category_map[
                current_category
            ] = row

            continue


        # ----------------------------------------------------
        # TOTAL FLEET
        # ----------------------------------------------------

        if label == "Total Fleet":

            total_fleet = row

            current_category = ""
            current_machine = ""

            continue


        if not current_category:

            continue


        # ----------------------------------------------------
        # MACHINE TOTAL
        #
        # ADT01 / IS0601 / IS0338 etc.
        # ----------------------------------------------------

        if (
            label
            and not material
        ):

            current_machine = label


            machine_map[
                (
                    current_category,
                    current_machine,
                )
            ] = row


            continue


        # ----------------------------------------------------
        # MATERIAL / DETAIL
        #
        # If Tallies already has this exact material key,
        # save it for numeric overlay.
        # ----------------------------------------------------

        if (
            current_machine
            and material
        ):

            material_map[
                (
                    current_category,
                    current_machine,
                    material,
                )
            ] = row


    return {
        "categories":
            category_map,

        "machines":
            machine_map,

        "materials":
            material_map,

        "total_fleet":
            total_fleet,
    }


def _productivity_v61_copy_numbers(
    target,
    source,
):

    if not source:

        return target


    # Tallies numeric fields.
    for fieldname in (
        "working_hours",
        "output",
        "adjusted_bcm",
    ):

        if fieldname in source:

            target[
                fieldname
            ] = source.get(
                fieldname
            )


    return target


def _productivity_v61_recalculate(
    row,
):

    hours = (
        _productivity_v61_number(
            row.get(
                "working_hours"
            )
        )
    )


    output = (
        _productivity_v61_number(
            row.get(
                "output"
            )
        )
    )


    # --------------------------------------------------------
    # BCM / HR
    # --------------------------------------------------------

    if hours > 0:

        row[
            "productivity"
        ] = round(
            output / hours
        )


    elif output > 0:

        row[
            "productivity"
        ] = 0


    # --------------------------------------------------------
    # BCM / HD
    #
    # Only detail rows with an actual hauling-distance value.
    # Headings remain blank.
    # --------------------------------------------------------

    is_heading = bool(
        row.get(
            "productivity_material_heading_v53"
        )
        or row.get(
            "productivity_dozer_heading_v55"
        )
    )


    if is_heading:

        row[
            "productivity_bcm_hd"
        ] = ""

        return row


    distance_text = str(
        row.get(
            "hauling_distance_m"
        )
        or ""
    ).strip()


    if not distance_text:

        row[
            "productivity_bcm_hd"
        ] = ""

        return row


    try:

        average_distance = (
            _productivity_v57_distance(
                distance_text
            )
        )

    except Exception:

        average_distance = 0


    if (
        output > 0
        and average_distance > 0
    ):

        row[
            "productivity_bcm_hd"
        ] = round(
            output
            / average_distance,
            2,
        )


    else:

        row[
            "productivity_bcm_hd"
        ] = ""


    return row


def _productivity_v61_merge_rows(
    actual_rows,
    tallies_rows,
):

    # --------------------------------------------------------
    # Tallies lookups.
    # --------------------------------------------------------

    maps = (
        _productivity_v61_build_maps(
            tallies_rows
        )
    )


    result = copy.deepcopy(
        actual_rows
        or []
    )


    categories = (
        "Excavator",
        "ADT",
        "Dozer",
    )


    current_category = ""
    current_machine = ""


    for row in result:

        if not hasattr(
            row,
            "get",
        ):

            continue


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()


        # ----------------------------------------------------
        # MONTH HEADER
        # ----------------------------------------------------

        if label.startswith(
            "MONTHLY PRODUCTION:"
        ):

            current_category = ""
            current_machine = ""

            continue


        # ----------------------------------------------------
        # CATEGORY
        # ----------------------------------------------------

        if label in categories:

            current_category = label
            current_machine = ""


            _productivity_v61_copy_numbers(
                row,
                maps[
                    "categories"
                ].get(
                    current_category
                ),
            )


            _productivity_v61_recalculate(
                row
            )


            row[
                "productivity_tallies_mirror_v61"
            ] = 1


            continue


        # ----------------------------------------------------
        # TOTAL FLEET
        # ----------------------------------------------------

        if label == "Total Fleet":

            _productivity_v61_copy_numbers(
                row,
                maps.get(
                    "total_fleet"
                ),
            )


            _productivity_v61_recalculate(
                row
            )


            row[
                "productivity_tallies_mirror_v61"
            ] = 1


            current_category = ""
            current_machine = ""

            continue


        if not current_category:

            continue


        # ----------------------------------------------------
        # MACHINE TOTAL
        # ----------------------------------------------------

        if (
            label
            and not material
        ):

            current_machine = label


            source = (
                maps[
                    "machines"
                ].get(
                    (
                        current_category,
                        current_machine,
                    )
                )
            )


            _productivity_v61_copy_numbers(
                row,
                source,
            )


            _productivity_v61_recalculate(
                row
            )


            row[
                "productivity_tallies_mirror_v61"
            ] = 1


            continue


        # ----------------------------------------------------
        # MATERIAL OR BREAKDOWN
        # ----------------------------------------------------

        if (
            current_machine
            and material
        ):

            source = (
                maps[
                    "materials"
                ].get(
                    (
                        current_category,
                        current_machine,
                        material,
                    )
                )
            )


            # If Tallies contains the exact material/detail row,
            # use its numeric values.
            #
            # If not, retain the existing captured detail values
            # from the finished report structure.

            if source:

                _productivity_v61_copy_numbers(
                    row,
                    source,
                )


            _productivity_v61_recalculate(
                row
            )


            row[
                "productivity_tallies_mirror_v61"
            ] = 1


    return result


def _productivity_v61_columns(
    actual_columns,
):

    columns = copy.deepcopy(
        actual_columns
        or []
    )


    for column in columns:

        if not hasattr(
            column,
            "get",
        ):

            continue


        fieldname = str(
            column.get(
                "fieldname"
            )
            or ""
        ).strip()


        if fieldname == "output":

            column[
                "label"
            ] = "Output Tallies (BCM)"


        elif fieldname == "productivity":

            column[
                "label"
            ] = "Productivity (BCM/HR)"

            column[
                "precision"
            ] = 0


        elif fieldname == "productivity_bcm_hd":

            column[
                "label"
            ] = "Productivity (BCM/HD)"

            column[
                "precision"
            ] = 2


    return columns


def _productivity_apply_tallies_match_actual_v61(
    tallies_result,
    filters,
):

    if not tallies_result:

        return tallies_result


    if not (
        _productivity_v61_is_target(
            filters
        )
    ):

        return tallies_result


    # --------------------------------------------------------
    # Run the SAME report with Actual BCMs.
    #
    # We use this only for its already-finished row structure,
    # markers and edit behaviour.
    # --------------------------------------------------------

    actual_filters = dict(
        filters
        or {}
    )


    actual_filters[
        "bcm_basis"
    ] = "Actual BCMs"


    actual_result = (
        _productivity_execute_before_tallies_match_actual_v61(
            actual_filters
        )
    )


    if (
        not actual_result
        or len(
            actual_result
        ) < 2
    ):

        return tallies_result


    tallies_parts = list(
        tallies_result
    )


    actual_parts = list(
        actual_result
    )


    # --------------------------------------------------------
    # EXACT SAME COLUMNS/LAYOUT AS ACTUAL,
    # but rename Output to Tallies.
    # --------------------------------------------------------

    tallies_parts[
        0
    ] = (
        _productivity_v61_columns(
            actual_parts[
                0
            ]
        )
    )


    # --------------------------------------------------------
    # EXACT SAME ROW STRUCTURE AS ACTUAL,
    # but overlay Tallies values.
    # --------------------------------------------------------

    tallies_parts[
        1
    ] = (
        _productivity_v61_merge_rows(
            actual_parts[
                1
            ],
            tallies_result[
                1
            ],
        )
    )


    if isinstance(
        tallies_result,
        tuple,
    ):

        return tuple(
            tallies_parts
        )


    return tallies_parts


def execute(filters=None):

    result = (
        _productivity_execute_before_tallies_match_actual_v61(
            filters
        )
    )


    return (
        _productivity_apply_tallies_match_actual_v61(
            result,
            filters,
        )
    )


# END KOSI_PRODUCTIVITY_TALLIES_MATCH_ACTUAL_V61


# ============================================================
# KOSI_PRODUCTIVITY_TALLIES_HOURS_MATCH_ACTUAL_V62
#
# TALLIES BCMs / HOURS AND MATERIAL
#
# GOAL:
#
# Tallies Hours and Material must have the SAME structure as
# the finished Actual BCMs Hours and Material report.
#
#
# Example:
#
# Excavator
#
#     Softs
#         detailed row
#
#     Coal
#         Ramp 2 - 3 2#Coal
#         Ramp 1 - 2 2#Coal
#
#     Hards
#         Ramp 1 Overburden
#         Ramp 3 Overburden
#
#
# ADT
#
#     Coal
#         Ramp 2 - 3 2#Coal
#         Ramp 1 - 2 2#Coal
#
#     Hards
#         Ramp 1 Overburden
#         Ramp 3 Overburden
#
#     Softs
#         Topsoil Dump
#
#
# Dozer
#
#     2 - Midburden
#         Midburden Dozing
#
#     1 - Overburden
#         Midburden Dozing
#
#
# IMPORTANT:
#
# The structure/route fields come from the finished Actual
# report layout.
#
# Production values remain TALLIES BCM.
#
#
# Child Tallies output:
#
# 1. Exact Tallies detail value is used when available.
#
# 2. If Tallies only has the broad parent total, split the
#    Tallies BCM using the Actual detail-output proportions.
#
# This guarantees:
#
#     sum(detail Tallies BCM)
#         =
#     parent Tallies BCM
#
#
# BCM/HR:
#
#     Tallies BCM / row Working Hours
#
#
# BCM/HD:
#
#     Tallies BCM / Average Hauling Distance
#
#
# Only targets:
#
#     Tallies BCMs
#     Hours and Material
#
# Summary Per Machine untouched.
# Actual BCMs untouched.
# ============================================================


import copy
import re


_productivity_execute_before_tallies_hours_match_actual_v62 = execute


def _productivity_v62_number(value):

    try:

        return float(
            value
            or 0
        )

    except Exception:

        return 0.0


def _productivity_v62_indent(row):

    try:

        return int(
            float(
                row.get(
                    "indent"
                )
                or 0
            )
        )

    except Exception:

        return 0


def _productivity_v62_is_target(filters):

    filters = frappe._dict(
        filters
        or {}
    )

    basis = str(
        filters.get(
            "bcm_basis"
        )
        or ""
    ).strip().lower()

    view = str(
        filters.get(
            "summary_view"
        )
        or ""
    ).strip().lower()

    return (
        basis.startswith(
            "tallies"
        )
        and view
        == "hours and material"
    )


def _productivity_v62_distance(value):

    text = str(
        value
        or ""
    ).strip()

    if not text:

        return 0.0


    text = (
        text
        .replace(
            "–",
            "-",
        )
        .replace(
            "—",
            "-",
        )
        .replace(
            ",",
            "",
        )
    )


    numbers = re.findall(
        r"\d+(?:\.\d+)?",
        text,
    )


    if not numbers:

        return 0.0


    values = [
        float(
            value
        )
        for value in numbers
    ]


    if len(values) >= 2:

        start = values[0]
        end = values[1]

        if start <= 0 or end <= 0:

            return 0.0

        return (
            start + end
        ) / 2.0


    return (
        values[0]
        if values[0] > 0
        else 0.0
    )


def _productivity_v62_parent_label(
    category,
    label,
):

    label = str(
        label
        or ""
    ).strip()

    lower = label.lower()


    if lower in (
        "coal",
        "hards",
        "softs",
    ):

        return True


    if category == "Dozer":

        if (
            label
            and "dozing"
            not in lower
            and (
                "midburden"
                in lower
                or "overburden"
                in lower
            )
        ):

            return True


    return False


def _productivity_v62_tallies_maps(rows):

    categories = (
        "Excavator",
        "ADT",
        "Dozer",
    )


    category_map = {}
    parent_map = {}
    detail_map = {}

    parent_labels = {
        "Excavator":
            set(),

        "ADT":
            set(),

        "Dozer":
            set(),
    }


    total_fleet = None

    current_category = ""
    current_parent = ""
    current_parent_indent = None


    for row in (
        rows
        or []
    ):

        if not hasattr(
            row,
            "get",
        ):

            continue


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()


        indent = (
            _productivity_v62_indent(
                row
            )
        )


        if label.startswith(
            "MONTHLY PRODUCTION:"
        ):

            current_category = ""
            current_parent = ""
            current_parent_indent = None

            continue


        if label in categories:

            current_category = label

            category_map[
                label
            ] = row

            current_parent = ""
            current_parent_indent = None

            continue


        if label == "Total Fleet":

            total_fleet = row

            current_category = ""
            current_parent = ""
            current_parent_indent = None

            continue


        if not current_category:

            continue


        # ====================================================
        # BROAD MATERIAL PARENT
        # ====================================================

        if (
            _productivity_v62_parent_label(
                current_category,
                label,
            )
        ):

            current_parent = label
            current_parent_indent = indent


            parent_map[
                (
                    current_category,
                    current_parent,
                )
            ] = row


            parent_labels[
                current_category
            ].add(
                current_parent
            )


            continue


        # ====================================================
        # EXACT DETAIL IF TALLIES ALREADY HAS ONE
        # ====================================================

        if current_parent:

            detail_identity = (
                material
                or label
            )


            if detail_identity:

                detail_map[
                    (
                        current_category,
                        current_parent,
                        detail_identity,
                    )
                ] = row


                if label:

                    detail_map[
                        (
                            current_category,
                            current_parent,
                            label,
                        )
                    ] = row


                if material:

                    detail_map[
                        (
                            current_category,
                            current_parent,
                            material,
                        )
                    ] = row


    return {
        "categories":
            category_map,

        "parents":
            parent_map,

        "details":
            detail_map,

        "parent_labels":
            parent_labels,

        "total_fleet":
            total_fleet,
    }


def _productivity_v62_copy_numbers(
    target,
    source,
):

    if not source:

        return


    if "working_hours" in source:

        target[
            "working_hours"
        ] = source.get(
            "working_hours"
        )


    if "output" in source:

        target[
            "output"
        ] = source.get(
            "output"
        )


    if "adjusted_bcm" in source:

        target[
            "adjusted_bcm"
        ] = source.get(
            "adjusted_bcm"
        )


def _productivity_v62_calc_row(
    row,
    heading=False,
):

    hours = (
        _productivity_v62_number(
            row.get(
                "working_hours"
            )
        )
    )


    output = (
        _productivity_v62_number(
            row.get(
                "output"
            )
        )
    )


    # ========================================================
    # BCM / HR
    # ========================================================

    if hours > 0:

        row[
            "productivity"
        ] = round(
            output / hours
        )


    elif output > 0:

        row[
            "productivity"
        ] = 0


    # ========================================================
    # BCM / HD
    # ========================================================

    if heading:

        row[
            "productivity_bcm_hd"
        ] = ""

        return


    distance = (
        _productivity_v62_distance(
            row.get(
                "hauling_distance_m"
            )
        )
    )


    if (
        output > 0
        and distance > 0
    ):

        row[
            "productivity_bcm_hd"
        ] = round(
            output / distance,
            2,
        )


    else:

        row[
            "productivity_bcm_hd"
        ] = ""


def _productivity_v62_allocation_precision(
    value,
):

    number = (
        _productivity_v62_number(
            value
        )
    )


    if abs(
        number
        - round(
            number
        )
    ) < 0.000001:

        return 0


    return 3


def _productivity_v62_allocate_group(
    rows,
    category,
    parent_label,
    parent_index,
    detail_indices,
    tallies_maps,
):

    source_parent = (
        tallies_maps[
            "parents"
        ].get(
            (
                category,
                parent_label,
            )
        )
    )


    # No Tallies parent = never show Actual BCM as Tallies.
    if not source_parent:

        rows[
            parent_index
        ][
            "output"
        ] = 0


        _productivity_v62_calc_row(
            rows[
                parent_index
            ],
            heading=True,
        )


        for index in detail_indices:

            rows[
                index
            ][
                "output"
            ] = 0


            _productivity_v62_calc_row(
                rows[
                    index
                ],
                heading=False,
            )


        return


    # ========================================================
    # PARENT TALLIES VALUES
    # ========================================================

    _productivity_v62_copy_numbers(
        rows[
            parent_index
        ],
        source_parent,
    )


    parent_total = (
        _productivity_v62_number(
            rows[
                parent_index
            ].get(
                "output"
            )
        )
    )


    _productivity_v62_calc_row(
        rows[
            parent_index
        ],
        heading=True,
    )


    if not detail_indices:

        return


    # ========================================================
    # FIRST PASS:
    #
    # Use exact Tallies child rows when they exist.
    # ========================================================

    exact_total = 0.0
    unmatched = []


    for index in detail_indices:

        row = rows[
            index
        ]


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()


        identities = []


        if material:

            identities.append(
                material
            )


        if (
            label
            and label not in identities
        ):

            identities.append(
                label
            )


        source = None


        for identity in identities:

            source = (
                tallies_maps[
                    "details"
                ].get(
                    (
                        category,
                        parent_label,
                        identity,
                    )
                )
            )


            if source:

                break


        if source:

            _productivity_v62_copy_numbers(
                row,
                source,
            )


            exact_total += (
                _productivity_v62_number(
                    row.get(
                        "output"
                    )
                )
            )


            row[
                "productivity_tallies_exact_detail_v62"
            ] = 1


        else:

            unmatched.append(
                index
            )


    # ========================================================
    # SECOND PASS:
    #
    # If Tallies has only a parent total, allocate the
    # remaining Tallies BCM across Actual detail rows using
    # the Actual detail-output proportions.
    # ========================================================

    remaining_total = (
        parent_total
        - exact_total
    )


    if remaining_total < 0:

        remaining_total = 0


    if unmatched:

        weights = []


        for index in unmatched:

            # At this point output still contains the Actual
            # template value. Use it only as the allocation
            # proportion.

            weight = (
                _productivity_v62_number(
                    rows[
                        index
                    ].get(
                        "output"
                    )
                )
            )


            weights.append(
                weight
            )


        weight_total = sum(
            weights
        )


        # Fallback to Working Hours when Actual detail output
        # is zero.
        if weight_total <= 0:

            weights = [
                _productivity_v62_number(
                    rows[
                        index
                    ].get(
                        "working_hours"
                    )
                )

                for index in unmatched
            ]


            weight_total = sum(
                weights
            )


        if weight_total <= 0:

            weights = [
                1.0
                for _index in unmatched
            ]


            weight_total = float(
                len(
                    unmatched
                )
            )


        precision = (
            _productivity_v62_allocation_precision(
                parent_total
            )
        )


        allocated_so_far = 0.0


        for position, index in enumerate(
            unmatched
        ):

            if (
                position
                == len(
                    unmatched
                )
                - 1
            ):

                allocated = (
                    remaining_total
                    - allocated_so_far
                )


            else:

                allocated = round(
                    remaining_total
                    * (
                        weights[
                            position
                        ]
                        / weight_total
                    ),
                    precision,
                )


                allocated_so_far += (
                    allocated
                )


            if precision == 0:

                allocated = int(
                    round(
                        allocated
                    )
                )


            else:

                allocated = round(
                    allocated,
                    precision,
                )


            rows[
                index
            ][
                "output"
            ] = allocated


            if (
                "adjusted_bcm"
                in rows[
                    index
                ]
            ):

                rows[
                    index
                ][
                    "adjusted_bcm"
                ] = allocated


            rows[
                index
            ][
                "productivity_tallies_allocated_detail_v62"
            ] = 1


    # ========================================================
    # FINAL CALCULATION FOR ALL DETAIL ROWS
    # ========================================================

    for index in detail_indices:

        _productivity_v62_calc_row(
            rows[
                index
            ],
            heading=False,
        )


def _productivity_v62_merge(
    actual_rows,
    tallies_rows,
):

    rows = copy.deepcopy(
        actual_rows
        or []
    )


    maps = (
        _productivity_v62_tallies_maps(
            tallies_rows
        )
    )


    categories = (
        "Excavator",
        "ADT",
        "Dozer",
    )


    current_category = ""

    active_parent = ""
    active_parent_index = None
    active_details = []


    def flush():

        nonlocal active_parent
        nonlocal active_parent_index
        nonlocal active_details


        if (
            current_category
            and active_parent
            and active_parent_index is not None
        ):

            _productivity_v62_allocate_group(
                rows,
                current_category,
                active_parent,
                active_parent_index,
                active_details,
                maps,
            )


        active_parent = ""
        active_parent_index = None
        active_details = []


    for index, row in enumerate(
        rows
    ):

        if not hasattr(
            row,
            "get",
        ):

            continue


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        # ====================================================
        # MONTH HEADER
        # ====================================================

        if label.startswith(
            "MONTHLY PRODUCTION:"
        ):

            flush()

            current_category = ""

            continue


        # ====================================================
        # CATEGORY
        # ====================================================

        if label in categories:

            flush()

            current_category = label


            source = (
                maps[
                    "categories"
                ].get(
                    label
                )
            )


            _productivity_v62_copy_numbers(
                row,
                source,
            )


            _productivity_v62_calc_row(
                row,
                heading=True,
            )


            row[
                "productivity_tallies_hours_v62"
            ] = 1


            continue


        # ====================================================
        # TOTAL FLEET
        # ====================================================

        if label == "Total Fleet":

            flush()


            _productivity_v62_copy_numbers(
                row,
                maps.get(
                    "total_fleet"
                ),
            )


            _productivity_v62_calc_row(
                row,
                heading=True,
            )


            row[
                "productivity_tallies_hours_v62"
            ] = 1


            current_category = ""

            continue


        if not current_category:

            continue


        # ====================================================
        # BROAD MATERIAL PARENT
        # ====================================================

        if (
            _productivity_v62_parent_label(
                current_category,
                label,
            )
        ):

            flush()


            active_parent = label
            active_parent_index = index
            active_details = []


            row[
                "productivity_tallies_hours_parent_v62"
            ] = 1


            row[
                "productivity_tallies_hours_v62"
            ] = 1


            continue


        # ====================================================
        # DETAIL UNDER CURRENT MATERIAL
        # ====================================================

        if active_parent:

            active_details.append(
                index
            )


            row[
                "productivity_tallies_hours_detail_v62"
            ] = 1


            row[
                "productivity_tallies_hours_v62"
            ] = 1


    flush()


    return rows


def _productivity_v62_columns(
    actual_columns,
):

    columns = copy.deepcopy(
        actual_columns
        or []
    )


    for column in columns:

        if not hasattr(
            column,
            "get",
        ):

            continue


        fieldname = str(
            column.get(
                "fieldname"
            )
            or ""
        ).strip()


        if fieldname == "output":

            column[
                "label"
            ] = "Output Tallies (BCM)"


        elif fieldname == "productivity":

            column[
                "label"
            ] = "Productivity (BCM/HR)"

            column[
                "precision"
            ] = 0


        elif fieldname == "productivity_bcm_hd":

            column[
                "label"
            ] = "Productivity (BCM/HD)"

            column[
                "precision"
            ] = 2


    return columns


def _productivity_apply_tallies_hours_match_actual_v62(
    tallies_result,
    filters,
):

    if not tallies_result:

        return tallies_result


    if not (
        _productivity_v62_is_target(
            filters
        )
    ):

        return tallies_result


    # ========================================================
    # FINISHED ACTUAL HOURS AND MATERIAL
    #
    # Used ONLY as the structure/template.
    # ========================================================

    actual_filters = dict(
        filters
        or {}
    )


    actual_filters[
        "bcm_basis"
    ] = "Actual BCMs"


    actual_filters[
        "summary_view"
    ] = "Hours and Material"


    actual_result = (
        _productivity_execute_before_tallies_hours_match_actual_v62(
            actual_filters
        )
    )


    if (
        not actual_result
        or len(
            actual_result
        ) < 2
    ):

        return tallies_result


    result = list(
        tallies_result
    )


    result[
        0
    ] = (
        _productivity_v62_columns(
            actual_result[
                0
            ]
        )
    )


    result[
        1
    ] = (
        _productivity_v62_merge(
            actual_result[
                1
            ],
            tallies_result[
                1
            ],
        )
    )


    if isinstance(
        tallies_result,
        tuple,
    ):

        return tuple(
            result
        )


    return result


def execute(filters=None):

    result = (
        _productivity_execute_before_tallies_hours_match_actual_v62(
            filters
        )
    )


    return (
        _productivity_apply_tallies_hours_match_actual_v62(
            result,
            filters,
        )
    )


# END KOSI_PRODUCTIVITY_TALLIES_HOURS_MATCH_ACTUAL_V62


# ============================================================
# KOSI_PRODUCTIVITY_FINAL_COAL_TONNES_V63
#
# FINAL RULE:
#
# Actual BCMs / Hours and Material
#
# COAL OUTPUT:
#     Survey Metric Tonnes
#
# COAL PRODUCTIVITY:
#     Tonnes / Working Hours
#
# OTHER MATERIAL:
#     BCM
#
# CATEGORY / FLEET TOTALS:
#     remain BCM
#
# BCM/HD:
#     remains BCM based, NOT tonnes based.
#
# Tallies BCMs:
#     untouched.
#
# If no qualifying Survey exists:
#     leave current report values unchanged.
# ============================================================


_productivity_execute_before_final_coal_tonnes_v63 = execute


def _productivity_v63_float(value):

    try:

        return float(
            value
            or 0
        )

    except Exception:

        return 0.0


def _productivity_v63_indent(row):

    try:

        return int(
            float(
                row.get(
                    "indent"
                )
                or 0
            )
        )

    except Exception:

        return 0


def _productivity_v63_is_target(filters):

    filters = frappe._dict(
        filters
        or {}
    )

    basis = str(
        filters.get(
            "bcm_basis"
        )
        or ""
    ).strip().lower()

    view = str(
        filters.get(
            "summary_view"
        )
        or ""
    ).strip().lower()

    return (
        basis.startswith(
            "actual"
        )
        and view
        == "hours and material"
    )


def _productivity_v63_plans(filters):

    import json

    filters = frappe._dict(
        filters
        or {}
    )

    raw = (
        filters.get(
            "monthly_production_plans"
        )
        or filters.get(
            "monthly_production_plan"
        )
        or []
    )

    if isinstance(
        raw,
        (
            list,
            tuple,
            set,
        ),
    ):

        values = list(
            raw
        )

    elif isinstance(
        raw,
        str,
    ):

        text = raw.strip()

        if not text:

            values = []

        elif text.startswith(
            "["
        ):

            try:

                parsed = json.loads(
                    text
                )

                values = (
                    parsed
                    if isinstance(
                        parsed,
                        list,
                    )
                    else [
                        parsed
                    ]
                )

            except Exception:

                values = [
                    text
                ]

        else:

            values = [
                text
            ]

    else:

        values = [
            raw
        ] if raw else []

    return [
        str(
            value
            or ""
        ).strip()

        for value in values

        if str(
            value
            or ""
        ).strip()
    ]


def _productivity_v63_survey_coal(
    site,
    monthly_plan,
    end_date,
):

    if not (
        site
        and monthly_plan
    ):

        return None


    survey_filters = {
        "docstatus":
            1,

        "location":
            site,

        "monthly_production_plan_ref":
            monthly_plan,
    }


    if end_date:

        survey_filters[
            "last_production_shift_start_date"
        ] = [
            "<=",
            end_date,
        ]


    surveys = frappe.get_all(
        "Survey",
        filters=
            survey_filters,
        fields=[
            "name",
            "last_production_shift_start_date",
            "survey_datetime",
        ],
        order_by=(
            "last_production_shift_start_date desc, "
            "survey_datetime desc, "
            "modified desc"
        ),
        limit_page_length=1,
    )


    if not surveys:

        return None


    survey_name = (
        surveys[
            0
        ].name
    )


    doc = frappe.get_doc(
        "Survey",
        survey_name,
    )


    result = {
        "survey":
            survey_name,

        "Excavator": {
            "tonnes": 0.0,
            "bcm": 0.0,
        },

        "ADT": {
            "tonnes": 0.0,
            "bcm": 0.0,
        },

        "Dozer": {
            "tonnes": 0.0,
            "bcm": 0.0,
        },
    }


    for item in (
        doc.get(
            "surveyed_values"
        )
        or []
    ):

        material = str(
            item.get(
                "mat_type"
            )
            or ""
        ).strip().lower()


        if material != "coal":

            continue


        handling = "".join(
            ch.lower()
            for ch in str(
                item.get(
                    "handling_method"
                )
                or ""
            )
            if ch.isalpha()
        )


        tonnes = (
            _productivity_v63_float(
                item.get(
                    "metric_tonnes"
                )
            )
        )


        bcm = (
            _productivity_v63_float(
                item.get(
                    "bcm"
                )
            )
        )


        if handling == "truckandshovel":

            for category in (
                "Excavator",
                "ADT",
            ):

                result[
                    category
                ][
                    "tonnes"
                ] += tonnes

                result[
                    category
                ][
                    "bcm"
                ] += bcm


        elif handling == "dozing":

            result[
                "Dozer"
            ][
                "tonnes"
            ] += tonnes

            result[
                "Dozer"
            ][
                "bcm"
            ] += bcm


    return result


def _productivity_v63_average_distance(value):

    import re

    text = str(
        value
        or ""
    ).strip()

    if not text:

        return 0.0


    text = (
        text
        .replace(
            "–",
            "-",
        )
        .replace(
            "—",
            "-",
        )
        .replace(
            ",",
            "",
        )
    )


    numbers = re.findall(
        r"\d+(?:\.\d+)?",
        text,
    )


    if not numbers:

        return 0.0


    values = [
        float(
            number
        )

        for number in numbers
    ]


    if len(
        values
    ) >= 2:

        return (
            values[
                0
            ]
            + values[
                1
            ]
        ) / 2.0


    return values[
        0
    ]


def _productivity_v63_apply_group(
    rows,
    parent_index,
    child_indices,
    tonnes_total,
    bcm_total,
    survey_name,
):

    parent = rows[
        parent_index
    ]


    # --------------------------------------------------------
    # Preserve existing BCM allocations BEFORE replacing
    # visible Coal Output with tonnes.
    # --------------------------------------------------------

    child_bcm_values = [
        _productivity_v63_float(
            rows[
                index
            ].get(
                "output"
            )
        )

        for index in child_indices
    ]


    child_bcm_total = sum(
        child_bcm_values
    )


    parent_hours = (
        _productivity_v63_float(
            parent.get(
                "working_hours"
            )
        )
    )


    parent[
        "productivity_coal_bcm_v63"
    ] = bcm_total


    parent[
        "output"
    ] = tonnes_total


    parent[
        "productivity"
    ] = (
        round(
            tonnes_total
            / parent_hours
        )
        if parent_hours > 0
        else 0
    )


    parent[
        "productivity_bcm_hd"
    ] = ""


    parent[
        "productivity_output_unit_v19"
    ] = "Metric Tonnes"


    parent[
        "productivity_coal_tonnes_v63"
    ] = 1


    parent[
        "productivity_coal_survey_v63"
    ] = survey_name


    if (
        "adjusted_bcm"
        in parent
    ):

        parent[
            "adjusted_bcm"
        ] = tonnes_total


    # --------------------------------------------------------
    # Child Coal rows:
    #
    # Allocate Survey Tonnes according to existing BCM detail
    # proportions.
    #
    # Their BCM/HD calculation continues using BCM.
    # --------------------------------------------------------

    if (
        child_indices
        and child_bcm_total > 0
    ):

        allocated = 0.0


        for position, index in enumerate(
            child_indices
        ):

            row = rows[
                index
            ]


            child_bcm = (
                child_bcm_values[
                    position
                ]
            )


            if (
                position
                == len(
                    child_indices
                )
                - 1
            ):

                child_tonnes = (
                    tonnes_total
                    - allocated
                )

            else:

                child_tonnes = (
                    tonnes_total
                    * (
                        child_bcm
                        / child_bcm_total
                    )
                )

                allocated += (
                    child_tonnes
                )


            row_hours = (
                _productivity_v63_float(
                    row.get(
                        "working_hours"
                    )
                )
            )


            row[
                "productivity_coal_bcm_v63"
            ] = child_bcm


            row[
                "output"
            ] = child_tonnes


            row[
                "productivity"
            ] = (
                round(
                    child_tonnes
                    / row_hours
                )
                if row_hours > 0
                else 0
            )


            distance = (
                _productivity_v63_average_distance(
                    row.get(
                        "hauling_distance_m"
                    )
                )
            )


            # BCM/HD remains BCM based.
            row[
                "productivity_bcm_hd"
            ] = (
                round(
                    child_bcm
                    / distance,
                    2,
                )
                if (
                    child_bcm > 0
                    and distance > 0
                )
                else ""
            )


            row[
                "productivity_output_unit_v19"
            ] = "Metric Tonnes"


            row[
                "productivity_coal_tonnes_v63"
            ] = 1


            row[
                "productivity_coal_survey_v63"
            ] = survey_name


            if (
                "adjusted_bcm"
                in row
            ):

                row[
                    "adjusted_bcm"
                ] = child_tonnes


def _productivity_v63_rows(
    rows,
    filters,
):

    filters = frappe._dict(
        filters
        or {}
    )


    rows = list(
        rows
        or []
    )


    site = str(
        filters.get(
            "site"
        )
        or filters.get(
            "location"
        )
        or ""
    ).strip()


    end_date = (
        filters.get(
            "end_date"
        )
        or filters.get(
            "to_date"
        )
    )


    plans = (
        _productivity_v63_plans(
            filters
        )
    )


    plan_index = -1

    current_plan = (
        plans[
            0
        ]
        if len(
            plans
        ) == 1
        else ""
    )


    current_category = ""

    survey_cache = {}


    for index, row in enumerate(
        rows
    ):

        if not hasattr(
            row,
            "get",
        ):

            continue


        label = str(
            row.get(
                "label"
            )
            or ""
        ).strip()


        material = str(
            row.get(
                "material"
            )
            or ""
        ).strip()


        # ----------------------------------------------------
        # MONTH SECTION
        # ----------------------------------------------------

        if label.startswith(
            "MONTHLY PRODUCTION:"
        ):

            current_category = ""

            if len(
                plans
            ) > 1:

                plan_index += 1

                current_plan = (
                    plans[
                        plan_index
                    ]
                    if plan_index < len(
                        plans
                    )
                    else ""
                )

            continue


        # ----------------------------------------------------
        # CATEGORY
        # ----------------------------------------------------

        if label in (
            "Excavator",
            "ADT",
            "Dozer",
        ):

            current_category = label

            continue


        if label == "Total Fleet":

            current_category = ""

            continue


        # ----------------------------------------------------
        # COAL PARENT
        # ----------------------------------------------------

        is_coal_parent = (
            label.lower()
            == "coal"
            or (
                material.lower()
                == "coal"
                and bool(
                    row.get(
                        "productivity_material_heading_v53"
                    )
                )
            )
        )


        if not (
            current_category
            and is_coal_parent
            and current_plan
        ):

            continue


        cache_key = (
            site,
            current_plan,
            str(
                end_date
                or ""
            ),
        )


        if cache_key not in survey_cache:

            survey_cache[
                cache_key
            ] = (
                _productivity_v63_survey_coal(
                    site,
                    current_plan,
                    end_date,
                )
            )


        survey = (
            survey_cache[
                cache_key
            ]
        )


        if not survey:

            continue


        category_data = (
            survey.get(
                current_category
            )
            or {}
        )


        tonnes_total = (
            _productivity_v63_float(
                category_data.get(
                    "tonnes"
                )
            )
        )


        bcm_total = (
            _productivity_v63_float(
                category_data.get(
                    "bcm"
                )
            )
        )


        if tonnes_total <= 0:

            continue


        parent_indent = (
            _productivity_v63_indent(
                row
            )
        )


        child_indices = []


        child_index = (
            index + 1
        )


        while child_index < len(
            rows
        ):

            child = rows[
                child_index
            ]


            if not hasattr(
                child,
                "get",
            ):

                break


            child_indent = (
                _productivity_v63_indent(
                    child
                )
            )


            if child_indent <= parent_indent:

                break


            child_indices.append(
                child_index
            )


            child_index += 1


        _productivity_v63_apply_group(
            rows,
            index,
            child_indices,
            tonnes_total,
            bcm_total,
            survey.get(
                "survey"
            ),
        )


    return rows


def _productivity_v63_columns(columns):

    for column in (
        columns
        or []
    ):

        if not hasattr(
            column,
            "get",
        ):

            continue


        fieldname = str(
            column.get(
                "fieldname"
            )
            or ""
        ).strip()


        if fieldname == "output":

            column[
                "label"
            ] = (
                "Output Actual "
                "(BCM / Coal Tonnes)"
            )


        elif fieldname == "productivity":

            column[
                "label"
            ] = (
                "Productivity "
                "(BCM/HR / Coal t/HR)"
            )


    return columns


def _productivity_apply_final_coal_tonnes_v63(
    result,
    filters,
):

    if not result:

        return result


    if not (
        _productivity_v63_is_target(
            filters
        )
    ):

        return result


    parts = list(
        result
    )


    if len(
        parts
    ) >= 1:

        parts[
            0
        ] = (
            _productivity_v63_columns(
                parts[
                    0
                ]
            )
        )


    if len(
        parts
    ) >= 2:

        parts[
            1
        ] = (
            _productivity_v63_rows(
                parts[
                    1
                ],
                filters,
            )
        )


    if isinstance(
        result,
        tuple,
    ):

        return tuple(
            parts
        )


    return parts


def execute(filters=None):

    result = (
        _productivity_execute_before_final_coal_tonnes_v63(
            filters
        )
    )


    return (
        _productivity_apply_final_coal_tonnes_v63(
            result,
            filters,
        )
    )


# END KOSI_PRODUCTIVITY_FINAL_COAL_TONNES_V63
