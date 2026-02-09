from __future__ import annotations

from datetime import datetime


def parse_date_br(text: str):
    """
    Aceita dd/mm/aaaa.
    Retorna datetime.date
    """
    t = (text or "").strip()
    dt = datetime.strptime(t, "%d/%m/%Y")
    return dt.date()
