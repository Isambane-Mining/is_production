
"""Excel export using the existing permission-checked hourly analysis."""
from collections import OrderedDict
from io import BytesIO
import re

import xlsxwriter

COLOURS = {
    "green": "#00B050",
    "amber": "#FF9F1C",
    "red": "#FFE066",
    "bright_red": "#D63C4B",
    "grey": "#E8EDF2",
}

LABELS = {
    "green": "Target achieved >=100%",
    "amber": "85-99.9% achieved",
    "red": "Below 85%",
    "bright_red": "Zero production, machines available",
    "grey": "Fatigue / Non-working",
}


def make_workbook(data):
    output = BytesIO()
    workbook = xlsxwriter.Workbook(output, {"in_memory": True})

    workbook.set_properties({
        "title": "Hourly Productivity Analysis",
        "subject": "Production performance by operational day",
        "comments": "Generated from permission-checked ERPNext report data",
    })

    title = workbook.add_format({
        "bold": True, "font_size": 18, "font_color": "#FFFFFF",
        "bg_color": "#183153", "valign": "vcenter",
    })
    section = workbook.add_format({
        "bold": True, "font_color": "#FFFFFF",
        "bg_color": "#315574", "border": 1,
        "border_color": "#CAD5E0",
    })
    head = workbook.add_format({
        "bold": True, "bg_color": "#DFE8F1",
        "border": 1, "border_color": "#B8C8D5",
        "align": "center",
    })
    text = workbook.add_format({"font_color": "#253647"})
    integer = workbook.add_format({"num_format": "#,##0"})
    decimal = workbook.add_format({"num_format": "#,##0.0"})
    percentage = workbook.add_format({"num_format": "0.0%"})

    fills = {
        key: workbook.add_format({
            "bg_color": colour,
            "font_color": "#FFFFFF" if key == "bright_red" else "#17212B",
            "border": 1,
            "border_color": "#C7D2DD",
            "align": "center",
            "num_format": "#,##0.0#",
        })
        for key, colour in COLOURS.items()
    }

    missing_fills = {
        key: workbook.add_format({
            "bg_color": colour,
            "font_color": "#FFFFFF" if key == "bright_red" else "#17212B",
            "border": 1,
            "border_color": "#C7D2DD",
            "align": "center",
            "bold": True,
        })
        for key, colour in COLOURS.items()
    }

    months = OrderedDict()
    for row in data["rows"]:
        months.setdefault(row["month_key"], []).append(row)

    overview = workbook.add_worksheet("Summary")
    overview.hide_gridlines(2)
    overview.set_tab_color("#183153")
    overview.set_column("A:A", 22)
    overview.set_column("B:B", 18)
    overview.set_column("C:J", 19)

    overview.merge_range("A1:J2", "HOURLY PRODUCTIVITY | MANAGEMENT SUMMARY", title)
    overview.write("A4", "Site", section)
    overview.write("B4", data["site"], text)
    overview.write("A5", "From date", section)
    overview.write("B5", data["from_date"], text)
    overview.write("A6", "To date", section)
    overview.write("B6", data["to_date"], text)

    headers = [
        "Month", "Days", "Actual BCM", "Target BCM",
        "Achievement", ">=100% hrs", "85-99.9% hrs",
        "Below 85% hrs", "Zero-production hrs", "Non-working hrs",
    ]

    for col, label in enumerate(headers):
        overview.write(8, col, label, head)

    for month_index, (month_key, rows) in enumerate(months.items()):
        month_name = rows[0]["month"]
        sheet = workbook.add_worksheet(month_name[:31])
        sheet.set_tab_color("#00B050")
        sheet.hide_gridlines(2)
        sheet.set_landscape()
        sheet.fit_to_pages(1, 1)
        sheet.set_paper(8)
        sheet.set_margins(0.25, 0.25, 0.4, 0.4)
        sheet.repeat_rows(0, 5)
        sheet.freeze_panes(6, 3)
        sheet.set_column("A:A", 13)
        sheet.set_column("B:B", 9)
        sheet.set_column("C:C", 14)
        sheet.set_column(3, 26, 11)

        sheet.merge_range("A1:AA2", f"{data['site']} | {month_name} | HOURLY PRODUCTIVITY", title)
        sheet.merge_range("A4:C4", "OPERATIONAL DAY 06:00-06:00", section)
        sheet.merge_range("D4:O4", "DAY SHIFT | 06:00-18:00", section)
        sheet.merge_range("P4:AA4", "NIGHT SHIFT | 18:00-06:00", section)

        for col, label in enumerate(["Date", "Day", "Month"]):
            sheet.write(5, col, label, head)

        hours = [cell["label"] for cell in rows[0]["cells"]]
        for col, label in enumerate(hours, 3):
            sheet.write(5, col, label, head)

        totals = {
            "actual": 0.0, "target": 0.0,
            **{key: 0.0 for key in COLOURS},
        }

        for day_index, row in enumerate(rows):
            excel_row = day_index + 6
            sheet.write(excel_row, 0, row["date"], text)
            sheet.write(excel_row, 1, row["day"], text)
            sheet.write(excel_row, 2, row["month"], text)

            for hour_index, cell in enumerate(row["cells"]):
                col = hour_index + 3
                category = cell["category"]
                if category not in COLOURS:
                    category = "red"

                actual = float(cell["actual_bcm"] or 0)
                target = float(cell["target_bcm"] or 0)
                totals["actual"] += actual
                totals["target"] += target

                segments = cell.get("segments") or []
                if segments:
                    for segment in segments:
                        key = segment["category"]
                        if key in totals:
                            totals[key] += (
                                float(segment["end_percent"])
                                - float(segment["start_percent"])
                            ) / 100
                else:
                    totals[category] += 1

                if not cell["has_record"]:
                    sheet.write(excel_row, col, f"{actual:g}*", missing_fills[category])
                    sheet.write_comment(
                        excel_row, col,
                        "No hourly production record. Zero is unverified."
                    )
                elif not cell["completed"]:
                    sheet.write(excel_row, col, f"{actual:g}†", missing_fills[category])
                    sheet.write_comment(
                        excel_row, col, "Current or unfinished hour."
                    )
                else:
                    sheet.write_number(excel_row, col, actual, fills[category])

                if len(segments) > 1:
                    sheet.write_comment(
                        excel_row, col,
                        "Mixed classification within this hour: "
                        + ", ".join(
                            f"{s['category']}: "
                            f"{float(s['end_percent'])-float(s['start_percent']):.0f}%"
                            for s in segments
                        )
                    )

        end_row = 6 + len(rows)
        sheet.autofilter(5, 0, end_row - 1, 26)
        sheet.print_area(0, 0, end_row + 10, 26)

        note_row = end_row + 2
        sheet.merge_range(
            note_row, 0, note_row, 12,
            "* Missing record (unverified zero)  |  † Unfinished hour",
            text,
        )

        sheet.write(note_row + 2, 0, "Performance category", head)
        sheet.write(note_row + 2, 1, "Hours", head)
        for ix, key in enumerate(COLOURS):
            sheet.write(note_row + 3 + ix, 0, LABELS[key], fills[key])
            sheet.write_number(note_row + 3 + ix, 1, totals[key], decimal)
        sheet.set_column("A:A", 35)

        summary_row = 9 + month_index
        values = [
            month_name, len(rows), totals["actual"], totals["target"],
            totals["actual"] / totals["target"] if totals["target"] else 0,
            totals["green"], totals["amber"], totals["red"],
            totals["bright_red"], totals["grey"],
        ]
        for col, value in enumerate(values):
            fmt = percentage if col == 4 else decimal if col >= 5 else integer if col in (1, 2, 3) else text
            if isinstance(value, str):
                overview.write(summary_row, col, value, fmt)
            else:
                overview.write_number(summary_row, col, value, fmt)

    last = 9 + len(months)
    overview.autofilter(8, 0, last - 1, 9)
    overview.freeze_panes(9, 1)

    chart = workbook.add_chart({"type": "column"})
    chart.add_series({
        "name": "Actual BCM",
        "categories": ["Summary", 9, 0, last - 1, 0],
        "values": ["Summary", 9, 2, last - 1, 2],
    })
    chart.add_series({
        "name": "Target BCM",
        "categories": ["Summary", 9, 0, last - 1, 0],
        "values": ["Summary", 9, 3, last - 1, 3],
    })
    chart.set_title({"name": "Monthly Actual vs Target BCM"})
    chart.set_y_axis({"name": "BCM"})
    chart.set_legend({"position": "bottom"})
    chart.set_style(10)
    overview.insert_chart(last + 3, 0, chart, {"x_scale": 1.6, "y_scale": 1.4})

    overview.write(
        last + 27, 0,
        "Performance categories are hour-weighted. "
        "Mixed fatigue hours contribute fractionally.",
        text,
    )

    workbook.close()
    return output.getvalue()
