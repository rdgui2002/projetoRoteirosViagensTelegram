from __future__ import annotations

from io import BytesIO
from typing import List

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


def build_pdf_bytes(title: str, subtitle: str, lines: List[str]) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4

    y = height - 60
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, y, title[:90])
    y -= 22

    c.setFont("Helvetica", 11)
    c.drawString(50, y, subtitle[:120])
    y -= 26

    c.setFont("Helvetica", 10)
    for line in lines:
        if y < 60:
            c.showPage()
            y = height - 60
            c.setFont("Helvetica", 10)
        c.drawString(50, y, str(line)[:140])
        y -= 14

    c.save()
    return buf.getvalue()
