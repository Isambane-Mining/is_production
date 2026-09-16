# Copyright (c) 2026, BuFf0k and contributors
# For license information, please see license.txt

from frappe.model.document import Document
import frappe
from frappe import _
from html import escape
from frappe.utils import getdate, add_to_date, nowdate, formatdate


class HourlyProduction(Document):

    # -------------------------------------------------------------------------
    # Hour Slot / Naming Integrity
    # -------------------------------------------------------------------------
    def before_insert(self):
        """
        Calculate the authoritative hour slot BEFORE Frappe generates
        the document name.

        Hourly Production naming is:
            location-prod_date-hour_slot
        """
        self._set_hour_slot_from_shift_num_hour()

    def _set_hour_slot_from_shift_num_hour(self):
        """
        Derive hour_sort_key and hour_slot from shift_num_hour.

        IMPORTANT:
        - Uses the configured shift start time where available.
        - Uses zero-padded HH:00-HH:00 format.
        - Prevents naming fields from silently changing on an
          already-existing Hourly Production document.
        """
        if not self.shift_num_hour:
            return

        try:
            shift_label, idx_str = self.shift_num_hour.rsplit("-", 1)
            idx = int(idx_str)
        except (ValueError, AttributeError):
            frappe.throw(
                _(
                    "Invalid Shift Number of Hour: {0}"
                ).format(self.shift_num_hour)
            )

        shift_label = (shift_label or self.shift or "").strip()

        def get_start_hour(value, fallback):
            if not value:
                return fallback

            try:
                return int(str(value).split(":", 1)[0])
            except (ValueError, TypeError, IndexError):
                return fallback

        if shift_label == "Day":
            base = get_start_hour(
                getattr(self, "day_shift_start", None),
                6,
            )

        elif shift_label == "Morning":
            base = get_start_hour(
                getattr(self, "morning_shift_start", None),
                6,
            )

        elif shift_label == "Afternoon":
            base = get_start_hour(
                getattr(self, "afternoon_shift_start", None),
                14,
            )

        elif shift_label == "Night":
            base = get_start_hour(
                getattr(self, "night_shift_start", None),
                18,
            )

        else:
            frappe.throw(
                _("Unsupported shift: {0}").format(shift_label)
            )

        start = (base + (idx - 1)) % 24
        end = (start + 1) % 24

        expected_slot = (
            f"{start:02d}:00-{end:02d}:00"
        )

        self.hour_sort_key = idx

        # -----------------------------------------------------
        # Existing-document protection
        # -----------------------------------------------------
        # Never silently change location/date/hour so that the
        # stored name and naming fields disagree.
        # -----------------------------------------------------
        if (
            self.name
            and frappe.db.exists("Hourly Production", self.name)
            and self.location
            and self.prod_date
        ):
            expected_name = (
                f"{self.location}-"
                f"{self.prod_date}-"
                f"{expected_slot}"
            )

            if self.name != expected_name:
                frappe.throw(
                    _(
                        "Hourly Production naming fields cannot be "
                        "changed after the document has been created.<br><br>"
                        "Current document:<br><b>{0}</b><br><br>"
                        "Selected values would require:<br><b>{1}</b><br><br>"
                        "Please correct the document through the approved "
                        "rename/repair process instead of changing its "
                        "Site, Production Date, Shift or Hour."
                    ).format(self.name, expected_name),
                    title=_("Hourly Production Name Protection"),
                )

        self.hour_slot = expected_slot

        if self.meta.has_field("locked_hour_slot"):
            self.locked_hour_slot = expected_slot

    def _protect_existing_naming_fields(self):
        """
        Prevent existing Hourly Production records from silently
        changing the fields that define their document identity.

        Historical records that already contain an old name/hour
        mismatch remain editable for normal production data.
        """
        if self.is_new():
            return

        stored = frappe.db.get_value(
            "Hourly Production",
            self.name,
            [
                "location",
                "prod_date",
                "shift",
                "shift_num_hour",
                "hour_slot",
                "hour_sort_key",
            ],
            as_dict=True,
        )

        if not stored:
            return

        def clean(value):
            return str(value or "").strip()

        def clean_date(value):
            if not value:
                return ""
            try:
                return str(getdate(value))
            except Exception:
                return clean(value)

        changes = []

        checks = [
            (
                "Site",
                clean(stored.location),
                clean(self.location),
            ),
            (
                "Production Date",
                clean_date(stored.prod_date),
                clean_date(self.prod_date),
            ),
            (
                "Shift",
                clean(stored.shift),
                clean(self.shift),
            ),
            (
                "Shift Number of Hour",
                clean(stored.shift_num_hour),
                clean(self.shift_num_hour),
            ),
        ]

        for label, old_value, new_value in checks:
            if old_value != new_value:
                changes.append(
                    f"{label}: "
                    f"{old_value or '-'} "
                    f"→ {new_value or '-'}"
                )

        if changes:
            frappe.throw(
                _(
                    "The naming fields of an existing Hourly Production "
                    "record cannot be changed."
                    "<br><br>"
                    "Document:<br><b>{0}</b>"
                    "<br><br>{1}"
                    "<br><br>"
                    "Create the correct hourly record or use the "
                    "approved rename/repair process."
                ).format(
                    self.name,
                    "<br>".join(changes),
                ),
                title=_(
                    "Hourly Production Name Protection"
                ),
            )

        # Existing records retain the stored naming hour.
        # This also keeps old legacy mismatches editable.
        self.hour_slot = stored.hour_slot

        if stored.hour_sort_key is not None:
            self.hour_sort_key = stored.hour_sort_key

        if self.meta.has_field("locked_hour_slot"):
            self.locked_hour_slot = stored.hour_slot


    # -------------------------------------------------------------------------
    # Asset Link Normalization (v16 migration)
    # -------------------------------------------------------------------------
    def normalize_asset_links(self):
        """
        Ensure all child-table Link fields point to Asset.name (primary key),
        while keeping Plant No (Asset.asset_name) in separate Data fields.

        This fixes saves failing with LinkValidationError when legacy code stored
        Asset.asset_name (Plant No) inside a Link field after v16 migration.
        """

        def _resolve_asset(value):
            """Return tuple (asset_name_pk, plant_no, item_name) for an Asset lookup."""
            if not value:
                return (None, None, None)

            # 1) If it's already an Asset.name, keep it
            if frappe.db.exists('Asset', value):
                plant_no, item_name = frappe.db.get_value('Asset', value, ['asset_name', 'item_name']) or (None, None)
                return (value, plant_no, item_name)

            # 2) Otherwise treat it as Plant No (Asset.asset_name)
            asset_pk = frappe.db.get_value('Asset', {'asset_name': value}, 'name')
            if asset_pk:
                plant_no, item_name = frappe.db.get_value('Asset', asset_pk, ['asset_name', 'item_name']) or (None, None)
                return (asset_pk, plant_no, item_name)

            return (None, None, None)

        # Truck Loads child table
        for row in getattr(self, 'truck_loads', []) or []:
            # Truck link
            pk, plant_no, item_name = _resolve_asset(getattr(row, 'asset_name_truck', None))
            if pk:
                row.asset_name_truck = pk
                if hasattr(row, 'truck_plant_no'):
                    row.truck_plant_no = plant_no or ''
                if hasattr(row, 'item_name') and item_name is not None:
                    row.item_name = item_name

            # Excavator/Shoval link (optional)
            if hasattr(row, 'asset_name_shoval'):
                epk, eplant_no, _ = _resolve_asset(getattr(row, 'asset_name_shoval', None))
                if epk:
                    row.asset_name_shoval = epk
                    if hasattr(row, 'excavator_plant_no'):
                        row.excavator_plant_no = eplant_no or ''

        # Dozer Production child table
        for row in getattr(self, 'dozer_production', []) or []:
            pk, plant_no, item_name = _resolve_asset(getattr(row, 'asset_name', None))
            if pk:
                row.asset_name = pk
                if hasattr(row, 'dozer_plant_no'):
                    row.dozer_plant_no = plant_no or ''
                if hasattr(row, 'item_name') and item_name is not None:
                    row.item_name = item_name

    def validate(self):
        """General validation before save"""
        # Server-side fallback: rebuild truck rows if the browser only sent a blank row
        self.populate_truck_loads_if_blank()

        # v16 migration safety: ensure Link fields store Asset.name (primary key)
        self.normalize_asset_links()
        self.populate_truck_material_types()
        self.apply_planning_tub_factors()
        self.validate_truck_loads()
        self.validate_dozer_production()
        # self.validate_and_apply_tub_factors()
        self.before_save_logic()  # <-- NOW RUNS AUTOMATICALLY

    def populate_truck_loads_if_blank(self):
        """Populate truck_loads from Asset if the form only has blank/default rows."""
        if not self.location:
            return

        valid_rows = [
            row for row in (self.truck_loads or [])
            if row.asset_name_truck and row.item_name
        ]

        if valid_rows:
            return

        self.set("truck_loads", [])

        trucks = frappe.get_all(
            "Asset",
            filters=[
                ["location", "=", self.location],
                ["asset_category", "in", ["ADT", "RIGID"]],
                ["docstatus", "=", 1],
            ],
            fields=[
                "name",
                "asset_name",
                "item_name",
            ],
            order_by="asset_name asc",
            limit_page_length=500,
        )

        for asset in trucks:
            if not asset.name or not asset.item_name:
                continue

            self.append("truck_loads", {
                "asset_name_truck": asset.name,
                "truck_plant_no": asset.asset_name or "",
                "item_name": asset.item_name or "",
                "loads": 0,
                "mat_type": "Softs",
            })


    def populate_truck_material_types(self):
        """
        Populate Truck Load material types from the selected Geo Material
        Layer before Tub Factor validation runs.
        """
        if not self.month_prod_planning:
            return

        if not frappe.db.exists(
            "Monthly Production Planning",
            self.month_prod_planning,
        ):
            return

        mpp = frappe.get_doc(
            "Monthly Production Planning",
            self.month_prod_planning,
        )

        material_map = {
            row.geo_ref_description: row.custom_material_type
            for row in (mpp.get("geo_mat_layer") or [])
            if row.geo_ref_description
        }

        for row in getattr(self, "truck_loads", []) or []:
            if row.geo_mat_layer_truck and not row.mat_type:
                row.mat_type = material_map.get(
                    row.geo_mat_layer_truck
                )


    def apply_planning_tub_factors(self):
        """
        Apply Tub Factors directly from the selected Monthly Production
        Planning document.

        Matching trims truck model and material type values so legacy
        Asset records containing extra spaces still match planning.
        """
        if not self.month_prod_planning:
            return

        if not frappe.db.exists(
            "Monthly Production Planning",
            self.month_prod_planning,
        ):
            return

        plan = frappe.get_doc(
            "Monthly Production Planning",
            self.month_prod_planning,
        )

        factor_map = {}

        for factor_row in plan.get("tub_factors") or []:
            item_name = str(
                factor_row.item_name or ""
            ).strip()

            mat_type = str(
                factor_row.mat_type or ""
            ).strip()

            if not item_name or not mat_type:
                continue

            factor_map[(item_name, mat_type)] = {
                "link": factor_row.tub_factor,
                "value": float(
                    factor_row.factor_value or 0
                ),
            }

        for row in self.get("truck_loads") or []:
            item_name = str(
                row.item_name or ""
            ).strip()

            mat_type = str(
                row.mat_type or ""
            ).strip()

            factor = factor_map.get(
                (item_name, mat_type)
            )

            if not factor:
                continue

            row.tub_factor_doc_link = factor["link"]
            row.tub_factor = factor["value"]
            row.bcms = (
                float(row.loads or 0)
                * factor["value"]
            )


    def validate_truck_loads(self):
        for row in getattr(self, "truck_loads", []):
            if (row.loads or 0) > 0:
                if not row.geo_mat_layer_truck:
                    frappe.throw(
                        _("Row {0}: Please select a Geo Material Layer for truck {1}")
                        .format(row.idx, row.asset_name_truck or "")
                    )
                if not row.mining_areas_trucks:
                    frappe.throw(
                        _("Row {0}: Please select a Mining Area for truck {1}")
                        .format(row.idx, row.asset_name_truck or "")
                    )

    def validate_and_apply_tub_factors(self):
        """
        Validate Truck Load Tub Factors against the selected
        Monthly Production Planning document.

        Rules:
        - Only submitted Tub Factors approved on the selected MPP may be used.
        - Truck model and material type must match.
        - If one approved factor exists, select it automatically.
        - If multiple approved factors exist, the user must select one.
        - BCM is always recalculated from the validated linked Tub Factor.
        """
        truck_rows = getattr(self, "truck_loads", []) or []

        rows_requiring_factor = [
            row
            for row in truck_rows
            if float(row.loads or 0) > 0
        ]

        if not rows_requiring_factor:
            return

        if not self.month_prod_planning:
            frappe.throw(
                _(
                    "Please select a Monthly Production Plan before "
                    "capturing truck loads."
                )
            )

        if not frappe.db.exists(
            "Monthly Production Planning",
            self.month_prod_planning,
        ):
            frappe.throw(
                _("Monthly Production Plan {0} does not exist.").format(
                    self.month_prod_planning
                )
            )

        mpp = frappe.get_doc(
            "Monthly Production Planning",
            self.month_prod_planning,
        )

        approved_factor_names = {
            row.tub_factor
            for row in (mpp.get("tub_factors") or [])
            if row.tub_factor
        }

        approved_factors = {}

        if approved_factor_names:
            factor_rows = frappe.get_all(
                "Tub Factor",
                filters={
                    "name": ["in", list(approved_factor_names)],
                    "docstatus": 1,
                },
                fields=[
                    "name",
                    "item_name",
                    "mat_type",
                    "tub_factor",
                ],
                limit_page_length=500,
            )

            approved_factors = {
                factor.name: factor
                for factor in factor_rows
            }

        for row in truck_rows:
            loads = float(row.loads or 0)

            if loads <= 0:
                row.bcms = 0
                continue

            matching_factors = [
                factor
                for factor in approved_factors.values()
                if factor.item_name == row.item_name
                and factor.mat_type == row.mat_type
            ]

            selected_factor = None

            if row.tub_factor_doc_link:
                selected_factor = approved_factors.get(
                    row.tub_factor_doc_link
                )

                if not selected_factor:
                    frappe.throw(
                        _(
                            "Row {0}: Tub Factor {1} is not approved "
                            "on Monthly Production Plan {2}."
                        ).format(
                            row.idx,
                            row.tub_factor_doc_link,
                            self.month_prod_planning,
                        )
                    )

                if selected_factor.item_name != row.item_name:
                    frappe.throw(
                        _(
                            "Row {0}: Tub Factor {1} is for truck model "
                            "{2}, not {3}."
                        ).format(
                            row.idx,
                            selected_factor.name,
                            selected_factor.item_name,
                            row.item_name,
                        )
                    )

                if selected_factor.mat_type != row.mat_type:
                    frappe.throw(
                        _(
                            "Row {0}: Tub Factor {1} is for material "
                            "{2}, not {3}."
                        ).format(
                            row.idx,
                            selected_factor.name,
                            selected_factor.mat_type,
                            row.mat_type,
                        )
                    )

            elif len(matching_factors) == 1:
                selected_factor = matching_factors[0]
                row.tub_factor_doc_link = selected_factor.name

            elif len(matching_factors) > 1:
                frappe.throw(
                    _(
                        "Row {0}: Multiple Tub Factors are approved for "
                        "{1} and {2}. Please select the applicable "
                        "Tub Factor."
                    ).format(
                        row.idx,
                        row.item_name,
                        row.mat_type,
                    )
                )

            else:
                frappe.throw(
                    _(
                        "Row {0}: No submitted Tub Factor is approved "
                        "on Monthly Production Plan {1} for {2} and {3}."
                    ).format(
                        row.idx,
                        self.month_prod_planning,
                        row.item_name,
                        row.mat_type,
                    )
                )

            row.tub_factor = selected_factor.tub_factor
            row.bcms = loads * float(
                selected_factor.tub_factor or 0
            )


    def validate_dozer_production(self):
        for row in getattr(self, "dozer_production", []):
            if (row.bcm_hour or 0) > 0:
                if not row.dozer_geo_mat_layer:
                    frappe.throw(
                        _("Row {0}: Please select a Geo Material Layer for dozer {1}")
                        .format(row.idx, row.asset_name or "")
                    )
                if not row.mining_areas_dozer_child:
                    frappe.throw(
                        _("Row {0}: Please select a Mining Area for dozer {1}")
                        .format(row.idx, row.asset_name or "")
                    )

    # -------------------------------------------------------------------------
    #     BEFORE SAVE LOGIC (formerly your broken before_save method)
    # -------------------------------------------------------------------------
    def before_save_logic(self):
        """
        Ensures all child tables and derived fields are updated before saving
        the parent document. Also validates Dozer Production entries based on
        the Dozer Service selected. Always recalculates hour_sort_key and
        hour_slot from shift_num_hour.
        """

        # Coal ton calculation
        if self.total_coal_bcm is not None:
            self.coal_tons_total = self.total_coal_bcm * 1.5
        else:
            self.coal_tons_total = 0

        # --- Truck Loads Calculations ---
        total_softs_bcm = total_hards_bcm = total_coal_bcm = total_ts_bcm = 0.0
        num_prod_trucks = 0



        if hasattr(self, 'truck_loads'):
            for row in self.truck_loads:

                # Tub Factor validation and BCM calculation are handled by
                # validate_and_apply_tub_factors() before this method runs.
                try:
                    row.bcms = (
                        float(row.loads or 0)
                        * float(row.tub_factor or 0)
                    )
                except Exception:
                    row.bcms = 0

                if row.bcms:
                    total_ts_bcm += row.bcms
                    if row.mat_type == "Softs":
                        total_softs_bcm += row.bcms
                    elif row.mat_type == "Hards":
                        total_hards_bcm += row.bcms
                    elif row.mat_type == "Coal":
                        total_coal_bcm += row.bcms

                    if row.bcms > 0:
                        num_prod_trucks += 1

        # --- Dozer Production Calculations & Validation ---
        total_dozing_bcm = 0.0
        num_prod_dozers = 0
        allowed_bcm_values = [0,100,110,120,130,140,150,180,190,200]

        if hasattr(self, 'dozer_production'):
            for row in self.dozer_production:
                if row.dozer_service in ["No Dozing", "Tip Dozing", "Levelling"]:
                    if row.bcm_hour != 0:
                        frappe.throw(_(
                            "For Dozer Service '{0}', only a BCM in Hour value of 0 is allowed."
                        ).format(row.dozer_service))
                elif row.dozer_service in ["Production Dozing-50m", "Production Dozing-100m"]:
                    # Kriel Rehabilitation uses fixed production dozing rates.
                    # Enforce the value server-side as well so API/import/stale
                    # browser saves cannot store a different BCM/Hour.
                    if self.location == "Kriel Rehabilitation":
                        required_bcm = (
                            75
                            if row.dozer_service == "Production Dozing-50m"
                            else 150
                        )
                        row.bcm_hour = required_bcm
                    elif row.bcm_hour not in allowed_bcm_values:
                        frappe.throw(_(
                            "For Dozer Service '{0}', BCM in Hour value must be one of {1}."
                        ).format(row.dozer_service, allowed_bcm_values))
                else:
                    frappe.throw(_("Invalid Dozer Service value: {0}.").format(row.dozer_service))

                if row.bcm_hour and row.bcm_hour > 0:
                    total_dozing_bcm += row.bcm_hour
                    num_prod_dozers += 1

        # --- Set Calculated Values ---
        self.total_softs_bcm = total_softs_bcm
        self.total_hards_bcm = total_hards_bcm
        self.total_coal_bcm = total_coal_bcm
        self.total_ts_bcm = total_ts_bcm
        self.num_prod_trucks = num_prod_trucks
        self.total_dozing_bcm = total_dozing_bcm
        self.num_prod_dozers = num_prod_dozers

        hour_total_bcm = total_ts_bcm + total_dozing_bcm
        self.hour_total_bcm = hour_total_bcm
        self.ts_percent = (total_ts_bcm / hour_total_bcm * 100) if hour_total_bcm else 0
        self.dozing_percent = (total_dozing_bcm / hour_total_bcm * 100) if hour_total_bcm else 0
        self.ave_bcm_dozer = (total_dozing_bcm / num_prod_dozers) if num_prod_dozers else 0
        self.ave_bcm_prod_truck = (total_ts_bcm / num_prod_trucks) if num_prod_trucks else 0

        self.calculate_day_total_bcm()

        # --- Authoritative Hour Slot / Naming Protection ---
        if self.is_new():
            self._set_hour_slot_from_shift_num_hour()
        else:
            self._protect_existing_naming_fields()

    # -------------------------------------------------------------------------

    def calculate_day_total_bcm(self):
        """Calculate and set the day_total_bcm from all hourly entries for this location and date"""
        if not self.prod_date or not self.location:
            return

        day_total = frappe.db.sql("""
            SELECT SUM(hour_total_bcm)
            FROM `tabHourly Production`
            WHERE location = %s
            AND prod_date = %s
            AND docstatus < 2
            AND name != %s
        """, (self.location, self.prod_date, self.name), as_list=True)

        current_hour_total = self.hour_total_bcm or 0
        self.day_total_bcm = (day_total[0][0] or 0) + current_hour_total

    def before_print(self, print_settings):
        if getattr(self, 'month_prod_planning', None):
            frappe.get_attr(
                "is_production.production.doctype.monthly_production_planning."
                "monthly_production_planning.update_mtd_production"
            )(name=self.month_prod_planning)

            mpp = frappe.get_doc("Monthly Production Planning", self.month_prod_planning)
            for field in [
                'monthly_target_bcm', 'target_bcm_day', 'target_bcm_hour',
                'month_act_ts_bcm_tallies', 'month_act_dozing_bcm_tallies',
                'monthly_act_tally_survey_variance', 'month_actual_bcm',
                'mtd_bcm_day', 'mtd_bcm_hour', 'month_forecated_bcm'
            ]:
                setattr(self, field, getattr(mpp, field))

    # -------------------------------------------------------------------------
    # Raven Production Reports
    # Replaces old WhatsApp Hour Report and Shift Summary.
    # -------------------------------------------------------------------------

    def _raven_e(self, value):
        return escape(str(value or ""))

    def _raven_num(self, value, decimals=0):
        try:
            value = float(value or 0)
        except Exception:
            value = 0
        if decimals == 0:
            return f"{value:,.0f}"
        return f"{value:,.{decimals}f}"

    def _raven_volume_bg(self, value):
        return "#fff2cc"

    def _raven_volume_text_color(self, value):
        try:
            value = float(value or 0)
        except Exception:
            value = 0

        if value < 200:
            return "#cc0000"   # red text
        elif value <= 219:
            return "#e69138"   # orange text
        else:
            return "#38761d"   # green text

    def _raven_date(self):
        if not self.prod_date:
            return ""
        return formatdate(self.prod_date, "MMMM d, yyyy")

    def get_raven_channel_for_production(self):
        site = (self.location or "").strip()
        site_key = site.lower().replace(" ", "-")

        candidates = [
            f"Raven-prod-{site_key}",
            f"Raven-production-{site_key}",
            f"prod-{site_key}",
            f"production-{site_key}",
            "Raven-production",
            "Production",
        ]

        for channel in candidates:
            if frappe.db.exists("Raven Channel", channel):
                return channel

        frappe.throw(_("Please create Raven channel Raven-production first."))

    def insert_raven_message(self, channel_id, message):
        if not frappe.db.exists("DocType", "Raven Message"):
            frappe.throw(_("Raven is not installed on this site."))

        meta = frappe.get_meta("Raven Message")
        doc = frappe.new_doc("Raven Message")

        if meta.has_field("channel_id"):
            doc.channel_id = channel_id
        elif meta.has_field("channel"):
            doc.channel = channel_id
        elif meta.has_field("raven_channel"):
            doc.raven_channel = channel_id

        if meta.has_field("message_type"):
            doc.message_type = "Text"

        if meta.has_field("text"):
            doc.text = message
        elif meta.has_field("message"):
            doc.message = message
        elif meta.has_field("content"):
            doc.content = message

        if meta.has_field("json"):
            doc.json = frappe.as_json({
                "doctype": self.doctype,
                "docname": self.name,
                "location": self.location,
                "prod_date": str(self.prod_date or ""),
                "shift": self.shift,
                "hour_slot": self.hour_slot,
            })

        doc.insert(ignore_permissions=True)
        return doc

    def get_raven_monthly_statistics_html(self):
        monthly_target = self.monthly_target_bcm or 0
        production_to_date = self.month_actual_bcm or 0
        remaining_bcm = monthly_target - production_to_date

        rows = [
            ("Monthly Target BCM", monthly_target, 0),
            ("Daily Target", self.target_bcm_day, 0),
            ("Target Hourly Rate", self.target_bcm_hour, 0),
            ("Production to Date", production_to_date, 0),
            ("Remaining BCM", remaining_bcm, 0),
            ("Current Rate", self.mtd_bcm_hour, 0),
            ("Required Rate", self.mtd_bcm_day, 0),
            ("Strip Ratio", self.monthly_act_tally_survey_variance, 2),
            ("Forecasted BCM", self.month_forecated_bcm, 0),
        ]

        html = """
        <table style="border-collapse:collapse;width:100%;font-size:9px;background:#ffffff;color:#000000;">
            <tr>
                <th colspan="2" style="background:#1f4e79;color:#ffffff;border:1px solid #000;padding:4px;text-align:left;font-size:9px;">
                    Monthly Statistics
                </th>
            </tr>
        """

        for label, value, decimals in rows:
            html += f"""
            <tr>
                <td style="background:#d9e2f3;color:#000000;border:1px solid #000;padding:4px;text-align:center;width:68%;">
                    {self._raven_e(label)}
                </td>
                <td style="background:#ffffff;color:#000000;border:1px solid #000;padding:4px;text-align:right;font-weight:bold;width:32%;">
                    {self._raven_num(value, decimals)}
                </td>
            </tr>
            """

        html += "</table>"
        return html

    def get_raven_hour_report_html(self):
        number_of_utilized_trucks = len({
            row.asset_name_truck
            for row in self.truck_loads or []
            if row.asset_name_truck and (row.loads or 0) > 0
        })

        excavator_groups = {}

        for row in self.truck_loads or []:
            if not (row.bcms or 0):
                continue

            excavator = row.excavator_plant_no or row.asset_name_shoval or "No Excavator"
            area = row.mining_areas_trucks or ""
            material = row.geo_mat_layer_truck or ""
            key = (excavator, area, material)

            if key not in excavator_groups:
                excavator_groups[key] = {
                    "excavator": excavator,
                    "area": area,
                    "material": material,
                    "trucks": set(),
                    "loads": 0,
                    "volume": 0,
                }

            excavator_groups[key]["trucks"].add(row.truck_plant_no or row.asset_name_truck or "")
            excavator_groups[key]["loads"] += row.loads or 0
            excavator_groups[key]["volume"] += row.bcms or 0

        excavator_rows = ""

        if excavator_groups:
            for data in excavator_groups.values():
                excavator_rows += f"""
                <tr>
                    <td colspan="3" style="background:#ffffff;color:#000000;border:1px solid #000;padding:5px;font-size:11px;font-weight:bold;">
                        {self._raven_e(data["excavator"])} &nbsp; | &nbsp;
                        {self._raven_e(data["area"])} &nbsp; | &nbsp;
                        {self._raven_e(data["material"])}
                    </td>
                </tr>
                <tr>
                    <td style="background:#d9e2f3;color:#000000;border:1px solid #000;padding:4px;text-align:center;font-size:9px;width:29%;white-space:nowrap;">
                        Trucks<br><b style="font-size:11px;">{len(data["trucks"])}</b>
                    </td>
                    <td style="background:#d9e2f3;color:#000000;border:1px solid #000;padding:4px;text-align:center;font-size:9px;width:29%;white-space:nowrap;">
                        Loads<br><b style="font-size:11px;">{self._raven_num(data["loads"])}</b>
                    </td>
                    <td style="background:#fff2cc;color:#000000;border:1px solid #000;padding:4px;text-align:center;font-size:9px;width:42%;white-space:nowrap;">
                        Volume<br><b style="font-size:11px;color:{self._raven_volume_text_color(data["volume"])};">{self._raven_num(data["volume"])}</b>
                    </td>
                </tr>
                """
        else:
            excavator_rows = """
            <tr>
                <td colspan="3" style="border:1px solid #000;padding:8px;text-align:center;background:#ffffff;color:#000000;font-weight:bold;">
                    No Excavator Data
                </td>
            </tr>
            """

        dozer_rows = ""
        for row in self.dozer_production or []:
            if not (row.bcm_hour or 0):
                continue

            dozer_rows += f"""
            <tr>
                <td colspan="3" style="background:#ffffff;color:#000000;border:1px solid #000;padding:5px;font-size:11px;font-weight:bold;">
                    {self._raven_e(row.dozer_plant_no or row.asset_name)} &nbsp; | &nbsp;
                    {self._raven_e(row.mining_areas_dozer_child)} &nbsp; | &nbsp;
                    {self._raven_e(row.dozer_geo_mat_layer)}
                </td>
            </tr>
            <tr>
                <td colspan="3" style="background:#fff2cc;color:#000000;border:1px solid #000;padding:5px;text-align:center;font-size:10px;font-weight:bold;">
                    Volume: <span style="color:{self._raven_volume_text_color(row.bcm_hour)};">{self._raven_num(row.bcm_hour)}</span>
                </td>
            </tr>
            """

        if not dozer_rows:
            dozer_rows = """
            <tr>
                <td colspan="3" style="border:1px solid #000;padding:8px;text-align:center;background:#ffffff;color:#000000;font-weight:bold;">
                    No Dozer Data
                </td>
            </tr>
            """

        return f"""
        <div style="background:#ffffff;color:#000000;font-family:Arial,sans-serif;width:100%;max-width:430px;margin:0 auto;padding:4px;box-sizing:border-box;">
            <div style="text-align:center;font-size:18px;font-weight:bold;color:#1f4e79;margin:2px 0 5px 0;">
                Hour Report
            </div>

            <div style="text-align:center;font-size:11px;font-weight:bold;color:#000000;margin-bottom:6px;line-height:1.4;">
                Site: {self._raven_e(self.location)} &nbsp; | &nbsp;
                Shift: {self._raven_e(self.shift)}<br>
                Date: {self._raven_e(self._raven_date())} &nbsp; | &nbsp;
                Hour: {self._raven_e(self.hour_slot)}
            </div>

            <table style="border-collapse:collapse;width:100%;background:#ffffff;color:#000000;">
                <tr>
                    <td style="vertical-align:top;width:62%;padding-right:5px;background:#ffffff;color:#000000;">

                        <table style="border-collapse:collapse;width:100%;font-size:10px;background:#ffffff;color:#000000;margin-bottom:6px;">
                            <tr>
                                <th colspan="3" style="background:#1f4e79;color:#ffffff;border:1px solid #000;padding:5px;text-align:left;font-size:11px;">
                                    Excavator Production
                                </th>
                            </tr>
                            {excavator_rows}
                        </table>

                        <table style="border-collapse:collapse;width:100%;font-size:10px;background:#ffffff;color:#000000;margin-bottom:6px;">
                            <tr>
                                <th colspan="3" style="background:#1f4e79;color:#ffffff;border:1px solid #000;padding:5px;text-align:left;font-size:11px;">
                                    Dozer Production
                                </th>
                            </tr>
                            {dozer_rows}
                        </table>

                        <table style="border-collapse:collapse;width:100%;font-size:10px;background:#ffffff;color:#000000;margin-bottom:6px;">
                            <tr>
                                <th colspan="3" style="background:#1f4e79;color:#ffffff;border:1px solid #000;padding:5px;text-align:left;font-size:11px;">
                                    Drill Production
                                </th>
                            </tr>
                            <tr>
                                <td colspan="3" style="border:1px solid #000;padding:8px;text-align:center;background:#ffffff;color:#000000;font-weight:bold;">
                                    No drill data available.
                                </td>
                            </tr>
                        </table>

                    </td>

                    <td style="vertical-align:top;width:38%;background:#ffffff;color:#000000;">
                        {self.get_raven_monthly_statistics_html()}
                    </td>
                </tr>
            </table>

            <table style="border-collapse:collapse;width:100%;font-size:11px;background:#ffffff;color:#000000;margin-top:5px;">
                <tr>
                    <th colspan="2" style="background:#1f4e79;color:#ffffff;border:1px solid #000;padding:5px;text-align:left;">
                        Hour Summary
                    </th>
                </tr>
                <tr>
                    <td style="background:#d9e2f3;color:#000000;border:1px solid #000;padding:7px;text-align:center;">Number of Utilized Trucks</td>
                    <td style="background:#fff2cc;color:#000000;border:1px solid #000;padding:7px;text-align:center;font-weight:bold;">{self._raven_num(number_of_utilized_trucks, 0)}</td>
                </tr>
                <tr>
                    <td style="background:#d9e2f3;color:#000000;border:1px solid #000;padding:7px;text-align:center;width:72%;">Total BCM TS</td>
                    <td style="background:#fff2cc;color:#000000;border:1px solid #000;padding:7px;text-align:center;font-weight:bold;">{self._raven_num(self.total_ts_bcm)}</td>
                </tr>
                <tr>
                    <td style="background:#d9e2f3;color:#000000;border:1px solid #000;padding:7px;text-align:center;">Total BCM Dozing</td>
                    <td style="background:#fff2cc;color:#000000;border:1px solid #000;padding:7px;text-align:center;font-weight:bold;">{self._raven_num(self.total_dozing_bcm)}</td>
                </tr>
                <tr>
                    <td style="background:#d9e2f3;color:#000000;border:1px solid #000;padding:7px;text-align:center;">Total Shift Acc BCM</td>
                    <td style="background:#fff2cc;color:#000000;border:1px solid #000;padding:7px;text-align:center;font-weight:bold;">{self._raven_num(self.day_total_bcm)}</td>
                </tr>
            </table>
        </div>
        """

    def get_raven_shift_docs(self):
        if not self.location or not self.prod_date or not self.shift:
            return []

        rows = frappe.get_all(
            "Hourly Production",
            filters={
                "location": self.location,
                "prod_date": self.prod_date,
                "shift": self.shift,
                "docstatus": ["<", 2],
            },
            fields=["name"],
            order_by="hour_sort_key asc, creation asc",
            limit_page_length=500,
        )

        return [frappe.get_doc("Hourly Production", row.name) for row in rows]

    def get_raven_shift_summary_html(self):
        docs = self.get_raven_shift_docs()

        excavator_groups = {}
        dozer_groups = {}
        material_totals = {}

        total_ts = 0
        total_dozing = 0
        diesel_used = 0
        water_used = 0

        for doc in docs:
            total_ts += doc.total_ts_bcm or 0
            total_dozing += doc.total_dozing_bcm or 0
            diesel_used += doc.diesel_used or 0
            water_used += doc.water_used or 0

            for row in doc.truck_loads or []:
                if not (row.bcms or 0):
                    continue

                excavator = row.excavator_plant_no or row.asset_name_shoval or "No Excavator"
                area = row.mining_areas_trucks or ""
                material = row.geo_mat_layer_truck or ""

                key = (excavator, area, material)

                if key not in excavator_groups:
                    excavator_groups[key] = {
                        "excavator": excavator,
                        "area": area,
                        "material": material,
                        "volume": 0,
                        "start_hours": None,
                        "end_hours": None,
                    }

                excavator_groups[key]["volume"] += row.bcms or 0

                start_hours = row.exc_start_hours or 0
                end_hours = row.exc_stop_hours or 0

                if start_hours:
                    if excavator_groups[key]["start_hours"] is None:
                        excavator_groups[key]["start_hours"] = start_hours
                    else:
                        excavator_groups[key]["start_hours"] = min(
                            excavator_groups[key]["start_hours"],
                            start_hours
                        )

                if end_hours:
                    if excavator_groups[key]["end_hours"] is None:
                        excavator_groups[key]["end_hours"] = end_hours
                    else:
                        excavator_groups[key]["end_hours"] = max(
                            excavator_groups[key]["end_hours"],
                            end_hours
                        )

                material_totals[material] = material_totals.get(material, 0) + (row.bcms or 0)

            # Dozers are still shown for volume only. Hours are ignored as requested.
            for row in doc.dozer_production or []:
                if not (row.bcm_hour or 0):
                    continue

                dozer = row.dozer_plant_no or row.asset_name or "No Dozer"
                area = row.mining_areas_dozer_child or ""
                material = row.dozer_geo_mat_layer or ""

                key = (dozer, area, material)

                if key not in dozer_groups:
                    dozer_groups[key] = {
                        "dozer": dozer,
                        "area": area,
                        "material": material,
                        "volume": 0,
                    }

                dozer_groups[key]["volume"] += row.bcm_hour or 0

        excavator_rows = ""

        if excavator_groups:
            for data in excavator_groups.values():
                start = data["start_hours"] or 0
                end = data["end_hours"] or 0
                total_hours = 0

                if start and end and end >= start:
                    total_hours = end - start

                excavator_rows += f"""
                <tr>
                    <td colspan="4" style="background:#ffffff;color:#000000;border:1px solid #000;padding:5px;font-size:11px;font-weight:bold;">
                        {self._raven_e(data["excavator"])} &nbsp; | &nbsp;
                        {self._raven_e(data["area"])} &nbsp; | &nbsp;
                        {self._raven_e(data["material"])}
                    </td>
                </tr>
                <tr>
                    <td style="background:#fff2cc;color:#000000;border:1px solid #000;padding:5px;text-align:center;font-size:10px;width:34%;">
                        Volume<br>
                        <b style="font-size:11px;color:{self._raven_volume_text_color(data["volume"])};">
                            {self._raven_num(data["volume"])}
                        </b>
                    </td>
                    <td style="background:#d9e2f3;color:#000000;border:1px solid #000;padding:5px;text-align:center;font-size:9px;width:22%;">
                        Start<br><b>{self._raven_num(start)}</b>
                    </td>
                    <td style="background:#d9e2f3;color:#000000;border:1px solid #000;padding:5px;text-align:center;font-size:9px;width:22%;">
                        End<br><b>{self._raven_num(end)}</b>
                    </td>
                    <td style="background:#d9e2f3;color:#000000;border:1px solid #000;padding:5px;text-align:center;font-size:9px;width:22%;">
                        Hours<br><b>{self._raven_num(total_hours, 0)}</b>
                    </td>
                </tr>
                """
        else:
            excavator_rows = """
            <tr>
                <td colspan="4" style="border:1px solid #000;padding:8px;text-align:center;background:#ffffff;color:#000000;font-weight:bold;">
                    No Excavator Data
                </td>
            </tr>
            """

        dozer_rows = ""

        if dozer_groups:
            for data in dozer_groups.values():
                dozer_rows += f"""
                <tr>
                    <td colspan="3" style="background:#ffffff;color:#000000;border:1px solid #000;padding:5px;font-size:11px;font-weight:bold;">
                        {self._raven_e(data["dozer"])} &nbsp; | &nbsp;
                        {self._raven_e(data["area"])} &nbsp; | &nbsp;
                        {self._raven_e(data["material"])}
                    </td>
                </tr>
                <tr>
                    <td colspan="3" style="background:#fff2cc;color:#000000;border:1px solid #000;padding:5px;text-align:center;font-size:10px;font-weight:bold;">
                        Volume:
                        <span style="color:{self._raven_volume_text_color(data["volume"])};">
                            {self._raven_num(data["volume"])}
                        </span>
                    </td>
                </tr>
                """
        else:
            dozer_rows = """
            <tr>
                <td colspan="3" style="border:1px solid #000;padding:8px;text-align:center;background:#ffffff;color:#000000;font-weight:bold;">
                    No Dozer Data
                </td>
            </tr>
            """

        material_rows = ""

        if material_totals:
            for material, volume in material_totals.items():
                material_rows += f"""
                <tr>
                    <td style="background:#d9e2f3;color:#000000;border:1px solid #000;padding:7px;text-align:center;">
                        {self._raven_e(material)}
                    </td>
                    <td style="background:#ffffff;color:#000000;border:1px solid #000;padding:7px;text-align:center;font-weight:bold;">
                        {self._raven_num(volume)}
                    </td>
                </tr>
                """
        else:
            material_rows = """
            <tr>
                <td colspan="2" style="background:#ffffff;color:#000000;border:1px solid #000;padding:7px;text-align:center;font-weight:bold;">
                    No TS Material
                </td>
            </tr>
            """

        total_shift_bcm = total_ts + total_dozing

        return f"""
        <div style="background:#ffffff;color:#000000;font-family:Arial,sans-serif;width:100%;max-width:430px;margin:0 auto;padding:4px;box-sizing:border-box;">
            <div style="text-align:center;font-size:18px;font-weight:bold;color:#1f4e79;margin:2px 0 5px 0;">
                Shift Summary
            </div>

            <div style="text-align:center;font-size:11px;font-weight:bold;color:#000000;margin-bottom:6px;line-height:1.4;">
                Site: {self._raven_e(self.location)} &nbsp; | &nbsp;
                Shift: {self._raven_e(self.shift)}<br>
                Date: {self._raven_e(self._raven_date())}
            </div>

            {self.get_raven_monthly_statistics_html()}

            <table style="border-collapse:collapse;width:100%;font-size:10px;background:#ffffff;color:#000000;margin-bottom:7px;">
                <tr>
                    <th colspan="4" style="background:#1f4e79;color:#ffffff;border:1px solid #000;padding:5px;text-align:left;font-size:11px;">
                        Excavator Production Full Shift
                    </th>
                </tr>
                {excavator_rows}
            </table>

            <table style="border-collapse:collapse;width:100%;font-size:10px;background:#ffffff;color:#000000;margin-bottom:7px;">
                <tr>
                    <th colspan="3" style="background:#1f4e79;color:#ffffff;border:1px solid #000;padding:5px;text-align:left;font-size:11px;">
                        Dozer Production Full Shift
                    </th>
                </tr>
                {dozer_rows}
            </table>

            <table style="border-collapse:collapse;width:100%;font-size:11px;background:#ffffff;color:#000000;margin-top:5px;">
                <tr>
                    <th colspan="2" style="background:#1f4e79;color:#ffffff;border:1px solid #000;padding:5px;text-align:left;">
                        Shift Report
                    </th>
                </tr>
                <tr>
                    <td style="background:#d9e2f3;color:#000000;border:1px solid #000;padding:7px;text-align:center;width:72%;">Total BCM TS</td>
                    <td style="background:#fff2cc;color:#000000;border:1px solid #000;padding:7px;text-align:center;font-weight:bold;">{self._raven_num(total_ts)}</td>
                </tr>
                <tr>
                    <td style="background:#d9e2f3;color:#000000;border:1px solid #000;padding:7px;text-align:center;">Total BCM Dozing</td>
                    <td style="background:#fff2cc;color:#000000;border:1px solid #000;padding:7px;text-align:center;font-weight:bold;">{self._raven_num(total_dozing)}</td>
                </tr>
                <tr>
                    <td colspan="2" style="background:#d9e2f3;color:#000000;border:1px solid #000;padding:7px;font-weight:bold;">
                        TS Material
                    </td>
                </tr>
                {material_rows}
                <tr>
                    <td style="background:#d9e2f3;color:#000000;border:1px solid #000;padding:7px;text-align:center;">Total Shift BCM</td>
                    <td style="background:#fff2cc;color:#000000;border:1px solid #000;padding:7px;text-align:center;font-weight:bold;">{self._raven_num(total_shift_bcm)}</td>
                </tr>
                <tr>
                    <td style="background:#d9e2f3;color:#000000;border:1px solid #000;padding:7px;text-align:center;">Diesel Used</td>
                    <td style="background:#ffffff;color:#000000;border:1px solid #000;padding:7px;text-align:center;">{self._raven_num(diesel_used)}</td>
                </tr>
                <tr>
                    <td style="background:#d9e2f3;color:#000000;border:1px solid #000;padding:7px;text-align:center;">Water Used</td>
                    <td style="background:#ffffff;color:#000000;border:1px solid #000;padding:7px;text-align:center;">{self._raven_num(water_used)}</td>
                </tr>
            </table>
        </div>
        """


    def get_raven_end_of_day_report_html(self):
        docs = []
        day_rows = frappe.get_all(
            "Hourly Production",
            filters={
                "location": self.location,
                "prod_date": self.prod_date,
                "docstatus": ["<", 2],
            },
            fields=["name", "shift"],
            order_by="shift asc, hour_slot asc, creation asc",
        )

        shifts_included = []

        for row in day_rows:
            doc = frappe.get_doc("Hourly Production", row.name)
            docs.append(doc)
            if doc.shift and doc.shift not in shifts_included:
                shifts_included.append(doc.shift)

        excavator_groups = {}
        dozer_groups = {}
        material_totals = {}

        total_ts = 0
        total_dozing = 0
        diesel_used = 0
        water_used = 0

        for doc in docs:
            total_ts += doc.total_ts_bcm or 0
            total_dozing += doc.total_dozing_bcm or 0
            diesel_used += doc.diesel_used or 0
            water_used += doc.water_used or 0

            for row in doc.truck_loads or []:
                if not (row.bcms or 0):
                    continue

                excavator = row.excavator_plant_no or row.asset_name_shoval or "No Excavator"
                area = row.mining_areas_trucks or ""
                material = row.geo_mat_layer_truck or ""

                key = (excavator, area, material)

                if key not in excavator_groups:
                    excavator_groups[key] = {
                        "excavator": excavator,
                        "area": area,
                        "material": material,
                        "volume": 0,
                        "start_hours": None,
                        "end_hours": None,
                    }

                excavator_groups[key]["volume"] += row.bcms or 0

                start_hours = row.exc_start_hours or 0
                end_hours = row.exc_stop_hours or 0

                if start_hours:
                    if excavator_groups[key]["start_hours"] is None:
                        excavator_groups[key]["start_hours"] = start_hours
                    else:
                        excavator_groups[key]["start_hours"] = min(excavator_groups[key]["start_hours"], start_hours)

                if end_hours:
                    if excavator_groups[key]["end_hours"] is None:
                        excavator_groups[key]["end_hours"] = end_hours
                    else:
                        excavator_groups[key]["end_hours"] = max(excavator_groups[key]["end_hours"], end_hours)

                material_totals[material] = material_totals.get(material, 0) + (row.bcms or 0)

            for row in doc.dozer_production or []:
                if not (row.bcm_hour or 0):
                    continue

                dozer = row.dozer_plant_no or row.asset_name or "No Dozer"
                area = row.mining_areas_dozer_child or ""
                material = row.dozer_geo_mat_layer or ""

                key = (dozer, area, material)

                if key not in dozer_groups:
                    dozer_groups[key] = {
                        "dozer": dozer,
                        "area": area,
                        "material": material,
                        "volume": 0,
                    }

                dozer_groups[key]["volume"] += row.bcm_hour or 0

        excavator_rows = ""

        if excavator_groups:
            for data in excavator_groups.values():
                start = data["start_hours"] or 0
                end = data["end_hours"] or 0
                total_hours = 0

                if start and end and end >= start:
                    total_hours = end - start

                excavator_rows += f"""
                <tr>
                    <td colspan="4" style="background:#ffffff;color:#000000;border:1px solid #000;padding:5px;font-size:11px;font-weight:bold;">
                        {self._raven_e(data["excavator"])} &nbsp; | &nbsp;
                        {self._raven_e(data["area"])} &nbsp; | &nbsp;
                        {self._raven_e(data["material"])}
                    </td>
                </tr>
                <tr>
                    <td style="background:#fff2cc;color:#000000;border:1px solid #000;padding:5px;text-align:center;font-size:10px;width:34%;">
                        Volume<br>
                        <b style="font-size:11px;color:{self._raven_volume_text_color(data["volume"])};">
                            {self._raven_num(data["volume"], 0)}
                        </b>
                    </td>
                    <td style="background:#d9e2f3;color:#000000;border:1px solid #000;padding:5px;text-align:center;font-size:9px;width:22%;">
                        Start<br><b>{self._raven_num(start, 0)}</b>
                    </td>
                    <td style="background:#d9e2f3;color:#000000;border:1px solid #000;padding:5px;text-align:center;font-size:9px;width:22%;">
                        End<br><b>{self._raven_num(end, 0)}</b>
                    </td>
                    <td style="background:#d9e2f3;color:#000000;border:1px solid #000;padding:5px;text-align:center;font-size:9px;width:22%;">
                        Hours<br><b>{self._raven_num(total_hours, 0)}</b>
                    </td>
                </tr>
                """
        else:
            excavator_rows = """
            <tr>
                <td colspan="4" style="border:1px solid #000;padding:8px;text-align:center;background:#ffffff;color:#000000;font-weight:bold;">
                    No Excavator Data
                </td>
            </tr>
            """

        dozer_rows = ""

        if dozer_groups:
            for data in dozer_groups.values():
                dozer_rows += f"""
                <tr>
                    <td colspan="3" style="background:#ffffff;color:#000000;border:1px solid #000;padding:5px;font-size:11px;font-weight:bold;">
                        {self._raven_e(data["dozer"])} &nbsp; | &nbsp;
                        {self._raven_e(data["area"])} &nbsp; | &nbsp;
                        {self._raven_e(data["material"])}
                    </td>
                </tr>
                <tr>
                    <td colspan="3" style="background:#fff2cc;color:#000000;border:1px solid #000;padding:5px;text-align:center;font-size:10px;font-weight:bold;">
                        Volume:
                        <span style="color:{self._raven_volume_text_color(data["volume"])};">
                            {self._raven_num(data["volume"], 0)}
                        </span>
                    </td>
                </tr>
                """
        else:
            dozer_rows = """
            <tr>
                <td colspan="3" style="border:1px solid #000;padding:8px;text-align:center;background:#ffffff;color:#000000;font-weight:bold;">
                    No Dozer Data
                </td>
            </tr>
            """

        material_rows = ""

        if material_totals:
            for material, volume in material_totals.items():
                material_rows += f"""
                <tr>
                    <td style="background:#d9e2f3;color:#000000;border:1px solid #000;padding:7px;text-align:center;">
                        {self._raven_e(material)}
                    </td>
                    <td style="background:#ffffff;color:#000000;border:1px solid #000;padding:7px;text-align:center;font-weight:bold;">
                        {self._raven_num(volume, 0)}
                    </td>
                </tr>
                """
        else:
            material_rows = """
            <tr>
                <td colspan="2" style="background:#ffffff;color:#000000;border:1px solid #000;padding:7px;text-align:center;font-weight:bold;">
                    No TS Material
                </td>
            </tr>
            """

        total_day_bcm = total_ts + total_dozing
        shifts_text = ", ".join(shifts_included) if shifts_included else "N/A"

        return f"""
        <div style="background:#ffffff;color:#000000;font-family:Arial,sans-serif;width:100%;max-width:430px;margin:0 auto;padding:4px;box-sizing:border-box;">
            <div style="text-align:center;font-size:16px;font-weight:bold;color:#1f4e79;margin:2px 0 5px 0;">
                End of Day Production Report
            </div>

            <div style="text-align:center;font-size:11px;font-weight:bold;color:#000000;margin-bottom:6px;line-height:1.4;">
                Site: {self._raven_e(self.location)}<br>
                Date: {self._raven_e(self._raven_date())}<br>
                Report: End of Day &nbsp; | &nbsp; Shifts: {self._raven_e(shifts_text)}
            </div>

            {self.get_raven_monthly_statistics_html()}

            <table style="border-collapse:collapse;width:100%;font-size:10px;background:#ffffff;color:#000000;margin-bottom:7px;">
                <tr>
                    <th colspan="4" style="background:#1f4e79;color:#ffffff;border:1px solid #000;padding:5px;text-align:left;font-size:11px;">
                        Excavator Production Full Day
                    </th>
                </tr>
                {excavator_rows}
            </table>

            <table style="border-collapse:collapse;width:100%;font-size:10px;background:#ffffff;color:#000000;margin-bottom:7px;">
                <tr>
                    <th colspan="3" style="background:#1f4e79;color:#ffffff;border:1px solid #000;padding:5px;text-align:left;font-size:11px;">
                        Dozer Production Full Day
                    </th>
                </tr>
                {dozer_rows}
            </table>

            <table style="border-collapse:collapse;width:100%;font-size:11px;background:#ffffff;color:#000000;margin-top:5px;">
                <tr>
                    <th colspan="2" style="background:#1f4e79;color:#ffffff;border:1px solid #000;padding:5px;text-align:left;">
                        End of Day Summary
                    </th>
                </tr>
                <tr>
                    <td style="background:#d9e2f3;color:#000000;border:1px solid #000;padding:7px;text-align:center;width:72%;">Total BCM TS Day</td>
                    <td style="background:#fff2cc;color:#000000;border:1px solid #000;padding:7px;text-align:center;font-weight:bold;">{self._raven_num(total_ts, 0)}</td>
                </tr>
                <tr>
                    <td style="background:#d9e2f3;color:#000000;border:1px solid #000;padding:7px;text-align:center;">Total BCM Dozing Day</td>
                    <td style="background:#fff2cc;color:#000000;border:1px solid #000;padding:7px;text-align:center;font-weight:bold;">{self._raven_num(total_dozing, 0)}</td>
                </tr>
                <tr>
                    <td colspan="2" style="background:#d9e2f3;color:#000000;border:1px solid #000;padding:7px;font-weight:bold;">
                        TS Material Day
                    </td>
                </tr>
                {material_rows}
                <tr>
                    <td style="background:#d9e2f3;color:#000000;border:1px solid #000;padding:7px;text-align:center;">Total Day BCM</td>
                    <td style="background:#fff2cc;color:#000000;border:1px solid #000;padding:7px;text-align:center;font-weight:bold;">{self._raven_num(total_day_bcm, 0)}</td>
                </tr>
                <tr>
                    <td style="background:#d9e2f3;color:#000000;border:1px solid #000;padding:7px;text-align:center;">Diesel Used</td>
                    <td style="background:#ffffff;color:#000000;border:1px solid #000;padding:7px;text-align:center;">{self._raven_num(diesel_used, 0)}</td>
                </tr>
                <tr>
                    <td style="background:#d9e2f3;color:#000000;border:1px solid #000;padding:7px;text-align:center;">Water Used</td>
                    <td style="background:#ffffff;color:#000000;border:1px solid #000;padding:7px;text-align:center;">{self._raven_num(water_used, 0)}</td>
                </tr>
            </table>
        </div>
        """

    def refresh_raven_monthly_statistics(self):
        """
        Refresh Monthly Statistics before sending Raven report.
        Raven does not call print/form refresh methods automatically.
        """
        if not self.month_prod_planning:
            return

        if not frappe.db.exists("Monthly Production Planning", self.month_prod_planning):
            return

        try:
            monthly_doc = frappe.get_doc("Monthly Production Planning", self.month_prod_planning)

            # Try controller refresh if method exists
            try:
                if hasattr(monthly_doc, "update_mtd_production"):
                    monthly_doc.update_mtd_production()
            except Exception:
                frappe.log_error(
                    frappe.get_traceback(),
                    "Raven Monthly Statistics Controller Refresh Failed"
                )

            monthly_doc.reload()

            monthly_fields = [
                "monthly_target_bcm",
                "target_bcm_day",
                "target_bcm_hour",
                "month_actual_bcm",
                "mtd_bcm_hour",
                "mtd_bcm_day",
                "monthly_act_tally_survey_variance",
                "month_forecated_bcm",
                "month_act_ts_bcm_tallies",
                "month_act_dozing_bcm_tallies",
            ]

            for fieldname in monthly_fields:
                if hasattr(monthly_doc, fieldname):
                    self.set(fieldname, monthly_doc.get(fieldname) or 0)

        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                "Raven Monthly Statistics Refresh Failed"
            )

    @frappe.whitelist()
    def send_raven_notification(self, report_type="both"):
        try:
            channel = self.get_raven_channel_for_production()
            self.refresh_raven_monthly_statistics()
            report_type = (report_type or "both").lower()

            sent_reports = []

            if report_type in ["hour", "both"]:
                self.insert_raven_message(channel, self.get_raven_hour_report_html())
                sent_reports.append("Hour Report")

            if report_type in ["shift"]:
                self.insert_raven_message(channel, self.get_raven_shift_summary_html())
                sent_reports.append("Shift Summary")

            if report_type in ["both"]:
                self.insert_raven_message(channel, self.get_raven_end_of_day_report_html())
                sent_reports.append("End of Day Production Report")

            if not sent_reports:
                frappe.throw(_("Please select a valid Raven report type."))

            frappe.msgprint(
                _("{0} sent to Raven channel {1}.").format(
                    " and ".join(sent_reports),
                    channel
                ),
                alert=True,
                indicator="green"
            )

        except Exception as e:
            frappe.log_error(frappe.get_traceback(), "Hourly Production Raven Report Error")
            frappe.msgprint(
                _("Failed to send Raven production report: {0}").format(str(e)),
                indicator="red"
            )

    def on_update(self):
        if not self.month_prod_planning:
            return

        try:
            frappe.get_attr(
                "is_production.production.doctype.monthly_production_planning."
                "monthly_production_planning.update_mtd_production"
            )(name=self.month_prod_planning)

            frappe.msgprint(
                _("Month-to-Date Production updated automatically."),
                alert=True,
                indicator="green"
            )

        except Exception as e:
            frappe.log_error(
                message=f"Auto MTD update failed for Hourly Production {self.name}: {e}",
                title="Hourly Production Auto MTD Update"
            )


@frappe.whitelist()
def get_user_whatsapp_number(user):
    if not user:
        return None
    try:
        user_doc = frappe.get_doc("User", user)
        return user_doc.get("whatsapp_number") or user_doc.get("mobile_no")
    except frappe.DoesNotExistError:
        return None


@frappe.whitelist()
def get_previous_hour_defaults(location, prod_date, current_hour_sort_key=None, current_name=None):
    """
    Return the latest previous Hourly Production record for same site/date.
    Copies setup only: truck excavator, mining area, geo layer, mat type, shift start/end.
    Does not copy loads or BCMs.
    """
    if not location or not prod_date:
        return None

    filters = [
        ["location", "=", location],
        ["prod_date", "=", prod_date],
        ["docstatus", "<", 2],
    ]

    if current_name and not str(current_name).startswith("new-"):
        filters.append(["name", "!=", current_name])

    if current_hour_sort_key:
        try:
            filters.append(["hour_sort_key", "<", int(float(current_hour_sort_key))])
        except Exception:
            pass

    previous = frappe.get_all(
        "Hourly Production",
        filters=filters,
        fields=[
            "name",
            "shift",
            "shift_num_hour",
            "hour_slot",
            "hour_sort_key",
            "day_shift_start",
            "day_shift_end",
            "total_working_hours",
            "night_shift_start",
            "night_shift_end",
            "total_working_hour",
        ],
        order_by="hour_sort_key desc, modified desc",
        limit=1,
    )

    if not previous:
        return None

    prev_doc = frappe.get_doc("Hourly Production", previous[0].name)

    assignments = {}

    for row in prev_doc.truck_loads or []:
        if not row.asset_name_truck:
            continue

        assignments[row.asset_name_truck] = {
            "excavator": row.asset_name_shoval or None,
            "mining_area": row.mining_areas_trucks or None,
            "geo_layer": row.geo_mat_layer_truck or None,
            "mat_type": row.mat_type or None,
        }

    return {
        "previous_name": prev_doc.name,
        "previous_shift_num_hour": prev_doc.shift_num_hour,
        "previous_hour_slot": prev_doc.hour_slot,
        "day_shift_start": prev_doc.day_shift_start,
        "day_shift_end": prev_doc.day_shift_end,
        "total_working_hours": prev_doc.total_working_hours,
        "night_shift_start": prev_doc.night_shift_start,
        "night_shift_end": prev_doc.night_shift_end,
        "total_working_hour": prev_doc.total_working_hour,
        "assignments": assignments,
    }


@frappe.whitelist()
def update_hourly_references():
    threshold = add_to_date(nowdate(), days=-30)
    recs = frappe.get_all(
        'Hourly Production',
        filters={'prod_date': ['>=', threshold]},
        fields=['name', 'prod_date', 'location', 'shift', 'shift_system', 'shift_num_hour']
    )

    updated_entries = []
    for r in recs:
        pd = getdate(r.prod_date)
        plans = frappe.get_all(
            'Monthly Production Planning',
            filters=[
                ['location', '=', r.location],
                ['prod_month_start_date', '<=', pd],
                ['prod_month_end_date', '>=', pd]
            ],
            fields=['name'],
            order_by='prod_month_start_date asc',
            limit_page_length=1
        )
        if not plans:
            continue

        mpp = frappe.get_doc('Monthly Production Planning', plans[0].name)
        ref = next(
            (row.hourly_production_reference for row in mpp.month_prod_days if row.shift_start_date == pd),
            None
        )

        values = {}
        if ref:
            values['monthly_production_child_ref'] = ref
        try:
            # hour_sort_key is safe to synchronize.
            #
            # DO NOT change hour_slot here. hour_slot is part of the
            # Hourly Production document name. Changing it with
            # frappe.db.set_value() would leave the name pointing to
            # a different hour.
            _, idx_str = r.shift_num_hour.rsplit("-", 1)
            values['hour_sort_key'] = int(idx_str)
        except Exception:
            pass

        if values:
            frappe.db.set_value('Hourly Production', r.name, values)
            updated_entries.append(f"{r.name} → {values}")

    frappe.db.commit()

    if updated_entries:
        frappe.log_error(
            message=(
                f"update_hourly_references synced the following Hourly Production records\n"
                f"(prod_date ≥ {threshold}):\n\n" + "\n".join(updated_entries)
            ),
            title="update_hourly_references"
        )

    return {'updated': len(updated_entries)}

@frappe.whitelist()
def get_day_total_bcm(location, prod_date, exclude_name=None):
    if not location or not prod_date:
        return 0

    cond = ""
    params = [location, prod_date]

    if exclude_name:
        cond = " AND name != %s"
        params.append(exclude_name)

    res = frappe.db.sql(
        f"""
        SELECT COALESCE(SUM(hour_total_bcm), 0)
        FROM `tabHourly Production`
        WHERE location = %s
          AND prod_date = %s
          AND docstatus < 2
          {cond}
        """,
        params,
    )
    return res[0][0] if res else 0




@frappe.whitelist()
def get_planning_tub_factor(
    monthly_production_plan,
    item_name,
    mat_type,
):
    """Return the matching factor directly from planning."""
    if (
        not monthly_production_plan
        or not item_name
        or not mat_type
    ):
        return {}

    result = frappe.db.sql(
        """
        SELECT
            mptf.tub_factor AS tub_factor_doc_link,
            mptf.factor_value AS factor_value
        FROM `tabMonthly Production Tub Factor` mptf
        WHERE mptf.parent = %(monthly_production_plan)s
          AND mptf.parenttype = 'Monthly Production Planning'
          AND mptf.parentfield = 'tub_factors'
          AND TRIM(mptf.item_name) = TRIM(%(item_name)s)
          AND TRIM(mptf.mat_type) = TRIM(%(mat_type)s)
        ORDER BY mptf.idx ASC
        LIMIT 1
        """,
        {
            "monthly_production_plan": monthly_production_plan,
            "item_name": item_name,
            "mat_type": mat_type,
        },
        as_dict=True,
    )

    return result[0] if result else {}


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_mpp_tub_factor_options(
    doctype,
    txt,
    searchfield,
    start,
    page_len,
    filters,
):
    """
    Return only submitted Tub Factors approved on the selected
    Monthly Production Planning document for the matching truck model
    and material type.
    """
    if isinstance(filters, str):
        filters = frappe.parse_json(filters)

    filters = frappe._dict(filters or {})

    monthly_production_plan = filters.get(
        "monthly_production_plan"
    )
    item_name = filters.get("item_name")
    mat_type = filters.get("mat_type")

    if not monthly_production_plan or not item_name or not mat_type:
        return []

    return frappe.db.sql(
        """
        SELECT
            tf.name,
            CONCAT(
                tf.item_name,
                ' | ',
                tf.mat_type,
                ' | Factor ',
                tf.tub_factor
            ) AS description
        FROM `tabMonthly Production Tub Factor` mptf
        INNER JOIN `tabTub Factor` tf
            ON tf.name = mptf.tub_factor
        WHERE mptf.parent = %(monthly_production_plan)s
          AND mptf.parenttype = 'Monthly Production Planning'
          AND mptf.parentfield = 'tub_factors'
          AND tf.docstatus = 1
          AND tf.item_name = %(item_name)s
          AND tf.mat_type = %(mat_type)s
          AND (
                tf.name LIKE %(txt)s
                OR tf.item_name LIKE %(txt)s
                OR tf.mat_type LIKE %(txt)s
              )
        ORDER BY tf.tub_factor ASC, tf.name ASC
        LIMIT %(start)s, %(page_len)s
        """,
        {
            "monthly_production_plan": monthly_production_plan,
            "item_name": item_name,
            "mat_type": mat_type,
            "txt": f"%{txt or ''}%",
            "start": int(start or 0),
            "page_len": int(page_len or 20),
        },
    )