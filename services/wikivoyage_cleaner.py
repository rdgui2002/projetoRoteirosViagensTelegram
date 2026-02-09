# services/wikivoyage_cleaner.py
from __future__ import annotations

import re
from bs4 import BeautifulSoup
from typing import Dict, Any, List

def _clean_space(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def _strip_coords(s: str) -> str:
    return re.sub(r"^\s*-?\d{1,3}\.\d+\s+-?\d{1,3}\.\d+\s+\d+\s+", "", s).strip()

def _strip_update(s: str) -> str:
    return re.sub(r"\(\s*updated.*?\)\s*", "", s, flags=re.IGNORECASE).strip()

def _shorten(s: str, max_len: int = 160) -> str:
    s = _clean_space(s)
    if len(s) <= max_len:
        return s
    cut = s[:max_len].rsplit(" ", 1)[0]
    return cut + "…"

def extract_items_from_section_html(html: str, limit: int = 20) -> List[str]:
    soup = BeautifulSoup(html, "lxml")
    items: List[str] = []

    # listings
    for listing in soup.select("div.listing, div.vcard"):
        name_el = (
            listing.select_one(".listing-name .fn.org")
            or listing.select_one(".listing-name .fn")
            or listing.select_one(".listing-name b")
            or listing.select_one(".listing-name a")
        )
        name = name_el.get_text(" ", strip=True) if name_el else None

        desc_el = listing.select_one(".listing-content, .listing-description")
        desc = desc_el.get_text(" ", strip=True) if desc_el else None

        if name and desc:
            raw = f"{name} — {desc}"
            raw = _strip_coords(_strip_update(raw))
            items.append(_shorten(raw, 170))
        elif name:
            raw = _strip_coords(_strip_update(name))
            items.append(_shorten(raw, 80))

    # fallback ul/li
    if len(items) < 8:
        for li in soup.select("ul > li"):
            txt = li.get_text(" ", strip=True)
            txt = _strip_coords(_strip_update(txt))
            txt = _shorten(txt, 170)
            if txt:
                items.append(txt)

    # dedup
    out = []
    seen = set()
    for it in items:
        it = _clean_space(it)
        if it and it.lower() not in seen:
            seen.add(it.lower())
            out.append(it)
        if len(out) >= limit:
            break
    return out

def clean_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    sections_html = payload.get("sections_html", {}) or {}
    categories = {}
    for key, html in sections_html.items():
        categories[key] = extract_items_from_section_html(html, limit=20)
    payload["categories"] = categories  # see/do/eat/drink -> [strings]
    return payload
