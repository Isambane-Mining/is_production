# apps/is_production/is_production/production/doctype/monthly_production_planning/monthly_production_planning.py

# Copyright (c) 2025, Isambane Mining (Pty) Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
import datetime
from frappe.exceptions import TimestampMismatchError
from frappe.utils import add_days, flt, get_datetime, getdate


# MPP_MACHINE_ALLOCATION_SERVER_ROLE_CONTROL
MPP_MACHINE_ALLOCATION_ROLES = {
    "Production Area Manager",
    "Engineering Area Manager",
    "Information Officer",
}


class MonthlyProductionPlanning(Document):
    def before_insert(self):
        """
        Copy approved Tub Factors from the latest previous Monthly
        Production Plan for the same Location.

        Existing manually selected Tub Factors are never overwritten.
        """
        self.copy_tub_factors_from_previous_plan()
        self.copy_machine_allocation_from_previous_plan()

    def validate(self):
        """
        Validate document before saving.
        Only sync month_prod_days when production date/shift setup changed.
        """
        self.validate_shift_hours()
        self.validate_machine_allocation_access()

        # Production Month End Invoicing must follow the captured Production Month End Date.
        # Each site can have its own captured month-end date.
        if getattr(self, "prod_month_end_date", None):
            self.prod_month_end = self.prod_month_end_date

        if self.should_sync_month_prod_days():
            self.sync_month_prod_days()

    def copy_tub_factors_from_previous_plan(self):
        """
        Copy Tub Factors from the latest previous Monthly Production
        Planning document for the same Location.

        This only runs for a new document and only when the new document's
        Tub Factors table is empty.
        """
        if not self.is_new():
            return

        if not self.location:
            return

        if self.get("tub_factors"):
            return

        previous_plan = frappe.db.sql(
            """
            SELECT mpp.name
            FROM `tabMonthly Production Planning` mpp
            WHERE mpp.location = %(location)s
              AND mpp.docstatus < 2
              AND EXISTS (
                    SELECT 1
                    FROM `tabMonthly Production Tub Factor` mptf
                    WHERE mptf.parent = mpp.name
                      AND mptf.parenttype =
                          'Monthly Production Planning'
                      AND mptf.parentfield = 'tub_factors'
                      AND IFNULL(mptf.tub_factor, '') != ''
              )
            ORDER BY
                mpp.prod_month_end_date DESC,
                mpp.creation DESC
            LIMIT 1
            """,
            {
                "location": self.location,
            },
            as_dict=True,
        )

        if not previous_plan:
            return

        previous_doc = frappe.get_doc(
            "Monthly Production Planning",
            previous_plan[0].name,
        )

        copied_names = set()

        for previous_row in previous_doc.get("tub_factors") or []:
            tub_factor_name = previous_row.tub_factor

            if not tub_factor_name:
                continue

            if tub_factor_name in copied_names:
                continue

            if not frappe.db.exists(
                "Tub Factor",
                {
                    "name": tub_factor_name,
                    "docstatus": 1,
                },
            ):
                continue

            self.append(
                "tub_factors",
                {
                    "tub_factor": tub_factor_name,
                    "item_name": previous_row.item_name,
                    "mat_type": previous_row.mat_type,
                    "factor_value": previous_row.factor_value,
                },
            )

            copied_names.add(tub_factor_name)

        
    # MPP_MACHINE_ALLOCATION_CARRY_FORWARD
    def _get_previous_machine_plan(self):
        """
        Get the latest previous Monthly Production Planning document
        for the same location that contains Excavator/ADT or Dozer
        planning.

        Where the new production start date is available, only plans
        ending before that start date are considered.
        """
        if not self.location:
            return None

        params = {
            "location": self.location,
        }

        date_condition = ""

        if self.prod_month_start_date:
            params["start_date"] = getdate(
                self.prod_month_start_date
            )

            date_condition = """
                AND COALESCE(
                    mpp.prod_month_end_date,
                    mpp.prod_month_start_date
                ) < %(start_date)s
            """

        previous = frappe.db.sql(
            f"""
            SELECT mpp.name
            FROM `tabMonthly Production Planning` mpp
            WHERE mpp.location = %(location)s
              AND mpp.docstatus < 2
              {date_condition}
              AND (
                    EXISTS (
                        SELECT 1
                        FROM `tabExcavator Truck Link` etl
                        WHERE etl.parent = mpp.name
                          AND etl.parenttype =
                              'Monthly Production Planning'
                          AND etl.parentfield =
                              'excavator_truck_assignments'
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM `tabDozers Planned` dz
                        WHERE dz.parent = mpp.name
                          AND dz.parenttype =
                              'Monthly Production Planning'
                          AND dz.parentfield =
                              'dozer_table'
                    )
              )
            ORDER BY
                COALESCE(
                    mpp.prod_month_end_date,
                    mpp.prod_month_start_date
                ) DESC,
                mpp.creation DESC
            LIMIT 1
            """,
            params,
            as_dict=True,
        )

        if not previous:
            return None

        return frappe.get_doc(
            "Monthly Production Planning",
            previous[0].name,
        )


    def copy_machine_allocation_from_previous_plan(self):
        """
        New monthly plans inherit the previous month's complete
        production-machine setup for the same location.

        This includes:
        - Assigned Excavators
        - Spare/Swing Excavators
        - Assigned ADTs
        - Spare/Swing ADTs
        - Excavator/ADT sort order
        - Assigned Dozers
        - Spare/Swing Dozers
        - Dozing Type
        - Production machine counts

        Existing rows on the new document are never overwritten.
        """
        if not self.is_new():
            return

        if not self.location:
            return

        previous = self._get_previous_machine_plan()

        if not previous:
            return

        copied_excavator_truck = False
        copied_dozers = False

        # ------------------------------------------------------
        # Excavators / ADTs
        # ------------------------------------------------------
        if not self.get("excavator_truck_assignments"):
            for row in (
                previous.get("excavator_truck_assignments")
                or []
            ):
                self.append(
                    "excavator_truck_assignments",
                    {
                        "excavator": row.excavator,
                        "excavator_model":
                            row.excavator_model,
                        "truck": row.truck,
                        "truck_model":
                            row.truck_model,
                        "sort_order":
                            row.sort_order,
                    },
                )

            copied_excavator_truck = True

        # ------------------------------------------------------
        # Dozers
        # ------------------------------------------------------
        if not self.get("dozer_table"):
            for row in previous.get("dozer_table") or []:
                self.append(
                    "dozer_table",
                    {
                        "asset_name": row.asset_name,
                        "item_name": row.item_name,
                        "dozing_type": row.dozing_type,
                    },
                )

            copied_dozers = True

        # ------------------------------------------------------
        # Keep the production counts consistent with the
        # inherited machine arrangement.
        # ------------------------------------------------------
        if copied_excavator_truck:
            self.num_excavators = (
                previous.num_excavators or 0
            )
            self.num_trucks = (
                previous.num_trucks or 0
            )

        if copied_dozers:
            self.num_dozers = (
                previous.num_dozers or 0
            )


    def validate_machine_allocation_access(self):
        """
        Only the approved machine-allocation roles may change:

        - Excavator / ADT assignment
        - Spare/Swing Excavators
        - Spare/Swing ADTs
        - Excavator / ADT ordering
        - Dozer assignment
        - Spare/Swing Dozers
        - Dozing Type

        Other users may still save unrelated Monthly Production
        Planning fields if their normal Frappe permissions allow it.
        """
        user = frappe.session.user
        roles = set(frappe.get_roles(user) or [])

        if roles.intersection(MPP_MACHINE_ALLOCATION_ROLES):
            return

        current_excavators = self._excavator_truck_snapshot(self)
        current_dozers = self._dozer_snapshot(self)
        current_counts = self._machine_count_snapshot(self)

        # ----------------------------------------------------------
        # New document:
        # unauthorized users must not create machine allocations.
        # ----------------------------------------------------------
        if self.is_new():
            previous = self._get_previous_machine_plan()

            if previous:
                previous_excavators = (
                    self._excavator_truck_snapshot(
                        previous
                    )
                )
                previous_dozers = self._dozer_snapshot(
                    previous
                )
                previous_counts = (
                    self._machine_count_snapshot(
                        previous
                    )
                )

                if (
                    current_excavators
                    != previous_excavators
                    or current_dozers
                    != previous_dozers
                    or current_counts
                    != previous_counts
                ):
                    self._throw_machine_allocation_permission_error()

                return

            # First-ever plan for a location:
            # non-manager may create it only without machine
            # allocation. A manager must establish the initial
            # machine arrangement.
            if (
                current_excavators
                or current_dozers
                or any(current_counts)
            ):
                self._throw_machine_allocation_permission_error()

            return

        # ----------------------------------------------------------
        # Existing document:
        # compare submitted values against the saved database state.
        # ----------------------------------------------------------
        before = self.get_doc_before_save()

        if not before and self.name and frappe.db.exists(
            "Monthly Production Planning",
            self.name,
        ):
            before = frappe.get_doc(
                "Monthly Production Planning",
                self.name,
            )

        if not before:
            return

        previous_excavators = self._excavator_truck_snapshot(before)
        previous_dozers = self._dozer_snapshot(before)
        previous_counts = self._machine_count_snapshot(before)

        excavator_changed = current_excavators != previous_excavators
        dozer_changed = current_dozers != previous_dozers
        counts_changed = current_counts != previous_counts

        if excavator_changed or dozer_changed or counts_changed:
            self._throw_machine_allocation_permission_error()

    @staticmethod
    def _excavator_truck_snapshot(doc):
        """
        Return a stable representation of Excavator / ADT allocation.

        Child row names and idx values are intentionally ignored.
        sort_order IS included because changing machine order is also
        part of the restricted machine-allocation interface.
        """
        rows = []

        for row in doc.get("excavator_truck_assignments") or []:
            rows.append(
                (
                    row.excavator or "",
                    row.excavator_model or "",
                    row.truck or "",
                    row.truck_model or "",
                    int(row.sort_order or 0),
                )
            )

        return sorted(rows)

    @staticmethod
    def _dozer_snapshot(doc):
        """
        Return a stable representation of Dozer allocation.

        dozing_type is included because Assigned versus Spare/Swing
        status is controlled through the Dozer planning interface.
        """
        rows = []

        for row in doc.get("dozer_table") or []:
            rows.append(
                (
                    row.asset_name or "",
                    row.item_name or "",
                    row.dozing_type or "",
                )
            )

        return sorted(rows)

    @staticmethod
    def _machine_count_snapshot(doc):
        """
        Protect the persisted production-machine counts calculated
        from the Excavator / ADT / Dozer allocation interface.
        """
        return (
            int(doc.get("num_excavators") or 0),
            int(doc.get("num_trucks") or 0),
            int(doc.get("num_dozers") or 0),
        )

    @staticmethod
    def _throw_machine_allocation_permission_error():
        frappe.throw(
            (
                "You can view the planned Excavators, ADTs and Dozers, "
                "but you are not permitted to change their allocation.<br><br>"
                "Only users with one of these roles may add, remove, "
                "assign or move machines:<br>"
                "<b>Production Area Manager</b><br>"
                "<b>Engineering Area Manager</b><br>"
                "<b>Information Officer</b>"
            ),
            title="Machine Allocation Permission Required",
            exc=frappe.PermissionError,
        )


    def validate_shift_hours(self):
        """
        Validate that shift hours don't exceed 12
        """
        if self.weekday_shift_hours and self.weekday_shift_hours > 12:
            frappe.throw(
                "Weekday Shift Hours cannot be more than 12",
                title="Invalid Shift Hours"
            )

        if self.saturday_shift_hours and self.saturday_shift_hours > 12:
            frappe.throw(
                "Saturday Shift Hours cannot be more than 12",
                title="Invalid Shift Hours"
            )


    def should_sync_month_prod_days(self):
        """
        Only run month_prod_days sync when setup fields changed.
        Avoid heavy child-table rebuild on every normal save.
        """
        if self.is_new():
            return True

        before = self.get_doc_before_save()

        if not before:
            return True

        sync_fields = [
            "prod_month_start_date",
            "prod_month_end_date",
            "shift_system",
            "weekday_shift_hours",
            "saturday_shift_hours",
            "num_sat_shifts",
            "num_excavators",
        ]

        return any(self.has_value_changed(fieldname) for fieldname in sync_fields)


    def sync_month_prod_days(self):
        """
        Ensure month_prod_days matches the configured production date range.

        This keeps existing row data for dates already present, creates any
        missing rows when the plan range is extended, and removes rows that
        fall outside the configured range.
        """
        if not self.prod_month_start_date or not self.prod_month_end_date:
            return

        start_date = getdate(self.prod_month_start_date)
        end_date = getdate(self.prod_month_end_date)

        if not start_date or not end_date or start_date > end_date:
            return

        existing_by_date = {}
        for row in self.month_prod_days or []:
            if row.shift_start_date:
                existing_by_date[getdate(row.shift_start_date)] = row.as_dict()

        rows = []
        totals = {"day": 0, "night": 0, "morning": 0, "afternoon": 0}
        shift_date = start_date

        while shift_date <= end_date:
            hours = self.get_shift_hours_for_date(shift_date)
            row_data = existing_by_date.get(shift_date, {})

            production_excavators = row_data.get("production_excavators")
            if production_excavators in (None, ""):
                production_excavators = self.num_excavators or 0

            total_shift_hours = sum(hours.values())
            bcm_per_day = (flt(production_excavators) if production_excavators else 0) * total_shift_hours * 220

            row_data.update({
                "doctype": "Monthly Production Days",
                "shift_start_date": shift_date,
                "day_week": shift_date.strftime("%A"),
                "shift_day_hours": hours["day"],
                "shift_night_hours": hours["night"],
                "shift_morning_hours": hours["morning"],
                "shift_afternoon_hours": hours["afternoon"],
                "production_excavators": production_excavators,
                "bcm_per_day": bcm_per_day,
            })

            if self.name:
                row_data["hourly_production_reference"] = f"{self.name}-{shift_date}"

            rows.append(row_data)

            totals["day"] += hours["day"]
            totals["night"] += hours["night"]
            totals["morning"] += hours["morning"]
            totals["afternoon"] += hours["afternoon"]
            shift_date = add_days(shift_date, 1)

        self.set("month_prod_days", [])
        for row_data in rows:
            self.append("month_prod_days", row_data)

        total_hours = totals["day"] + totals["night"] + totals["morning"] + totals["afternoon"]
        self.tot_shift_day_hours = totals["day"]
        self.tot_shift_night_hours = totals["night"]
        self.tot_shift_morning_hours = totals["morning"]
        self.tot_shift_afternoon_hours = totals["afternoon"]
        self.total_month_prod_hours = total_hours
        full_day_hours = self.get_full_production_day_hours()
        self.num_prod_days = (total_hours / full_day_hours) if full_day_hours else 0

        if self.monthly_target_bcm:
            self.target_bcm_day = self.monthly_target_bcm / self.num_prod_days if self.num_prod_days else 0
            self.target_bcm_hour = self.monthly_target_bcm / total_hours if total_hours else 0

    def get_shift_hours_for_date(self, shift_date):
        weekday_shift_hours = self.weekday_shift_hours or 0
        saturday_shift_hours = self.saturday_shift_hours or 0
        num_sat_shifts = int(self.num_sat_shifts or 0)
        day_name = shift_date.strftime("%A")

        hours = {"day": 0, "night": 0, "morning": 0, "afternoon": 0}

        if self.shift_system == "2x12Hour":
            if day_name == "Saturday":
                hours["day"] = saturday_shift_hours
                hours["night"] = saturday_shift_hours if num_sat_shifts > 1 else 0
            elif day_name != "Sunday":
                hours["day"] = weekday_shift_hours
                hours["night"] = weekday_shift_hours
        else:
            if day_name == "Saturday":
                hours["morning"] = saturday_shift_hours
                hours["afternoon"] = saturday_shift_hours if num_sat_shifts > 1 else 0
                hours["night"] = saturday_shift_hours if num_sat_shifts > 2 else 0
            elif day_name != "Sunday":
                hours["morning"] = weekday_shift_hours
                hours["afternoon"] = weekday_shift_hours
                hours["night"] = weekday_shift_hours

        return hours
    

    def get_full_production_day_hours(self):
        """
        Convert configured shift working hours to one normal production day.

        2x12Hour example:
            weekday shift hours = 9
            full production day = 9 + 9 = 18 hours

        Therefore:
            18 hours = 1.000 day
             6 hours = 0.333 day
            14 hours = 0.778 day
        """
        per_shift_hours = flt(self.weekday_shift_hours)

        if self.shift_system == "3x8Hour":
            full_day_hours = per_shift_hours * 3
            return full_day_hours if full_day_hours > 0 else 24.0

        full_day_hours = per_shift_hours * 2
        return full_day_hours if full_day_hours > 0 else 18.0


    def update_mtd_production(self):
        """
        Server-side Month-to-Date update: aggregates Hourly Production and Survey data
        and updates child table (month_prod_days) and parent fields accordingly.
        """
        # 1. Gather references from child table
        refs = [row.hourly_production_reference for row in self.month_prod_days if row.hourly_production_reference]
        
        # 2. Fetch Hourly Production aggregates
        hp_records = frappe.get_all(
            'Hourly Production',
            filters=[
                ['month_prod_planning', '=', self.name],
                ['monthly_production_child_ref', 'in', refs]
            ],
            fields=['monthly_production_child_ref', 'total_ts_bcm', 'total_dozing_bcm']
        )

        hp_map = {}
        for rec in hp_records:
            ref = rec['monthly_production_child_ref']
            ts = rec.get('total_ts_bcm') or 0
            dz = rec.get('total_dozing_bcm') or 0
            hp_map.setdefault(ref, {'ts': 0, 'dz': 0})
            hp_map[ref]['ts'] += ts
            hp_map[ref]['dz'] += dz

        # 3. Fetch Survey data (for TS / dozing variances)
        srv_records = frappe.get_all(
            'Survey',
            filters=[
                ['hourly_prod_ref', 'in', refs],
                ['docstatus', '=', 1]
            ],
            fields=['hourly_prod_ref', 'total_ts_bcm', 'total_dozing_bcm']
        )

        survey_map = {}
        for rec in srv_records:
            ref = rec['hourly_prod_ref']
            survey_map[ref] = {
                'ts': rec.get('total_ts_bcm') or 0,
                'dz': rec.get('total_dozing_bcm') or 0
            }

        # 4. Identify latest survey reference
        survey_rows = [row for row in self.month_prod_days if row.hourly_production_reference in survey_map]
        last_ref = None
        if survey_rows:
            last_row = max(survey_rows, key=lambda r: r.shift_start_date)
            last_ref = last_row.hourly_production_reference

        # ─────────────────────────────────────────────
        # MONTH ACTUAL COAL (MATCHES PRODUCTION SUMMARY)
        # ─────────────────────────────────────────────

        COAL_CONVERSION = 1.5
        month_actual_coal = 0

        start_dt = get_datetime(self.prod_month_start_date)
        end_dt   = get_datetime(f"{self.prod_month_end_date} 23:59:59")

        # 1️⃣ Get latest survey ≤ month end
        survey = frappe.get_all(
            "Survey",
            filters={
                "location": self.location,
                "last_production_shift_start_date": ["<=", end_dt],
                "docstatus": 1
            },
            fields=[
                "last_production_shift_start_date",
                "total_surveyed_coal_tons"
            ],
            order_by="last_production_shift_start_date desc",
            limit_page_length=1
        )

        if survey:
            survey_date = survey[0]["last_production_shift_start_date"]
            if isinstance(survey_date, datetime.datetime):
                survey_date = survey_date.date()


            # Ensure survey is inside the month
            if self.prod_month_start_date <= survey_date <= self.prod_month_end_date:
                month_actual_coal = survey[0]["total_surveyed_coal_tons"] or 0

                coal_after = frappe.db.sql("""
                    SELECT COALESCE(SUM(tl.bcms),0)
                    FROM `tabHourly Production` hp
                    JOIN `tabTruck Loads` tl ON tl.parent = hp.name
                    WHERE hp.location = %s
                      AND hp.prod_date > %s
                      AND hp.prod_date <= %s
                      AND LOWER(tl.mat_type) LIKE '%%coal%%'
                """, (self.location, survey_date, end_dt))[0][0]

                month_actual_coal += (coal_after or 0) * COAL_CONVERSION
            else:
                survey = None

        # 2️⃣ No valid survey → use all HP coal for month
        if not survey:
            coal_bcm = frappe.db.sql("""
                SELECT COALESCE(SUM(tl.bcms),0)
                FROM `tabHourly Production` hp
                JOIN `tabTruck Loads` tl ON tl.parent = hp.name
                WHERE hp.location = %s
                  AND hp.prod_date BETWEEN %s AND %s
                  AND LOWER(tl.mat_type) LIKE '%%coal%%'
            """, (self.location, start_dt, end_dt))[0][0]

            month_actual_coal = (coal_bcm or 0) * COAL_CONVERSION

        self.month_actual_coal = month_actual_coal

        # 5. Update child table rows
        cum_ts = 0
        cum_dz = 0
        for row in sorted(self.month_prod_days, key=lambda r: r.shift_start_date):
            ref = row.hourly_production_reference
            hp = hp_map.get(ref, {'ts': 0, 'dz': 0})

            row.total_ts_bcms = hp['ts']
            row.total_dozing_bcms = hp['dz']

            cum_ts += row.total_ts_bcms or 0
            cum_dz += row.total_dozing_bcms or 0

            row.cum_ts_bcms = cum_ts
            row.tot_cumulative_dozing_bcms = cum_dz

            sv = survey_map.get(ref, {'ts': 0, 'dz': 0})
            if ref == last_ref:
                row.tot_cum_ts_survey = sv['ts']
                row.tot_cum_dozing_survey = sv['dz']
                row.cum_ts_variance = sv['ts'] - cum_ts
                row.cum_dozing_variance = sv['dz'] - cum_dz
            else:
                row.tot_cum_ts_survey = 0
                row.tot_cum_dozing_survey = 0
                row.cum_ts_variance = 0
                row.cum_dozing_variance = 0

        # 6. Parent-level totals and MTD summary
        total_ts = sum(row.total_ts_bcms or 0 for row in self.month_prod_days)
        total_dz = sum(row.total_dozing_bcms or 0 for row in self.month_prod_days)

        survey_var = 0
        if last_ref:
            base_row = next((r for r in self.month_prod_days if r.hourly_production_reference == last_ref), None)
            if base_row:
                sv = survey_map.get(last_ref, {'ts': 0, 'dz': 0})
                survey_var = (sv['dz'] - base_row.tot_cumulative_dozing_bcms) + (sv['ts'] - base_row.cum_ts_bcms)

        today = datetime.date.today()
        yesterday = today - datetime.timedelta(days=1)

        done_days = 0
        done_hours = 0
        for r in self.month_prod_days:
            if r.shift_start_date and r.shift_start_date <= yesterday:
                hrs = (
                    (r.shift_day_hours or 0) +
                    (r.shift_night_hours or 0) +
                    (r.shift_morning_hours or 0) +
                    (r.shift_afternoon_hours or 0)
                )
                if hrs:
                    done_days += 1
                    done_hours += hrs

        actual = total_ts + total_dz + survey_var
        full_day_hours = self.get_full_production_day_hours()

        equivalent_total_days = (
            (self.total_month_prod_hours or 0) / full_day_hours
            if full_day_hours else 0
        )
        equivalent_done_days = (
            done_hours / full_day_hours
            if full_day_hours else 0
        )

        self.num_prod_days = equivalent_total_days

        mtd_day = actual / equivalent_done_days if equivalent_done_days else 0
        mtd_hour = actual / done_hours if done_hours else 0
        forecast = mtd_hour * (self.total_month_prod_hours or 0)

        self.month_act_ts_bcm_tallies = total_ts
        self.month_act_dozing_bcm_tallies = total_dz
        self.monthly_act_tally_survey_variance = survey_var
        self.month_actual_bcm = actual

        if self.month_actual_coal:
            try:
                self.split_ratio = (
                    self.month_actual_bcm
                    - (self.month_actual_coal / 1.5)
                ) / self.month_actual_coal
            except ZeroDivisionError:
                self.split_ratio = 0
        else:
            self.split_ratio = 0

        self.prod_days_completed = equivalent_done_days
        self.month_prod_hours_completed = done_hours
        self.month_remaining_production_days = (max((self.total_month_prod_hours or 0) - done_hours, 0) / full_day_hours) if full_day_hours else 0
        self.month_remaining_prod_hours = (self.total_month_prod_hours or 0) - done_hours
        self.mtd_bcm_day = mtd_day
        self.mtd_bcm_hour = mtd_hour
        self.month_forecated_bcm = forecast

        if self.num_prod_days:
            self.target_bcm_day = self.monthly_target_bcm / self.num_prod_days
        if self.total_month_prod_hours:
            self.target_bcm_hour = self.monthly_target_bcm / self.total_month_prod_hours

        try:
            self.save()
        except TimestampMismatchError as e:
            frappe.log_error(
                message=f"TimestampMismatchError in update_mtd_production for {self.name}: {e}",
                title="update_mtd_production"
            )


def update_all_active_mpp_mtd():
    today = getdate()

    mpps = frappe.get_all(
        "Monthly Production Planning",
        filters={
            "prod_month_start_date": ["<=", today],
            "prod_month_end_date": [">=", today],
            "docstatus": ["<", 2],
        },
        pluck="name",
    )

    for name in mpps:
        try:
            refresh_mtd_production(name)
            frappe.db.commit()
        except Exception:
            frappe.db.rollback()
            frappe.log_error(
                frappe.get_traceback(),
                f"Hourly MPP MTD Update - {name}",
            )


def refresh_mtd_production(name):
    """
    Recalculate and save MTD on one MPP, holding a row lock on it.

    for_update=True takes SELECT ... FOR UPDATE on the parent row, so concurrent
    writers (scheduler, Hourly Production saves, Raven reports, the MPP button)
    queue behind each other instead of failing with MariaDB 1020
    "Record has changed since last read".
    """
    doc = frappe.get_doc("Monthly Production Planning", name, for_update=True)
    doc.update_mtd_production()
    return doc


def enqueue_mtd_refresh(name):
    """Queue one MTD refresh per MPP; saves that land close together collapse into a single job."""
    frappe.enqueue(
        "is_production.production.doctype.monthly_production_planning."
        "monthly_production_planning.refresh_mtd_production",
        queue="short",
        name=name,
        job_id=f"mpp_mtd::{frappe.local.site}::{name}",
        deduplicate=True,
        enqueue_after_commit=True,
    )


@frappe.whitelist()
def update_mtd_production(name):
    """
    RPC wrapper so Hourly Production can trigger the MPP MTD update.
    """
    try:
        refresh_mtd_production(name)
        return {"status": "success", "name": name}
    except Exception as e:
        frappe.log_error(
            message=f"Error in RPC update_mtd_production for {name}: {e}",
            title="update_mtd_production RPC"
        )
        return {"status": "error", "message": str(e)}

@frappe.whitelist()
def dashboard_monthly_production_query(doctype, txt, searchfield, start, page_len, filters):
    """
    Monthly Production dropdown for Production Dashboard.
    Display only Production Month End Invoicing date, while selecting the real Monthly Production Planning name.
    """
    location = None

    if filters:
        location = filters.get("location") or filters.get("site")

    conditions = []
    values = {
        "txt": f"%{txt or ''}%",
        "start": start,
        "page_len": page_len,
    }

    if location:
        conditions.append("location = %(location)s")
        values["location"] = location

    if txt:
        conditions.append("""
            (
                name LIKE %(txt)s
                OR location LIKE %(txt)s
                OR DATE_FORMAT(prod_month_end, '%%d-%%m-%%Y') LIKE %(txt)s
                OR DATE_FORMAT(prod_month_end_date, '%%d-%%m-%%Y') LIKE %(txt)s
            )
        """)

    where_clause = " AND ".join(conditions)
    if where_clause:
        where_clause = "WHERE " + where_clause

    return frappe.db.sql(f"""
        SELECT
            name,
            DATE_FORMAT(prod_month_end, '%%d-%%m-%%Y') AS description
        FROM `tabMonthly Production Planning`
        {where_clause}
        ORDER BY prod_month_end DESC, name DESC
        LIMIT %(start)s, %(page_len)s
    """, values)



@frappe.whitelist()
def get_previous_tub_factor_rows(location):
    """Return complete approved Tub Factor rows from the latest plan at a site."""

    location = str(location or "").strip()

    if not location:
        return []

    previous_plan = frappe.db.sql(
        """
        SELECT mpp.name
        FROM `tabMonthly Production Planning` mpp
        WHERE mpp.location = %(location)s
          AND mpp.docstatus < 2
          AND EXISTS (
                SELECT 1
                FROM `tabMonthly Production Tub Factor` mptf
                WHERE mptf.parent = mpp.name
                  AND mptf.parenttype = 'Monthly Production Planning'
                  AND mptf.parentfield = 'tub_factors'
                  AND IFNULL(mptf.tub_factor, '') != ''
          )
        ORDER BY
            mpp.prod_month_end_date DESC,
            mpp.creation DESC
        LIMIT 1
        """,
        {"location": location},
        as_dict=True,
    )

    if not previous_plan:
        return []

    previous_doc = frappe.get_doc(
        "Monthly Production Planning",
        previous_plan[0].name,
    )

    output = []
    copied_names = set()

    for previous_row in previous_doc.get("tub_factors") or []:
        tub_factor_name = previous_row.tub_factor

        if (
            not tub_factor_name
            or tub_factor_name in copied_names
        ):
            continue

        factor = frappe.db.get_value(
            "Tub Factor",
            {
                "name": tub_factor_name,
                "docstatus": 1,
            },
            [
                "name",
                "item_name",
                "mat_type",
                "tub_factor",
            ],
            as_dict=True,
        )

        if not factor:
            continue

        output.append({
            "tub_factor": factor.name,
            "item_name": factor.item_name,
            "mat_type": factor.mat_type,
            "factor_value": factor.tub_factor,
        })

        copied_names.add(tub_factor_name)

    return output
