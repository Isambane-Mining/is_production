# daily_lost_hours_recon.py

import frappe
import datetime
from frappe.model.document import Document


class DailyLostHoursRecon(Document):
    def validate(self):
        # Sync new General Lost Hours table before old logic
        sync_general_lost_hours_table(self)
        self.set_day_of_week_if_missing()
        self.calculate_parent_total_general_lost_hours()
        self.copy_parent_general_hours_to_child_rows()
        self.validate_child_rows_hours()
        # Restore full General Lost Hours totals after legacy logic
        sync_general_lost_hours_table(self)

    def set_day_of_week_if_missing(self):
        if not self.day_of_week and self.shift_date:
            dt = datetime.datetime.strptime(self.shift_date, "%Y-%m-%d").date()
            self.day_of_week = dt.strftime("%A")

    def calculate_parent_total_general_lost_hours(self):
        """
        Includes blasting in calculation.
        """
        self.total_general_lost_hours = (
            (self.gen_training_hours or 0)
            + (self.weather_non_work_hours or 0)
            + (self.vfl_non_work_hours or 0)
            + (self.other_non_work_hours or 0)
            + (self.diesel_or_diesel_bowser_hours or 0)
            + (self.dust_water_bowser_issues_hours or 0)
            + (self.blasting or 0)
        )

    def copy_parent_general_hours_to_child_rows(self):
        for row in self.daily_lost_hours_assets_table:
            row.gen_training_hours_child = self.gen_training_hours or 0
            row.weather_non_work_hours_child = self.weather_non_work_hours or 0
            row.vfl_non_work_hours_child = self.vfl_non_work_hours or 0
            row.other_non_work_hours_child = self.other_non_work_hours or 0
            row.diesel_or_diesel_bowser_hours_child = self.diesel_or_diesel_bowser_hours or 0
            row.dust_water_bowser_issues_hours_child = self.dust_water_bowser_issues_hours or 0
            row.blasting_child = self.blasting or 0

            row.total_general_lost_hours_child = (
                (row.gen_training_hours_child or 0)
                + (row.weather_non_work_hours_child or 0)
                + (row.vfl_non_work_hours_child or 0)
                + (row.other_non_work_hours_child or 0)
                + (row.diesel_or_diesel_bowser_hours_child or 0)
                + (row.dust_water_bowser_issues_hours_child or 0)
                + (row.blasting_child or 0)
            )

    def validate_child_rows_hours(self):
        if self.day_of_week in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]:
            required_hours = self.weekday_required_hours or 0
        elif self.day_of_week == "Saturday":
            required_hours = self.sat_required_shift_hours or 0
        else:
            required_hours = 9999  # Sunday / disable validation

        for row in self.daily_lost_hours_assets_table:
            plant_specific = row.total_plant_specific_lost_hours or 0
            child_general = row.total_general_lost_hours_child or 0
            total_for_row = plant_specific + child_general

            if total_for_row > required_hours:
                frappe.throw(
                    f"Row #{row.idx}: (Plant Specific Lost Hours: {plant_specific} "
                    f"+ General Lost Hours Child: {child_general}) = {total_for_row} "
                    f"exceeds the required hours ({required_hours}) on {self.day_of_week}."
                )


# ------------------------------------------------------------------
# WHITELIST METHODS
# ------------------------------------------------------------------

@frappe.whitelist()
def get_monthly_production_planning(location, shift_date):
    """
    Auto-select Monthly Production Planning based on:
      - same location
      - prod_month_start_date <= shift_date <= prod_month_end_date
    Returns the plan name or None.
    """
    if not location or not shift_date:
        return None

    plan_name = frappe.db.get_value(
        "Monthly Production Planning",
        filters={
            "location": location,
            "prod_month_start_date": ["<=", shift_date],
            "prod_month_end_date": [">=", shift_date],
        },
        fieldname="name",
        order_by="prod_month_start_date desc",
    )

    return plan_name


@frappe.whitelist()
def get_shift_system(monthly_production_planning):
    if monthly_production_planning:
        return frappe.db.get_value(
            "Monthly Production Planning",
            monthly_production_planning,
            "shift_system"
        )
    return None


@frappe.whitelist()
def get_assets(location):
    asset_location_field = None
    doctype_meta = frappe.get_doc("DocType", "Asset")

    for field in doctype_meta.fields:
        if "location" in (field.fieldname or "").lower():
            asset_location_field = field.fieldname
            break

    if not asset_location_field:
        frappe.throw("No location field found in 'Asset'. Please check the Doctype configuration.")

    return frappe.db.get_all(
        "Asset",
        filters={asset_location_field: location, "docstatus": 1},
        fields=["name as asset_name", "item_name", "asset_category"],
        order_by="asset_category ASC"
    ) or []

# GENERAL_LOST_HOURS_TABLE_V1_START

GENERAL_LOST_HOUR_REASONS = {
    "Tramming Of machine":
        "Production time lost while tramming or relocating a machine.",

    "Training Non Work Hours":
        "Training, induction or scheduled learning activities that stop normal production.",

    "Weather Conditions Non Work Hours":
        "Weather-related stoppages such as rain, lightning, fog, excessive wind or unsafe conditions.",

    "VFL Non Work Hours":
        "Non-work time related to VFL activities, inspections or engagements.",

    "Other Non Work Hours":
        "Any approved non-work time that does not fit another listed category.",

    "Dust and/or Water Bowser Lost Hours":
        "Production time lost due to dust suppression requirements or water bowser availability / delays.",

    "Diesel and/or Diesel Bowser Lost Hours":
        "Production time lost due to refuelling, diesel availability or diesel bowser delays.",

    "Blasting":
        "Production time lost due to blasting clearance, evacuation, exclusion zones or waiting for blasting to finish.",

    "Safety Stoppage Lost Hours":
        "Safety stand-downs, unsafe conditions, safety instructions.",

    "Pre-Shift / Toolbox Talk Lost Hours":
        "Toolbox talks or extended pre-shift meetings.",

    "Shift Change Lost Hours":
        "Delayed handover between Day/Night shifts.",

    "No Operator / Labour Lost Hours":
        "Operator shortages, absenteeism, labour availability.",

    "Industrial Action Lost Hours":
        "Strike, work stoppage or labour-related interruption.",

    "Pit Access / Road Lost Hours":
        "Haul road blocked, unsafe road, no access to loading area.",

    "Road Maintenance Lost Hours":
        "Grading, road repairs or maintenance preventing production.",

    "Dewatering / Water in Pit Lost Hours":
        "Flooding, pumping or excessive water preventing mining.",

    "Survey Lost Hours":
        "Waiting for survey markings, measurements or clearance.",

    "Geology Lost Hours":
        "Waiting for geological identification or instructions.",

    "Mining Permit / Clearance Lost Hours":
        "Permit, authorisation or area-release delays.",

    "Bench / Face Preparation Lost Hours":
        "Loading face not ready, cleanup or preparation required.",

    "Crusher / Tip / Dump Lost Hours":
        "Crusher, tip, stockpile or dumping area unavailable.",

    "Traffic / Congestion Lost Hours":
        "Queuing or congestion affecting hauling.",

    "Power Failure Lost Hours":
        "Electrical supply failure affecting operations.",

    "Communication / System Lost Hours":
        "Radio, network, ERP or dispatch system failure.",

    "Community / External Stoppage Lost Hours":
        "Community protest, road closure or other external disruption.",

    "Environmental Stoppage Lost Hours":
        "Environmental restriction or inspection stopping work.",

    "Planned Production Delay Lost Hours":
        "Planned operational stoppages not covered elsewhere.",

    "Waiting for Instructions Lost Hours":
        "Production stopped while awaiting management or supervisor direction."
}


GENERAL_LOST_HOUR_PARENT_FIELDS = {
    "Training Non Work Hours":
        "gen_training_hours",

    "Weather Conditions Non Work Hours":
        "weather_non_work_hours",

    "VFL Non Work Hours":
        "vfl_non_work_hours",

    "Other Non Work Hours":
        "other_non_work_hours",

    "Dust and/or Water Bowser Lost Hours":
        "dust_water_bowser_issues_hours",

    "Diesel and/or Diesel Bowser Lost Hours":
        "diesel_or_diesel_bowser_hours",

    "Blasting":
        "blasting"
}


GENERAL_LOST_HOUR_CHILD_FIELDS = {
    "Training Non Work Hours":
        "gen_training_hours_child",

    "Weather Conditions Non Work Hours":
        "weather_non_work_hours_child",

    "VFL Non Work Hours":
        "vfl_non_work_hours_child",

    "Other Non Work Hours":
        "other_non_work_hours_child",

    "Dust and/or Water Bowser Lost Hours":
        "dust_water_bowser_issues_hours_child",

    "Diesel and/or Diesel Bowser Lost Hours":
        "diesel_or_diesel_bowser_hours_child"
}


def _general_lost_hour_seconds(value):
    if value is None:
        return None

    if hasattr(
        value,
        "total_seconds"
    ):
        return float(
            value.total_seconds()
        )

    if hasattr(
        value,
        "hour"
    ):
        return (
            value.hour * 3600
            + value.minute * 60
            + value.second
        )

    value = str(value)

    parts = value.split(":")

    if len(parts) < 2:
        return None

    hours = int(parts[0] or 0)
    minutes = int(parts[1] or 0)

    seconds = 0.0

    if len(parts) > 2:
        seconds = float(
            parts[2] or 0
        )

    return (
        hours * 3600
        + minutes * 60
        + seconds
    )


def _general_lost_hour_duration(row):
    if not row.reason_description:
        frappe.throw(
            "Please select Reason / Description for "
            + category
            + "."
        )


    if (
        not row.start_time
        or not row.end_time
    ):
        return 0.0

    start = _general_lost_hour_seconds(
        row.start_time
    )

    end = _general_lost_hour_seconds(
        row.end_time
    )

    if (
        start is None
        or end is None
    ):
        return 0.0

    seconds = end - start

    if seconds < 0:
        seconds += 24 * 3600

    return round(
        seconds / 3600,
        2
    )


# GLH_MASTER_DRIVEN_CATEGORIES_START

def _general_lost_hour_normalize_category(value):
    """
    Normalize category text before comparing it.

    Handles:
    - leading / trailing spaces
    - repeated spaces
    - non-breaking spaces
    - zero-width characters
    - Unicode formatting differences
    """

    import unicodedata

    text = unicodedata.normalize(
        "NFKC",
        str(
            value
            or ""
        ),
    )

    text = (
        text
        .replace("\u200b", "")
        .replace("\u200c", "")
        .replace("\u200d", "")
        .replace("\ufeff", "")
    )

    return " ".join(
        text.split()
    )


def _general_lost_hour_allowed_categories():
    """
    Return:

        normalized/casefold category
            ->
        canonical category

    Daily General Lost Hour Reason is the primary source.

    GENERAL_LOST_HOUR_REASONS remains as legacy/fallback
    support for older categories.
    """

    categories = {}


    # --------------------------------------------------------
    # Legacy / fallback categories
    # --------------------------------------------------------

    for category in GENERAL_LOST_HOUR_REASONS:

        canonical = (
            _general_lost_hour_normalize_category(
                category
            )
        )

        if not canonical:
            continue

        categories[
            canonical.casefold()
        ] = canonical


    # --------------------------------------------------------
    # Reason Master categories are authoritative.
    # --------------------------------------------------------

    if frappe.db.exists(
        "DocType",
        "Daily General Lost Hour Reason",
    ):

        master_categories = frappe.get_all(
            "Daily General Lost Hour Reason",
            filters={
                "enabled":
                    1
            },
            pluck="lost_hour_category",
            limit_page_length=0,
        )


        for category in master_categories:

            canonical = (
                _general_lost_hour_normalize_category(
                    category
                )
            )

            if not canonical:
                continue

            categories[
                canonical.casefold()
            ] = canonical


    return categories


# GLH_MASTER_DRIVEN_CATEGORIES_END


def sync_general_lost_hours_table(doc):
    rows = (
        doc.get(
            "general_lost_hours_table"
        )
        or []
    )

    allowed_categories = (
        _general_lost_hour_allowed_categories()
    )

    category_totals = {
        category: 0.0
        for category
        in allowed_categories.values()
    }

    grand_total = 0.0
    comments = []


    for row in rows:

        category = (
            _general_lost_hour_normalize_category(
                row.lost_hour_category
            )
        )


        if category:

            canonical_category = (
                allowed_categories.get(
                    category.casefold()
                )
            )


            if not canonical_category:

                frappe.throw(
                    "Invalid Lost Hour Category: "
                    + category
                )


            category = canonical_category


            if (
                row.lost_hour_category
                != canonical_category
            ):

                row.lost_hour_category = (
                    canonical_category
                )

        if (
            not row.location
            and doc.location
        ):
            row.location = doc.location


        # ----------------------------------------------------
        # KEEP THE EXACT REASON SELECTED BY THE USER
        # ----------------------------------------------------
        #
        # Do not derive Reason / Description from the category.
        # The category only filters which reasons may be selected.
        #
        # Example:
        # Communication / System Lost Hours
        #   -> ERP or dispatch system failure.
        #
        # Must remain exactly that after Save / Refresh / Reopen.
        # ----------------------------------------------------

        reason = str(
            row.reason_description
            or ""
        ).strip()


        if category and not reason:
            frappe.throw(
                "Please select Reason / Description "
                "on General Lost Hours row "
                + str(row.idx)
                + "."
            )


        if reason:

            reason_master = frappe.db.get_value(
                "Daily General Lost Hour Reason",
                reason,
                [
                    "name",
                    "lost_hour_category",
                    "reason_description",
                    "enabled",
                ],
                as_dict=True,
            )


            # Compatibility for any older records that may contain
            # reason text instead of the Link document name.
            if not reason_master:

                reason_master = frappe.db.get_value(
                    "Daily General Lost Hour Reason",
                    {
                        "reason_description":
                            reason
                    },
                    [
                        "name",
                        "lost_hour_category",
                        "reason_description",
                        "enabled",
                    ],
                    as_dict=True,
                )


            if not reason_master:
                frappe.throw(
                    "Invalid Reason / Description on "
                    "General Lost Hours row "
                    + str(row.idx)
                    + ": "
                    + reason
                )


            if not reason_master.get(
                "enabled"
            ):
                frappe.throw(
                    "Reason / Description is disabled on "
                    "General Lost Hours row "
                    + str(row.idx)
                    + ": "
                    + str(
                        reason_master.get(
                            "reason_description"
                        )
                        or reason
                    )
                )


            reason_category = str(
                reason_master.get(
                    "lost_hour_category"
                )
                or ""
            ).strip()


            if (
                category
                and reason_category
                and reason_category != category
            ):
                frappe.throw(
                    "Reason / Description on General Lost "
                    "Hours row "
                    + str(row.idx)
                    + " does not belong to "
                    + category
                    + "."
                )


            # Link fields must store the Reason master document name.
            # With the current Reason master this is the exact reason
            # text selected by the user.
            row.reason_description = (
                reason_master.get(
                    "name"
                )
                or reason
            )


        used = bool(
            row.machine
            or category
            or row.start_time
            or row.end_time
            or row.location
        )


        if used and not category:
            frappe.throw(
                "Please select Lost Hour Category "
                "on General Lost Hours row "
                + str(row.idx)
                + "."
            )


        if category:

            if not row.start_time:
                frappe.throw(
                    "Please enter Start Time for "
                    + category
                    + "."
                )

            if not row.end_time:
                frappe.throw(
                    "Please enter End Time for "
                    + category
                    + "."
                )


        row.total_hours = (
            _general_lost_hour_duration(
                row
            )
        )


        hours = float(
            row.total_hours or 0
        )


        if category:
            category_totals[
                category
            ] = (
                category_totals.get(
                    category,
                    0.0
                )
                + hours
            )


        grand_total += hours


        if hours > 0:
            parts = []

            if row.machine:
                parts.append(
                    str(row.machine)
                )

            if category:
                parts.append(
                    category
                )

            if row.reason_description:
                parts.append(
                    str(
                        row.reason_description
                    )
                )

            if (
                row.start_time
                and row.end_time
            ):
                parts.append(
                    str(row.start_time)
                    + "-"
                    + str(row.end_time)
                )

            comments.append(
                " - ".join(parts)
            )


    # --------------------------------------------------------
    # Preserve legacy parent fields for compatibility.
    # --------------------------------------------------------

    for (
        category,
        fieldname
    ) in (
        GENERAL_LOST_HOUR_PARENT_FIELDS.items()
    ):
        doc.set(
            fieldname,
            round(
                category_totals.get(
                    category,
                    0
                ),
                2
            )
        )



    doc.general_lost_hours_detail_total = (
        round(
            grand_total,
            2
        )
    )


    doc.total_general_lost_hours = round(
        grand_total,
        2
    )

    doc.gen_lost_hours_comments = (
        "\n".join(comments)
    )


    # --------------------------------------------------------
    # Preserve existing machine child calculations.
    #
    # Blank Machine:
    # applies to all assets.
    #
    # Selected Machine:
    # applies to that asset only.
    # --------------------------------------------------------

    for asset_row in (
        doc.daily_lost_hours_assets_table
        or []
    ):

        asset_category_totals = {
            category: 0.0
            for category
            in allowed_categories.values()
        }

        asset_general_total = 0.0


        for general_row in rows:

            if (
                general_row.machine and general_row.machine != "ALL Equipment" and general_row.machine != asset_row.asset_name
            ):
                continue


            hours = float(
                general_row.total_hours
                or 0
            )

            asset_general_total += hours


            category = (
                general_row.lost_hour_category
                or ""
            )

            if category:
                asset_category_totals[
                    category
                ] = (
                    asset_category_totals.get(
                        category,
                        0.0
                    )
                    + hours
                )


        for (
            category,
            fieldname
        ) in (
            GENERAL_LOST_HOUR_CHILD_FIELDS.items()
        ):
            asset_row.set(
                fieldname,
                round(
                    asset_category_totals.get(
                        category,
                        0
                    ),
                    2
                )
            )


        asset_row.total_general_lost_hours_child = (
            round(
                asset_general_total,
                2
            )
        )

# GENERAL_LOST_HOURS_TABLE_V1_END

# GLH_REASON_OPTIONS_API_START

@frappe.whitelist()
def get_general_lost_hour_reason_options():
    """Return active General Lost Hour reasons grouped by category."""

    rows = frappe.get_all(
        "Daily General Lost Hour Reason",
        filters={
            "enabled": 1
        },
        fields=[
            "lost_hour_category",
            "reason_description",
        ],
        order_by=(
            "lost_hour_category asc, "
            "reason_description asc"
        ),
        limit_page_length=0,
    )

    result = {}

    for row in rows:
        category = (
            row.lost_hour_category
            or ""
        ).strip()

        reason = (
            row.reason_description
            or ""
        ).strip()

        if not category or not reason:
            continue

        result.setdefault(
            category,
            []
        )

        if reason not in result[category]:
            result[category].append(
                reason
            )

    for category in result:
        result[category] = sorted(
            result[category],
            key=lambda value:
                value.casefold()
        )

    return result

# GLH_REASON_OPTIONS_API_END
