from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path
from typing import List

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfgen import canvas


LEFT_MARGIN = 50
RIGHT_MARGIN = 50
TOP_MARGIN = 64
BOTTOM_MARGIN = 52
WATERMARK_IMAGE_ENV = "PDF_WATERMARK_IMAGE_PATH"
WATERMARK_CANDIDATES = (
    "assets/icone.png",
    "assets/watermark.png",
    "assets/logo_mark.png",
    "assets/telegram_bo_ai.png",
)


def _resolve_watermark_image_path() -> str:
    env_path = (os.getenv(WATERMARK_IMAGE_ENV) or "").strip()
    if env_path:
        p = Path(env_path)
        if p.is_file():
            return str(p)

    for candidate in WATERMARK_CANDIDATES:
        p = Path(candidate)
        if p.is_file():
            return str(p)
    return ""


def _draw_logo_mark(c: canvas.Canvas, width: float, height: float) -> None:
    image_path = _resolve_watermark_image_path()
    if not image_path:
        return

    try:
        image = ImageReader(image_path)
    except Exception:
        return

    side = 24
    x = 44
    y = 34
    c.saveState()
    c.drawImage(
        image,
        x,
        y,
        width=side,
        height=side,
        preserveAspectRatio=True,
        mask="auto",
    )
    c.restoreState()


def _draw_frame(c: canvas.Canvas, width: float, height: float, page_number: int) -> None:
    c.saveState()
    c.setStrokeColor(colors.HexColor("#c6d3e6"))
    c.setLineWidth(1.2)
    c.rect(26, 26, width - 52, height - 52, stroke=1, fill=0)

    c.setStrokeColor(colors.HexColor("#e5ecf6"))
    c.setLineWidth(0.9)
    c.rect(34, 34, width - 68, height - 68, stroke=1, fill=0)

    c.setStrokeColor(colors.HexColor("#bac8dc"))
    c.setLineWidth(0.8)
    c.line(LEFT_MARGIN, height - 92, width - RIGHT_MARGIN, height - 92)

    c.setFont("Helvetica", 8)
    c.setFillColor(colors.HexColor("#7a8798"))
    c.drawRightString(width - RIGHT_MARGIN, 38, f"Pagina {page_number}")
    c.restoreState()


def _draw_header(c: canvas.Canvas, title: str, subtitle: str, width: float, height: float) -> float:
    y = height - TOP_MARGIN
    max_width = width - LEFT_MARGIN - RIGHT_MARGIN

    c.setFillColor(colors.HexColor("#0f172a"))
    c.setFont("Helvetica-Bold", 16)
    for part in simpleSplit((title or "").strip(), "Helvetica-Bold", 16, max_width)[:2]:
        c.drawString(LEFT_MARGIN, y, part)
        y -= 18

    c.setFillColor(colors.HexColor("#334155"))
    c.setFont("Helvetica", 10)
    for part in simpleSplit((subtitle or "").strip(), "Helvetica", 10, max_width)[:2]:
        c.drawString(LEFT_MARGIN, y, part)
        y -= 14

    y -= 2
    c.setStrokeColor(colors.HexColor("#d7e1f0"))
    c.setLineWidth(0.8)
    c.line(LEFT_MARGIN, y, width - RIGHT_MARGIN, y)
    return y - 14


def build_pdf_bytes(title: str, subtitle: str, lines: List[str]) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4

    page_number = 1
    _draw_logo_mark(c, width, height)
    _draw_frame(c, width, height, page_number)
    y = _draw_header(c, title, subtitle, width, height)

    max_width = width - LEFT_MARGIN - RIGHT_MARGIN

    def start_new_page() -> float:
        nonlocal page_number
        c.showPage()
        page_number += 1
        _draw_logo_mark(c, width, height)
        _draw_frame(c, width, height, page_number)
        return _draw_header(c, title, subtitle, width, height)

    for raw in lines:
        line = str(raw or "")
        stripped = line.strip()

        if not stripped:
            y -= 8
            if y < BOTTOM_MARGIN + 18:
                y = start_new_page()
            continue

        font_name = "Helvetica"
        font_size = 10
        color = colors.HexColor("#0f172a")

        if stripped.startswith("Dia ") or stripped.startswith("Eventos"):
            font_name = "Helvetica-Bold"
            font_size = 11
        elif stripped.startswith("Destino:") or stripped.startswith("Datas:") or stripped.startswith("Ritmo:"):
            font_name = "Helvetica-Bold"
            font_size = 10.5

        wrapped = simpleSplit(line, font_name, font_size, max_width)
        for part in wrapped:
            if y < BOTTOM_MARGIN + 16:
                y = start_new_page()
            c.setFont(font_name, font_size)
            c.setFillColor(color)
            c.drawString(LEFT_MARGIN, y, part)
            y -= 13 if font_size <= 10 else 14

        if stripped.startswith("Dia "):
            y -= 2

    c.save()
    return buf.getvalue()
