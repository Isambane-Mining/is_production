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
HEADING_YELLOW = "FFF200"

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

            # Dashboard captures already include the complete branded heading.
            if heading_style == "dashboard":
                _add_image_fitted(slide, image_bytes, top_inches=0,
                                  height_inches=SLIDE_HEIGHT, side_margin_inches=0)
                continue

            detailed_heading = bool(
                slide_subtitle
                or slide_period
            )

            engineering_heading = (
                heading_style
                == "engineering"
            )

            no_machines = captured.get("no_machines") is True

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

            # Maintain clearance below the enlarged header logo
            # without changing the captured chart or its values.
            safe_image_top = max(
                image_top,
                0.92 if engineering_heading else (
                    0.86 if hours_heading else image_top
                ),
            )

            _add_image_fitted(
                slide,
                (
                    _trim_engineering_image(image_bytes)
                    if engineering_heading
                    else image_bytes
                ),
                top_inches=safe_image_top,
                height_inches=(
                    SLIDE_HEIGHT
                    - safe_image_top
                    - (0.42 if hours_heading else image_bottom_margin)
                ),
                top_align=(
                    engineering_heading
                    or hours_heading
                ),
                side_margin_inches=(
                    0.22
                    if hours_heading
                    else (
                        0.10
                        if engineering_heading
                        else (
                            0.22
                            if detailed_heading
                            else 0.0
                        )
                    )
                ),
                stretch_to_fill=False,
            )

            # Hours Based only: standardise the presentation image area.
            # Keep the whole captured image visible with no cropping.
            # This intentionally expands short chart images vertically.
            if hours_heading:
                for picture in slide.shapes:
                    if (
                        picture.shape_type == 13
                        and picture.width > Inches(5)
                    ):
                        picture.left = Inches(0.22)
                        picture.top = Inches(0.92)
                        picture.width = Inches(12.89)
                        picture.height = Inches(5.95)

            # Engineering only: extend the picture downwards.
            # Preserve its width and horizontal position.
            # Leave clearance above the black footer.
            if engineering_heading and not hours_heading and not no_machines:
                for picture in slide.shapes:
                    if (
                        picture.shape_type == 13
                        and picture.width > Inches(5)
                    ):
                        picture.left = Inches(0.22)
                        picture.top = Inches(0.92)
                        picture.width = Inches(12.90)
                        picture.height = Inches(5.95)

            if engineering_heading and not hours_heading and no_machines:
                for picture in slide.shapes:
                    if (
                        picture.shape_type == 13
                        and picture.width > Inches(5)
                    ):
                        picture.left = Inches(0.22)
                        picture.top = Inches(0.92)
                        picture.width = Inches(12.90)


        if not prs.slides:
            _add_error_slide(
                prs,
                "The report images could not be decoded. "
                "Refresh the report and try the download again.",
            )


    # Final layout adjustments for exported slides.
    for slide_number, export_slide in enumerate(prs.slides, start=1):

        slide_text = " ".join(
            shape.text
            for shape in export_slide.shapes
            if shape.has_text_frame
        )

        # White slide number in the bottom-right black border.
        _add_text(
            export_slide,
            str(slide_number),
            12.48,
            7.30,
            0.45,
            0.16,
            10,
            WHITE,
            align=PP_ALIGN.RIGHT,
        )

    # Move decorative borders into the slide layout.
    # Layout shapes cannot be selected in normal PowerPoint editing.
    from copy import deepcopy

    border_positions = {
        (0, 0, Inches(SLIDE_WIDTH), Inches(0.80)),
        (0, Inches(7.30), Inches(SLIDE_WIDTH), Inches(0.20)),
        (0, Inches(0.80), Inches(0.20),
         Inches(SLIDE_HEIGHT - 0.80)),
    }

    def is_fixed_border(shape):
        return (
            shape.left,
            shape.top,
            shape.width,
            shape.height,
        ) in border_positions

    first_slide_borders = [
        shape for shape in prs.slides[0].shapes
        if is_fixed_border(shape)
    ]

    if first_slide_borders:
        if len(first_slide_borders) != 3:
            raise ValueError(
                "Expected exactly three black border shapes."
            )

        layout = prs.slide_layouts[6]

        for shape in first_slide_borders:
            copied = deepcopy(shape._element)

            # Assign a unique ID within the slide layout.
            copied.xpath(".//p:cNvPr")[0].set(
                "id", str(layout.shapes._next_shape_id)
            )

            layout.shapes._spTree.append(copied)

        # Remove original movable borders from individual slides.
        for export_slide in prs.slides:
            for shape in list(export_slide.shapes):
                if is_fixed_border(shape):
                    shape._element.getparent().remove(
                        shape._element
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
        decoded = base64.b64decode(text, validate=True)
    except (ValueError, binascii.Error):
        return None

    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(BytesIO(decoded)) as image:
            width, height = image.size

            if (
                width <= 0
                or height <= 0
                or width > 12000
                or height > 12000
                or width * height > 16000000
            ):
                raise ValueError(
                    "Presentation image dimensions exceed safety limits."
                )

            if image.format not in ("JPEG", "PNG"):
                raise ValueError(
                    "Unsupported presentation image format."
                )

            image.verify()

    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValueError("Invalid presentation image.") from None

    return decoded



def _trim_engineering_image(image_bytes: bytes) -> bytes:
    """Trim empty engineering-chart margins so the graph fills the slide better."""
    from PIL import Image

    try:
        image = Image.open(BytesIO(image_bytes)).convert("RGB")
        width, height = image.size

        if width < 100 or height < 100:
            return image_bytes

        pixels = image.load()

        # Use the top-right corner as the background reference because the
        # captured engineering report usually has a light plain background there.
        bg_r, bg_g, bg_b = pixels[max(0, width - 5), 5]

        def is_background(rgb):
            r, g, b = rgb
            return (
                abs(r - bg_r) <= 12
                and abs(g - bg_g) <= 12
                and abs(b - bg_b) <= 12
            )

        top = 0
        bottom = height - 1
        left = 0
        right = width - 1

        # Find top boundary.
        for y in range(height):
            non_bg = 0
            for x in range(width):
                if not is_background(pixels[x, y]):
                    non_bg += 1
            if non_bg > max(12, width * 0.01):
                top = y
                break

        # Find bottom boundary.
        for y in range(height - 1, -1, -1):
            non_bg = 0
            for x in range(width):
                if not is_background(pixels[x, y]):
                    non_bg += 1
            if non_bg > max(12, width * 0.01):
                bottom = y
                break

        # Find left boundary.
        for x in range(width):
            non_bg = 0
            for y in range(height):
                if not is_background(pixels[x, y]):
                    non_bg += 1
            if non_bg > max(8, height * 0.01):
                left = x
                break

        # Find right boundary.
        for x in range(width - 1, -1, -1):
            non_bg = 0
            for y in range(height):
                if not is_background(pixels[x, y]):
                    non_bg += 1
            if non_bg > max(8, height * 0.01):
                right = x
                break

        # Add safe padding so labels stay visible.
        pad_x = max(16, int(width * 0.025))
        pad_top = max(14, int(height * 0.02))
        pad_bottom = max(20, int(height * 0.03))

        left = max(0, left - pad_x)
        right = min(width, right + pad_x)
        top = max(0, top - pad_top)
        bottom = min(height, bottom + pad_bottom)

        cropped_width = right - left
        cropped_height = bottom - top

        # Reject obviously bad crops.
        if cropped_width < width * 0.55:
            return image_bytes

        if cropped_height < height * 0.22:
            return image_bytes

        cropped = image.crop((left, top, right, bottom))

        output = BytesIO()
        cropped.save(output, format="PNG")
        return output.getvalue()

    except (OSError, ValueError, IndexError):
        return image_bytes


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
        Inches(0.13),
        Inches(0.06),
        width=Inches(2.40),
        height=Inches(0.75),
    )


def _add_slide_heading(
    slide,
    title: str,
    *,
    subtitle: str = "",
    period_label: str = "",
    heading_style: str = "",
) -> None:
    detailed_heading = bool(subtitle or period_label)

    # Black header matching the reference PowerPoint.
    banner = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        0,
        0,
        Inches(SLIDE_WIDTH),
        Inches(0.80),
    )
    banner.fill.solid()
    banner.fill.fore_color.rgb = _rgb(BLACK)
    banner.line.fill.background()

    # The slide itself is white; no moveable white panel.
    # Keep a black bottom strip for slide numbers.

    footer = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        0,
        Inches(7.30),
        Inches(SLIDE_WIDTH),
        Inches(0.20),
    )
    footer.fill.solid()
    footer.fill.fore_color.rgb = _rgb(BLACK)
    footer.line.fill.background()

    # Black left border only; right side remains white.
    left_border = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        0,
        Inches(0.80),
        Inches(0.20),
        Inches(SLIDE_HEIGHT - 0.80),
    )
    left_border.fill.solid()
    left_border.fill.fore_color.rgb = _rgb(BLACK)
    left_border.line.fill.background()

    # White title in black header.
    _add_text(
        slide,
        title,
        2.70,
        0.10,
        10.20,
        0.48,
        17 if detailed_heading or heading_style == "engineering" else 20,
        HEADING_YELLOW,
        bold=True,
        align=PP_ALIGN.CENTER,
    )

    if subtitle:
        _add_text(
            slide,
            subtitle,
            0.55,
            0.82,
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

    # Add last so the logo is never hidden behind the header.
    _add_isambane_logo(slide)


def _blank_slide(prs: Presentation):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = _rgb(WHITE)
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