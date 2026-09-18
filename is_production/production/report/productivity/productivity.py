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
