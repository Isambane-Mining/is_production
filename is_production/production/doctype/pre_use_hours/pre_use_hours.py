# Copyright (c) 2026, BuFf0k and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from datetime import datetime, timedelta, date
from frappe.utils import flt
from frappe.utils.data import getdate


class PreUseHours(Document):
    def before_validate(self):
        """
        Ensure child row `asset_name` (Link -> Asset) stores Asset.name.
        If any existing rows contain the Plant No / code (Asset.asset_name),
        convert them to Asset.name BEFORE Frappe link validation runs.
        """
        self._normalize_asset_links()

    def validate(self):
        """
        HARD server-side machine-hour validation.

        Normal users may edit existing Pre-Use Hours records,
        but they may NOT save machine hours outside the permitted
        shift maximum.

        Exemptions:
            - Information Officer
            - Plot 22

        Example:
            Previous Start = 138
            Current Start  = 157
            Difference     = 19 hours
            Maximum        = 12 hours

            Normal Production User -> BLOCK SAVE
        """

        if self.shift_date:
            self.shift_date = getdate(self.shift_date)

        # Plot 22 keeps its requested exemption.
        if self.location == "Plot 22":
            return

        session_user = frappe.session.user

        # Read directly from the database rather than relying on
        # browser role cache.
        is_information_officer = bool(
            frappe.db.exists(
                "Has Role",
                {
                    "parent": session_user,
                    "parenttype": "User",
                    "role": "Information Officer",
                },
            )
        )

        # Information Officer keeps the requested exemption.
        if is_information_officer:
            return

        # =====================================================
        # HARD RULE FOR ALL OTHER USERS
        # =====================================================
        #
        # This runs during Frappe's validate lifecycle on EVERY
        # normal document save, including edits/re-saves.
        #
        self.validate_previous_shift_hours()
        self.validate_current_shift_hours()


    def _normalize_asset_links(self):
        rows = self.get("pre_use_assets") or []
        values = sorted({r.asset_name for r in rows if getattr(r, "asset_name", None)})
        if not values:
            return

        # Values that already exist as Asset.name
        existing_names = set(
            frappe.get_all("Asset", filters={"name": ["in", values]}, pluck="name")
        )

        # Anything not a valid Asset.name: try match against Asset.asset_name (Plant No.)
        unknown = [v for v in values if v not in existing_names]
        if not unknown:
            return

        matches = frappe.get_all(
            "Asset",
            filters={"asset_name": ["in", unknown]},
            fields=["name", "asset_name"],
        )
        code_to_name = {m.asset_name: m.name for m in matches if m.get("asset_name")}

        if not code_to_name:
            return

        for r in rows:
            v = getattr(r, "asset_name", None)
            if v and v in code_to_name:
                r.asset_name = code_to_name[v]

    def before_save(self):
        try:
            self.shift_date = getdate(self.shift_date)

            monthly_plan = get_monthly_production_plan(
                self.location,
                self.shift_date,
            )

            if not monthly_plan:
                frappe.throw(
                    "No Monthly Production Planning data found "
                    "for the selected location and shift date."
                )

            validate_shift_date(self, monthly_plan)

            # =====================================================
            # DETERMINE HARD-VALIDATION EXEMPTIONS
            # =====================================================
            #
            # Read Information Officer directly from Has Role.
            # This avoids an old/cached browser role being used to
            # bypass the machine-hour validation.
            #
            session_user = frappe.session.user

            is_information_officer = bool(
                frappe.db.exists(
                    "Has Role",
                    {
                        "parent": session_user,
                        "parenttype": "User",
                        "role": "Information Officer",
                    },
                )
            )

            is_plot_22 = self.location == "Plot 22"

            # =====================================================
            # NORMAL USERS - HARD MACHINE-HOUR VALIDATION
            # =====================================================
            #
            # Normal users MUST pass this before the save can
            # continue.
            #
            # Example:
            #   Previous Start = 138
            #   Current Start  = 157
            #   Worked         = 19
            #   Maximum        = 12
            #
            # Result:
            #   SAVE BLOCKED
            #
            if not is_information_officer and not is_plot_22:

                # Strict chronological sequence is only required
                # for a brand-new capture.
                if self.is_new():
                    check_previous_record_sequence(
                        self,
                        monthly_plan,
                    )

                # ALWAYS validate machine hours for normal users,
                # including edits/re-saves of existing records.
                self.validate_previous_shift_hours()
                self.validate_current_shift_hours()

            # =====================================================
            # PLOT 22 EXEMPTION
            # =====================================================
            if is_plot_22:
                self.data_integ_indicator = "🟢"
                self.data_integrity_summary = (
                    "<p><b>✅ Plot 22 excluded from "
                    "integrity checks.</b></p>"
                )

                # Maintain engine-hour linkage even though Plot 22
                # bypasses the hard maximum-hour validation.
                self.update_previous_eng_hrs_end()
                return

            # =====================================================
            # INFORMATION OFFICER EXEMPTION
            # =====================================================
            if is_information_officer:
                self.data_integ_indicator = "🟢"
                self.data_integrity_summary = (
                    "<p><b>✅ Information Officer excluded from "
                    "integrity checks.</b></p>"
                )

                # Maintain engine-hour linkage even though the
                # Information Officer bypasses hard validation.
                self.update_previous_eng_hrs_end()
                return

            # =====================================================
            # NORMAL USER - CONTINUE SAVE
            # =====================================================
            self.evaluate_data_integrity()
            self.update_previous_eng_hrs_end()

        except Exception:
            frappe.log_error(
                message=frappe.get_traceback(),
                title="Pre-Use Hours Validation Error",
            )
            raise

    def validate_current_shift_hours(self):
        """
        Block saving when this Pre-Use record contains completed
        machine hours outside the permitted shift maximum.

        This is important when a previously saved record is edited
        after Engine Hours End was populated by the following shift.

        Example:
            EX01
            Start = 2020
            End   = 2035
            Difference = 15 hours

        If maximum allowed = 12:
            SAVE MUST BE BLOCKED.
        """
        bad_assets = []

        (
            max_shift_hours,
            _max_daily_hours,
            saturday_plan_hours,
        ) = get_preuse_hour_limits(
            self.location,
            self.shift_date,
        )

        for row in self.get("pre_use_assets", []):
            if (
                not row.asset_name
                or row.eng_hrs_start is None
                or row.eng_hrs_end is None
            ):
                continue

            start = flt(row.eng_hrs_start)
            end = flt(row.eng_hrs_end)

            # Preserve existing legacy baseline handling.
            # Start = 0 or End = 0 means the engine reading is
            # not yet available / not captured.
            #
            # Do not create false negative working hours.
            if start == 0 or end == 0:
                continue

            working_hours = round(end - start, 1)

            if (
                working_hours < 0
                or working_hours > max_shift_hours
            ):
                bad_assets.append({
                    "asset": row.asset_name,
                    "start": row.eng_hrs_start,
                    "end": row.eng_hrs_end,
                    "working_hours": working_hours,
                    "max_hours": max_shift_hours,
                    "saturday_plan_hours": saturday_plan_hours,
                })

        if not bad_assets:
            return

        cards_html = "".join(
            f"""
            <div style="
                border:1px solid #d9d9d9;
                border-radius:8px;
                padding:12px;
                margin:10px 0;
                background:#fafafa;
            ">
                <div style="font-size:15px; margin-bottom:8px;">
                    <b>Asset:</b> {b['asset']}
                </div>

                <div><b>Start Hours:</b> {b['start']}</div>
                <div><b>End Hours:</b> {b['end']}</div>
                <div>
                    <b>Calculated Hours:</b>
                    <span style="color:#c0392b;">
                        {b['working_hours']:+g}
                    </span>
                </div>
                <div><b>Maximum Allowed:</b> {b['max_hours']:g}</div>
            </div>
            """
            for b in bad_assets
        )

        table_html = f"""
            <div style="font-size:14px; line-height:1.6;">
                <p style="margin-bottom:8px;">
                    <b>❌ Machine hours need to be fixed</b>
                </p>

                {cards_html}

                <p style="margin-top:12px; color:#666;">
                    Please correct the machine hours before saving again.
                </p>
            </div>
        """

        frappe.throw(
            table_html,
            title="Machine Hours Need To Be Fixed"
        )


    def validate_previous_shift_hours(self):
        """
        Validate previous shift hours per asset globally (not per site).
        Uses the current row's start hours to close the previous row for the same asset,
        regardless of location.
        """
        bad_assets = []

        for cr in self.pre_use_assets:
            if not cr.asset_name or cr.eng_hrs_start is None:
                continue

            # Zero means engine hours were NOT captured for this
            # asset in the current shift.
            #
            # Do not use zero to close the previous shift and do
            # not calculate a false negative working-hours value.
            if flt(cr.eng_hrs_start) == 0:
                continue


            prev_row = get_previous_asset_row(cr.asset_name, self.shift_date, self.shift)
            if not prev_row or prev_row.eng_hrs_start is None:
                continue

            eng_hrs_end = cr.eng_hrs_start
            working_hours = round(flt(eng_hrs_end) - flt(prev_row.eng_hrs_start), 1)

            # Allow reset from legacy/blank baseline only when previous start is exactly 0.
            if flt(prev_row.eng_hrs_start) == 0:
                continue            

            (
                max_shift_hours,
                _max_daily_hours,
                saturday_plan_hours,
            ) = get_preuse_hour_limits(
                prev_row.location,
                prev_row.shift_date,
            )

            if working_hours < 0 or working_hours > max_shift_hours:
                bad_assets.append({
                    "asset": cr.asset_name,
                    "prev_start": prev_row.eng_hrs_start,
                    "new_start": cr.eng_hrs_start,
                    "wh": working_hours,
                    "max_hours": max_shift_hours,
                    "saturday_plan_hours": saturday_plan_hours,
                    "prev_doc": prev_row.parent,
                })

        # No invalid machines = validation passes.
        if not bad_assets:
            return

        cards_html = "".join(
            f"""
            <div style="
                border:1px solid #d8d8d8;
                border-radius:8px;
                padding:12px 14px;
                margin:10px 0;
                background:#fafafa;
                line-height:1.7;
            ">
                <div style="
                    font-size:15px;
                    margin-bottom:8px;
                ">
                    <b>Machine:</b> {b['asset']}
                </div>

                <div>
                    <b>Previous Document:</b>
                    {b['prev_doc']}
                </div>

                <div>
                    <b>Previous Start:</b>
                    {b['prev_start']}
                </div>

                <div>
                    <b>Current Start:</b>
                    {b['new_start']}
                </div>

                <div>
                    <b>Calculated Hours:</b>
                    <span style="
                        font-weight:600;
                        color:#c0392b;
                    ">
                        {b['wh']}
                    </span>
                </div>

                <div>
                    <b>Maximum Allowed:</b>
                    {b['max_hours']} hours
                </div>
            </div>
            """
            for b in bad_assets
        )

        popup_html = f"""
            <div style="
                font-size:14px;
                line-height:1.6;
                max-width:100%;
            ">
                <div style="
                    font-size:16px;
                    font-weight:600;
                    margin-bottom:8px;
                ">
                    ❌ Cannot save this shift
                </div>

                <div style="margin-bottom:10px;">
                    The following machine hours need to be fixed:
                </div>

                {cards_html}

                <div style="
                    margin-top:12px;
                    color:#666;
                ">
                    Please adjust the
                    <b>engine start hours</b>
                    before saving again.
                </div>
            </div>
        """

        frappe.throw(
            popup_html,
            title="Machine Hours Need To Be Fixed"
        )

    def update_previous_eng_hrs_end(self):
        """
        Copy eng_hrs_end/working_hours into the previous row for each asset globally,
        then re-run integrity on each affected parent document.
        """
        try:
            touched_parents = set()

            for cr in self.pre_use_assets:
                if not cr.asset_name or cr.eng_hrs_start is None:
                    continue

                # Zero means NOT CAPTURED.
                #
                # Never overwrite the previous shift's valid
                # Engine Hours End with 0.
                if flt(cr.eng_hrs_start) == 0:
                    continue

                prev_row = get_previous_asset_row(cr.asset_name, self.shift_date, self.shift)
                if not prev_row:
                    continue

                prev_row.eng_hrs_end = cr.eng_hrs_start
                if prev_row.eng_hrs_start is not None:
                    prev_row.working_hours = round(flt(prev_row.eng_hrs_end) - flt(prev_row.eng_hrs_start), 1)

                frappe.db.set_value(
                    "Pre-use Assets",
                    prev_row.name,
                    {
                        "eng_hrs_end": prev_row.eng_hrs_end,
                        "working_hours": prev_row.working_hours
                    },
                    update_modified=False
                )

                touched_parents.add(prev_row.parent)

            previous_html_parts = []

            for parent_name in touched_parents:
                prev_doc = frappe.get_doc("Pre-Use Hours", parent_name)
                prev_doc.evaluate_data_integrity()

                frappe.db.set_value(
                    "Pre-Use Hours",
                    prev_doc.name,
                    {
                        "data_integrity_summary": prev_doc.data_integrity_summary,
                        "data_integ_indicator": prev_doc.data_integ_indicator
                    },
                    update_modified=False
                )

                frappe.publish_realtime("preuse:reload_doc", {
                    "doctype": "Pre-Use Hours", "name": prev_doc.name
                })

                previous_html_parts.append(
                    f"<h4>Previous Shift Integrity: {prev_doc.name}</h4>{prev_doc.data_integrity_summary or '<p><b>No issues in previous shift.</b></p>'}"
                )

            nav_buttons = """
                <div style="margin-bottom:10px;">
                  <button class="btn btn-sm btn-secondary" id="prev_record_top">⬅️ Previous</button>
                  <button class="btn btn-sm btn-secondary" id="next_record_top">Next ➡️</button>
                </div>
            """

            current_html = self.data_integrity_summary or "<p><b>No issues in current shift.</b></p>"
            previous_html = "".join(previous_html_parts) or "<p><b>No previous shift updates.</b></p>"

            legend = """
                <hr>
                <h5>Legend & Help</h5>
                <p>
                  <b>🔴 Red (Critical):</b> Negative working hours or hours above the configured shift maximum.<br>
                  <b>🟠 Yellow (Warning):</b> Missing data or zero hours.<br>
                  <b>🟢 Green (Valid):</b> All data checks passed.
                </p>
                <p style="color:gray; font-size:90%;">🔁 Save to refresh this summary.</p>
            """

            merged = f"""
                {nav_buttons}
                <div style="display:flex; gap:20px;">
                  <div style="flex:1; border-right:1px solid #ddd; padding-right:10px;">
                    <h4>Current Shift Integrity</h4>
                    {current_html}
                  </div>
                  <div style="flex:1; padding-left:10px;">
                    {previous_html}
                  </div>
                </div>
                {legend}
            """

            self.set("data_integrity_summary", merged)
            self.flags.dirty = True

        except Exception as e:
            frappe.log_error(message=frappe.get_traceback(), title="Engine Hours Update Error")
            frappe.throw(_("Error updating previous engine hours: {0}").format(e))











    def evaluate_data_integrity(self):
        errors = []
        warning_count = 0
        error_count = 0


        if self.location == "Plot 22":
            self.data_integ_indicator = "🟢"
            self.data_integrity_summary = "<p><b>✅ Plot 22 excluded from integrity checks.</b></p>"
            return



        (
            max_shift_hours,
            max_daily_hours,
            saturday_plan_hours,
        ) = get_preuse_hour_limits(
            self.location,
            self.shift_date,
        )

        is_saturday = (
            bool(self.shift_date)
            and getdate(self.shift_date).weekday() == 5
        )

        if is_saturday:
            saturday_plan_text = (
                f"{saturday_plan_hours:g}"
                if saturday_plan_hours is not None
                else "Not Found"
            )

            rule_note = (
                "<p>"
                "<b>Saturday Pre-Use validation:</b> "
                f"Monthly Planning Saturday Hours = {saturday_plan_text}; "
                f"Maximum Working Hours per shift = {max_shift_hours:g}."
                "</p>"
            )
        else:
            rule_note = (
                "<p>"
                "<b>Pre-Use validation:</b> "
                f"Maximum Working Hours per shift = {max_shift_hours:g}."
                "</p>"
            )

        for idx, row in enumerate(self.get("pre_use_assets", []), start=1):
            row_issues = []
            eng_hrs_start = row.eng_hrs_start
            eng_hrs_end = row.eng_hrs_end
            working_hours = None




            # Engine-hours value 0 means NOT CAPTURED.
            #
            # Do not calculate 0 - previous_reading because that
            # creates a false negative working-hours value.
            start_missing = (
                eng_hrs_start is None
                or flt(eng_hrs_start) == 0
            )
            end_missing = (
                eng_hrs_end is None
                or flt(eng_hrs_end) == 0
            )

            if start_missing:
                row_issues.append(
                    "❗ <span style='color:orange;'>"
                    "Missing Engine Hours Start"
                    "</span>"
                )

            if end_missing:
                row_issues.append(
                    "❗ <span style='color:orange;'>"
                    "Missing Engine Hours End"
                    "</span>"
                )

            if start_missing or end_missing:
                row.working_hours = 0.0

            else:
                working_hours = round(
                    flt(eng_hrs_end) - flt(eng_hrs_start),
                    1,
                )
                row.working_hours = working_hours

                if working_hours < 0:
                    row_issues.append(
                        "❌ <span style='color:red;'>"
                        "Negative Working Hours"
                        "</span>"
                    )
                elif working_hours > max_shift_hours:
                    row_issues.append(
                        "❌ <span style='color:red;'>"
                        f"Unrealistic Working Hours &gt; {max_shift_hours:g}"
                        "</span>"
                    )
                elif working_hours == 0:
                    row_issues.append(
                        "⚠️ <span style='color:orange;'>"
                        "Zero Working Hours"
                        "</span>"
                    )

            if row_issues:
                warning_count += sum("⚠️" in i or "orange" in i for i in row_issues)
                error_count += sum("❌" in i or "red" in i for i in row_issues)

                errors.append({
                    # Use the actual child-table row number shown
                    # in the Pre Use grid.
                    "row": row.idx or idx,
                    "asset": row.asset_name,
                    "issues": row_issues
                })

        indicator = "🟢"
        if error_count > 0:
            indicator = "🔴"
        elif warning_count > 0:
            indicator = "🟠"

        # Build the HTML summary table
        if errors:
            rows_html = "".join(
                f"<tr><td>Row #{e['row']}</td><td>{e['asset']}</td><td>{'<br>'.join(e['issues'])}</td></tr>"
                for e in errors
            )
            self.data_integrity_summary = f"""
                {rule_note}
                <table class="table table-bordered">
                    <tr><th>Row</th><th>Asset</th><th>Issues</th></tr>
                    {rows_html}
                </table>
            """
        else:
            self.data_integrity_summary = (
                rule_note
                + "<p><b>✅ No integrity issues found.</b></p>"
            )

        self.data_integ_indicator = indicator


# ISAMBANE SATURDAY PRE-USE LIMITS
def get_preuse_hour_limits(location, shift_date):
    """
    Return the valid Pre-Use working-hour limits for a site/date.

    Current business rules:

    Monday-Friday / Sunday:
        Shift maximum = 12 hours
        Daily maximum = 24 hours

    Saturday:
        Monthly Planning saturday_shift_hours = 9
            Shift maximum = 12 hours
            Daily maximum = 24 hours

        Monthly Planning saturday_shift_hours = 7
            Shift maximum = 9 hours
            Daily maximum = 18 hours

    Returns:
        (
            max_shift_hours,
            max_daily_hours,
            saturday_plan_hours,
        )
    """

    shift_date = getdate(shift_date)

    # Existing/default Pre-Use rule
    max_shift_hours = 12.0
    max_daily_hours = 24.0
    saturday_plan_hours = None

    if not shift_date:
        return (
            max_shift_hours,
            max_daily_hours,
            saturday_plan_hours,
        )

    # Python:
    # Monday    = 0
    # Tuesday   = 1
    # Wednesday = 2
    # Thursday  = 3
    # Friday    = 4
    # Saturday  = 5
    # Sunday    = 6
    if shift_date.weekday() != 5:
        return (
            max_shift_hours,
            max_daily_hours,
            saturday_plan_hours,
        )

    monthly_plan = get_monthly_production_plan(
        location,
        shift_date,
    )

    if not monthly_plan:
        return (
            max_shift_hours,
            max_daily_hours,
            saturday_plan_hours,
        )

    saturday_plan_hours = flt(
        monthly_plan.get("saturday_shift_hours")
    )

    if saturday_plan_hours == 7:
        max_shift_hours = 9.0
        max_daily_hours = 18.0

    elif saturday_plan_hours == 9:
        max_shift_hours = 12.0
        max_daily_hours = 24.0

    else:
        frappe.throw(
            "Saturday Shift No. Working Hours in Monthly Production "
            "Planning must be 7 or 9 for Pre-Use integrity validation. "
            f"Site: {location}, "
            f"Date: {shift_date}, "
            f"Configured value: {saturday_plan_hours:g}"
        )

    return (
        max_shift_hours,
        max_daily_hours,
        saturday_plan_hours,
    )


def get_monthly_production_plan(location, shift_date):
    """
    Fetch Monthly Production Planning record for this site + date.
    """
    records = frappe.get_all(
        "Monthly Production Planning",
        filters={
            "location": location,
            "prod_month_start_date": ["<=", shift_date],
            "prod_month_end_date": [">=", shift_date],
            "site_status": "Producing"
        },
        fields=[
            "name",
            "prod_month_start_date",
            "prod_month_end_date",
            "shift_system",
            "weekday_shift_hours",
            "saturday_shift_hours",
            "num_sat_shifts",
        ],
        limit=1
    )
    return records[0] if records else None


def validate_shift_date(doc, monthly_plan):
    """
    v16-safe: normalize values to datetime.date before comparing.
    Frappe may supply doc.shift_date as a string on new/unsaved docs.
    """
    shift_date = getdate(doc.shift_date)
    start_date = getdate(monthly_plan.prod_month_start_date)
    end_date = getdate(monthly_plan.prod_month_end_date)

    if not shift_date:
        frappe.throw(_("Shift Date is required."))

    if shift_date < start_date or shift_date > end_date:
        frappe.throw(_("Shift Date is outside the producing month range."))


def check_previous_record_sequence(doc, monthly_plan):
    """
    Ensure the previous shift record exists and sequencing is correct.
    """
    prev_doc = get_previous_document(doc.location, doc.creation)
    if not prev_doc:
        return

    # Enforce that shift date is not going backwards
    if prev_doc.shift_date and doc.shift_date and doc.shift_date < prev_doc.shift_date:
        frappe.throw("Shift Date cannot be before the previous record's shift date.")


def get_previous_document(location, creation_dt):
    """
    Get the previous Pre-Use Hours doc (by creation timestamp) for a location.
    """
    prev_name = frappe.db.get_value(
        "Pre-Use Hours",
        {"location": location, "creation": ["<", creation_dt]},
        "name",
        order_by="creation desc"
    )
    if not prev_name:
        return None
    return frappe.get_doc("Pre-Use Hours", prev_name)




def get_previous_asset_row(asset_name, shift_date, shift):
    """
    Get the previous Pre-use Assets row for the same asset globally,
    based on shift_date + shift order, not creation time.
    Plot 22 records are included as valid previous records.
    """
    shift_order = {
        "Morning": 1,
        "Day": 1,
        "Afternoon": 2,
        "Night": 3,
    }

    current_order = shift_order.get(shift, 0)

    row = frappe.db.sql("""
        SELECT
            pa.name,
            pa.parent,
            pa.asset_name,
            pa.eng_hrs_start,
            pa.eng_hrs_end,
            pa.working_hours,
            puh.location,
            puh.shift_date,
            puh.shift
        FROM `tabPre-use Assets` pa
        INNER JOIN `tabPre-Use Hours` puh ON puh.name = pa.parent
        WHERE pa.asset_name = %s
          AND (
                puh.shift_date < %s
                OR (
                    puh.shift_date = %s
                    AND CASE puh.shift
                        WHEN 'Morning' THEN 1
                        WHEN 'Day' THEN 1
                        WHEN 'Afternoon' THEN 2
                        WHEN 'Night' THEN 3
                        ELSE 0
                    END < %s
                )
          )
        ORDER BY
            puh.shift_date DESC,
            CASE puh.shift
                WHEN 'Night' THEN 3
                WHEN 'Afternoon' THEN 2
                WHEN 'Morning' THEN 1
                WHEN 'Day' THEN 1
                ELSE 0
            END DESC
        LIMIT 1
    """, (asset_name, shift_date, shift_date, current_order), as_dict=True)

    return frappe._dict(row[0]) if row else None
