from __future__ import annotations

import base64
import binascii
from io import BytesIO
from pathlib import Path
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

LOGO_PATH = (
    Path(__file__).resolve().parents[3]
    / "public"
    / "images"
    / "isambane_logo.png"
)


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
            slide_title = str(
                captured.get("title")
                or site
            )
            slide_subtitle = str(
                captured.get("subtitle")
                or ""
            )
            slide_period = str(
                captured.get("period_label")
                or ""
            )
            heading_style = str(
                captured.get("heading_style")
                or ""
            )

            detailed_heading = bool(
                slide_subtitle
                or slide_period
            )

            engineering_heading = (
                heading_style
                == "engineering"
            )

            hours_heading = (
                "Hours Based Performance"
                in slide_title
            )

            _add_slide_heading(
                slide,
                slide_title,
                subtitle=slide_subtitle,
                period_label=slide_period,
                heading_style=heading_style,
            )

            image_top = (
                0.58
                if hours_heading
                else (
                    0.80
                    if engineering_heading
                    else (
                        1.48
                        if detailed_heading
                        else 0.72
                    )
                )
            )

            image_bottom_margin = (
                0.08
                if hours_heading
                else (
                    0.28
                    if detailed_heading
                    else 0.18
                )
            )

            _add_image_fitted(
                slide,
                image_bytes,
                top_inches=image_top,
                height_inches=(
                    SLIDE_HEIGHT
                    - image_top
                    - image_bottom_margin
                ),
                top_align=(
                    engineering_heading
                    or hours_heading
                ),
                side_margin_inches=(
                    0.08
                    if hours_heading
                    else (
                        0.22
                        if engineering_heading or detailed_heading
                        else 0.0
                    )
                ),
                stretch_to_fill=(
                    detailed_heading
                    or hours_heading
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
    *,
    top_inches: float = 0.0,
    height_inches: float = SLIDE_HEIGHT,
    top_align: bool = False,
    side_margin_inches: float = 0.0,
    stretch_to_fill: bool = False,
) -> None:
    image_stream = BytesIO(image_bytes)

    picture = slide.shapes.add_picture(
        image_stream,
        0,
        0,
    )

    side_margin = Inches(side_margin_inches)
    slide_w = Inches(
        SLIDE_WIDTH
        - (side_margin_inches * 2)
    )
    slide_h = Inches(height_inches)
    top_offset = Inches(top_inches)

    image_w = picture.width
    image_h = picture.height

    if stretch_to_fill:
        picture.left = side_margin
        picture.top = top_offset
        picture.width = slide_w
        picture.height = slide_h
        return

    if not image_w or not image_h:
        picture.left = 0
        picture.top = top_offset
        picture.width = slide_w
        picture.height = slide_h
        return

    scale = min(
        slide_w / image_w,
        slide_h / image_h,
    )

    fitted_w = int(image_w * scale)
    fitted_h = int(image_h * scale)

    picture.width = fitted_w
    picture.height = fitted_h
    picture.left = (
        side_margin
        + int((slide_w - fitted_w) / 2)
    )

    if top_align:
        picture.top = top_offset
    else:
        picture.top = (
            top_offset
            + int((slide_h - fitted_h) / 2)
        )



def _add_isambane_logo(slide) -> None:
    if not LOGO_PATH.exists():
        return

    slide.shapes.add_picture(
        str(LOGO_PATH),
        Inches(0.12),
        Inches(0.03),
        width=Inches(1.20),
    )


def _add_slide_heading(
    slide,
    title: str,
    *,
    subtitle: str = "",
    period_label: str = "",
    heading_style: str = "",
) -> None:
    detailed_heading = bool(
        subtitle
        or period_label
    )

    _add_isambane_logo(slide)

    if heading_style == "engineering":
        _add_text(
            slide,
            title,
            0.35,
            0.05,
            12.63,
            0.48,
            17,
            "FFF200",
            bold=True,
            align=PP_ALIGN.CENTER,
        )

        panel = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE,
            Inches(0.20),
            Inches(0.66),
            Inches(SLIDE_WIDTH - 0.40),
            Inches(SLIDE_HEIGHT - 0.76),
        )
        panel.fill.solid()
        panel.fill.fore_color.rgb = _rgb(WHITE)
        panel.line.fill.background()

        return

    if not detailed_heading:
        banner = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE,
            0,
            0,
            Inches(SLIDE_WIDTH),
            Inches(0.68),
        )
        banner.fill.solid()
        banner.fill.fore_color.rgb = _rgb("0F1F53")
        banner.line.fill.background()

        accent = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE,
            0,
            Inches(0.64),
            Inches(SLIDE_WIDTH),
            Inches(0.04),
        )
        accent.fill.solid()
        accent.fill.fore_color.rgb = _rgb(RED)
        accent.line.fill.background()

        _add_text(
            slide,
            title,
            0.35,
            0.08,
            12.63,
            0.48,
            20,
            WHITE,
            bold=True,
            align=PP_ALIGN.CENTER,
        )
        return

    _add_text(
        slide,
        title,
        0.35,
        0.04,
        12.63,
        0.48,
        17,
        "FFF200",
        bold=True,
        align=PP_ALIGN.CENTER,
    )

    panel = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        Inches(0.20),
        Inches(0.66),
        Inches(SLIDE_WIDTH - 0.40),
        Inches(SLIDE_HEIGHT - 0.76),
    )
    panel.fill.solid()
    panel.fill.fore_color.rgb = _rgb(WHITE)
    panel.line.fill.background()

    if subtitle:
        _add_text(
            slide,
            subtitle,
            0.55,
            0.78,
            12.23,
            0.42,
            16,
            "59647A",
            align=PP_ALIGN.CENTER,
            font_name="Calibri",
        )

    if period_label:
        _add_text(
            slide,
            period_label,
            0.75,
            1.16,
            3.00,
            0.34,
            14,
            BLACK,
            align=PP_ALIGN.LEFT,
            font_name="Arial",
        )


def _blank_slide(prs: Presentation):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = _rgb(BLACK)
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
    font_name: str | None = None,
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
    run.font.name = font_name or FONT_NAME
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