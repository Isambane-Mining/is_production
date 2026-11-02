from __future__ import annotations

import base64
import binascii
from io import BytesIO
from pathlib import Path

import frappe
from typing import BinaryIO

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor


SLIDE_WIDTH = 13.333
SLIDE_HEIGHT = 7.5
FONT_NAME = "Aptos"

BLACK = "050505"
WHITE = "FFFFFF"
MUTED = "AAB4C3"
RED = "E03124"


def build_hod_presentation(
    payload: dict,
    output: BinaryIO | BytesIO,
) -> None:
    """Build a PowerPoint from browser-captured ERP report sections.

    Expected payload format::

        {
            "site": "Koppie / Uitgevallen",
            "period_label": "01 Jul 2026 to 17 Jul 2026",
            "generated_by": "Administrator",
            "generated_at": "2026-07-17 13:00:00",
            "captured_slides": [
                {
                    "title": "HOD Production Summary",
                    "image_data": "data:image/jpeg;base64,..."
                },
                {
                    "title": "Availability & Utilisation - Koppie",
                    "image_data": "data:image/jpeg;base64,..."
                }
            ]
        }

    Each captured image is fitted proportionally onto a widescreen slide.
    This preserves the rendered ERP design instead of redrawing it with
    PowerPoint shapes.
    """

    prs = Presentation()
    prs.slide_width = Inches(SLIDE_WIDTH)
    prs.slide_height = Inches(SLIDE_HEIGHT)

    site = str(payload.get("site") or "HOD Presentation")
    generated_by = str(payload.get("generated_by") or "Isambane")

    prs.core_properties.title = f"HOD Presentation - {site}"
    prs.core_properties.subject = (
        "HOD Production Summary and Availability & Utilisation"
    )
    prs.core_properties.author = generated_by

    captured_slides = payload.get("captured_slides") or []
    captured_slides = [
        item
        for item in captured_slides
        if isinstance(item, dict) and item.get("image_data")
    ]

    if not captured_slides:
        _add_error_slide(
            prs,
            "No captured report sections were supplied. "
            "Refresh the report and click Download Presentation again.",
        )
    else:
        for captured in captured_slides:
            image_bytes = _decode_image_data(captured.get("image_data"))
            if not image_bytes:
                continue

            slide = _blank_slide(prs)

            _add_branded_header(
                slide,
                _presentation_header_title(
                    str(captured.get("title") or ""),
                    site,
                ),
                str(captured.get("site") or site),
                len(prs.slides),
            )

            is_production_slide = str(
                captured.get("title") or ""
            ).casefold().startswith("hod production summary")

            if is_production_slide:
                _add_production_subheading(
                    slide,
                    str(captured.get("site") or site),
                    str(payload.get("end_date") or ""),
                )

            _add_image_fitted(
                slide,
                image_bytes,
                top_offset=(
                    1.72 if is_production_slide else 0.94
                ),
            )

        if not prs.slides:
            _add_error_slide(
                prs,
                "The report images could not be decoded. "
                "Refresh the report and try the download again.",
            )

    prs.save(output)


def _decode_image_data(value) -> bytes | None:
    if not value:
        return None

    if isinstance(value, bytes):
        return value

    text = str(value).strip()

    if not text:
        return None

    if text.startswith("data:"):
        marker = ";base64,"
        if marker not in text:
            return None
        text = text.split(marker, 1)[1]

    try:
        return base64.b64decode(text, validate=True)
    except (ValueError, binascii.Error):
        try:
            return base64.b64decode(text)
        except (ValueError, binascii.Error):
            return None


def _add_image_fitted(
    slide,
    image_bytes: bytes,
    top_offset: float = 0.94,
) -> None:
    """Fit captured report clearly without distorting its proportions."""

    left = Inches(0.20)
    top = Inches(top_offset)

    available_width = Inches(SLIDE_WIDTH) - left
    available_height = Inches(
        SLIDE_HEIGHT - top_offset - 0.22
    )

    picture = slide.shapes.add_picture(
        BytesIO(image_bytes),
        0,
        0,
    )

    if picture.width <= 0 or picture.height <= 0:
        raise ValueError("Invalid captured report image.")

    # Preserve original graph and text proportions.
    scale = min(
        available_width / picture.width,
        available_height / picture.height,
    )

    picture.width = int(picture.width * scale)
    picture.height = int(picture.height * scale)

    # Align to the top-left of the white reporting area.
    picture.left = left
    picture.top = top


def _complex_for_sites(site: str) -> str:
    """Use the named complex only when all sites belong to it."""

    selected = [
        name.strip().casefold()
        for name in site.split("/")
        if name.strip()
    ]

    complexes = {
        "Middelburg Complex": {
            "klipfontein",
            "gwab",
            "kriel rehabilitation",
        },
        "Ermelo Complex": {
            "uitgevallen",
            "koppie",
        },
    }

    if selected:
        for name, members in complexes.items():
            if all(item in members for item in selected):
                return name

    return "HOD"


def _presentation_header_title(title: str, site: str) -> str:
    """Use specific headings for Production, A&U and Hours Based."""

    title = str(title or "").strip()

    if title.casefold().startswith("hod production summary"):
        return f"{_complex_for_sites(site)} - Production Performance"

    if " - " in title:
        site_name, detail = title.split(" - ", 1)
        site_name = site_name.strip()
        detail = detail.strip()

        if "hours based" in detail.casefold():
            category = detail.split(" Hours Based", 1)[0].strip()
            if not category:
                category = "All Machines"

            return (
                f"Hours Based Performance - "
                f"{site_name} - {category}"
            )

        return f"Engineering Performance - {site_name}"

    return "Engineering Performance"


def _add_branded_header(
    slide,
    title: str,
    site: str,
    number: int,
):
    """Compact logo, yellow heading and black framing."""

    from pptx.enum.shapes import MSO_SHAPE

    header = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        0,
        0,
        Inches(SLIDE_WIDTH),
        Inches(0.88),
    )
    header.fill.solid()
    header.fill.fore_color.rgb = _rgb(BLACK)
    header.line.fill.background()
    _lock_decoration(header)

    logo_path = (
        Path(frappe.get_site_path("public", "files"))
        / "ISAMBANE-WHITE.png"
    )

    if not logo_path.is_file():
        raise FileNotFoundError(
            f"Isambane logo not found: {logo_path}"
        )

    picture = slide.shapes.add_picture(str(logo_path), 0, 0)

    scale = min(
        Inches(2.10) / picture.width,
        Inches(0.66) / picture.height,
    )
    picture.width = int(picture.width * scale)
    picture.height = int(picture.height * scale)
    picture.left = Inches(0.23)
    picture.top = int(
        (Inches(0.88) - picture.height) / 2
    )
    _lock_decoration(picture)

    heading = slide.shapes.add_textbox(
        Inches(2.55),
        Inches(0.17),
        Inches(10.30),
        Inches(0.48),
    )

    paragraph = heading.text_frame.paragraphs[0]
    paragraph.text = title
    paragraph.font.name = FONT_NAME
    paragraph.font.size = Pt(19)
    paragraph.font.bold = True
    paragraph.font.color.rgb = _rgb("FFD700")
    paragraph.alignment = PP_ALIGN.CENTER

    page = slide.shapes.add_textbox(
        Inches(12.0),
        Inches(7.24),
        Inches(1.0),
        Inches(0.19),
    )

    paragraph = page.text_frame.paragraphs[0]
    paragraph.text = str(number)
    paragraph.font.name = FONT_NAME
    paragraph.font.size = Pt(9)
    paragraph.font.color.rgb = _rgb(WHITE)
    paragraph.alignment = PP_ALIGN.RIGHT

def _lock_decoration(shape) -> None:
    """Ask PowerPoint to lock decoration position and selection."""

    from pptx.oxml.xmlchemy import OxmlElement

    element = shape._element

    if element.tag.endswith("}pic"):
        nv = element.find(
            "{http://schemas.openxmlformats.org/"
            "presentationml/2006/main}nvPicPr"
        )
        container_tag = "cNvPicPr"
        locks_tag = "picLocks"
    else:
        nv = element.find(
            "{http://schemas.openxmlformats.org/"
            "presentationml/2006/main}nvSpPr"
        )
        container_tag = "cNvSpPr"
        locks_tag = "spLocks"

    if nv is None:
        return

    container = next(
        (child for child in nv if child.tag.endswith("}" + container_tag)),
        None,
    )

    if container is None:
        return

    locks = next(
        (child for child in container if child.tag.endswith("}" + locks_tag)),
        None,
    )

    if locks is None:
        locks = OxmlElement("a:" + locks_tag)
        container.append(locks)

    locks.set("noMove", "1")
    locks.set("noResize", "1")
    locks.set("noSelect", "1")



def _add_production_subheading(
    slide,
    site: str,
    end_date: str,
) -> None:
    """Show selected sites and the selected reporting month."""

    from datetime import datetime

    sites = [
        item.strip()
        for item in str(site or "").split("/")
        if item.strip()
    ]

    if not sites:
        sites = ["Selected Sites"]

    if len(sites) == 1:
        site_names = sites[0]
    elif len(sites) == 2:
        site_names = " and ".join(sites)
    else:
        site_names = ", ".join(sites[:-1]) + " and " + sites[-1]

    subtitle = (
        f"Outlook and Operating Indicators for {site_names}"
    )

    description = slide.shapes.add_textbox(
        Inches(0.50),
        Inches(0.99),
        Inches(12.30),
        Inches(0.35),
    )

    paragraph = description.text_frame.paragraphs[0]
    paragraph.text = subtitle
    paragraph.alignment = PP_ALIGN.CENTER
    paragraph.font.name = FONT_NAME
    paragraph.font.size = Pt(16)
    paragraph.font.color.rgb = _rgb("53627C")

    try:
        month_label = datetime.strptime(
            str(end_date)[:10], "%Y-%m-%d"
        ).strftime("%B %Y")
    except (ValueError, TypeError):
        month_label = ""

    if month_label:
        month_box = slide.shapes.add_textbox(
            Inches(0.50),
            Inches(1.34),
            Inches(12.30),
            Inches(0.30),
        )

        paragraph = month_box.text_frame.paragraphs[0]
        paragraph.text = month_label
        paragraph.alignment = PP_ALIGN.LEFT
        paragraph.font.name = FONT_NAME
        paragraph.font.size = Pt(15)
        paragraph.font.color.rgb = _rgb("111111")



def _blank_slide(prs: Presentation):
    slide = prs.slides.add_slide(prs.slide_layouts[6])

    # A true, non-selectable slide background.
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = _rgb(WHITE)

    # Retain left black border.
    left_border = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        0,
        Inches(0.88),
        Inches(0.20),
        Inches(SLIDE_HEIGHT - 0.88),
    )
    left_border.fill.solid()
    left_border.fill.fore_color.rgb = _rgb(BLACK)
    left_border.line.fill.background()
    _lock_decoration(left_border)

    # Retain bottom border. No right border is added.
    bottom_border = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        0,
        Inches(7.30),
        Inches(SLIDE_WIDTH),
        Inches(0.20),
    )
    bottom_border.fill.solid()
    bottom_border.fill.fore_color.rgb = _rgb(BLACK)
    bottom_border.line.fill.background()
    _lock_decoration(bottom_border)

    return slide


def _add_error_slide(prs: Presentation, message: str) -> None:
    slide = _blank_slide(prs)

    shape = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE,
        Inches(0.70),
        Inches(2.25),
        Inches(11.93),
        Inches(2.30),
    )
    shape.fill.solid()
    shape.fill.fore_color.rgb = _rgb("151515")
    shape.line.color.rgb = _rgb(RED)
    shape.line.width = Pt(1.2)

    _add_text(
        slide,
        "PRESENTATION COULD NOT BE GENERATED",
        1.05,
        2.65,
        11.25,
        0.45,
        22,
        WHITE,
        bold=True,
        align=PP_ALIGN.CENTER,
    )
    _add_text(
        slide,
        message,
        1.25,
        3.25,
        10.85,
        0.75,
        13,
        MUTED,
        align=PP_ALIGN.CENTER,
    )


def _add_text(
    slide,
    text: str,
    x: float,
    y: float,
    w: float,
    h: float,
    font_size: float,
    colour: str,
    *,
    bold: bool = False,
    align=PP_ALIGN.LEFT,
):
    box = slide.shapes.add_textbox(
        Inches(x),
        Inches(y),
        Inches(w),
        Inches(h),
    )
    frame = box.text_frame
    frame.clear()
    frame.word_wrap = True
    frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    frame.margin_left = Inches(0.02)
    frame.margin_right = Inches(0.02)
    frame.margin_top = Inches(0.01)
    frame.margin_bottom = Inches(0.01)

    paragraph = frame.paragraphs[0]
    paragraph.alignment = align
    paragraph.space_before = Pt(0)
    paragraph.space_after = Pt(0)

    run = paragraph.add_run()
    run.text = str(text or "")
    run.font.name = FONT_NAME
    run.font.size = Pt(font_size)
    run.font.bold = bold
    run.font.color.rgb = _rgb(colour)
    return box


def _rgb(hex_colour: str) -> RGBColor:
    text = str(hex_colour or "000000").strip().lstrip("#")
    return RGBColor(
        int(text[0:2], 16),
        int(text[2:4], 16),
        int(text[4:6], 16),
    )