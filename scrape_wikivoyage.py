import json
import time
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "TravelPlannerBot/0.1 (contact: youremail@example.com)"
}

API = "https://pt.wikivoyage.org/w/api.php"

def api_get(params: dict, sleep_s: float = 1.0) -> dict:
    time.sleep(sleep_s)
    r = requests.get(API, params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()

def find_section_indexes(title: str):
    """
    Pega as seções via MediaWiki API e acha índices de:
    ver / fazer / comer / beber (inclusive combinações, etc.)
    """
    data = api_get({
        "action": "parse",
        "format": "json",
        "page": title,
        "prop": "sections"
    })

    sections = data.get("parse", {}).get("sections", [])
    if not sections:
        return {}

    want = {"see": None, "do": None, "eat": None, "drink": None}

    for s in sections:
        line = (s.get("line") or "").strip().lower()
        idx = s.get("index")

        # match por "contém" (pega "ver e fazer", etc.)
        if ("ver" in line or "veja" in line) and want["see"] is None:
            want["see"] = idx
        if ("fazer" in line or "faça" in line) and want["do"] is None:
            want["do"] = idx
        if "comer" in line and want["eat"] is None:
            want["eat"] = idx
        if "beber" in line and want["drink"] is None:
            want["drink"] = idx

    return {k: v for k, v in want.items() if v is not None}

def extract_items_from_section_html(html: str, limit: int = 20):
    soup = BeautifulSoup(html, "lxml")

    items = []

    # 1) listings (formato comum do Wikivoyage)
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
            items.append(f"{name} — {desc}")
        elif name:
            items.append(name)

    # 2) fallback: listas normais
    if len(items) < 8:
        for li in soup.select("ul > li"):
            txt = li.get_text(" ", strip=True)
            if txt:
                items.append(txt)

    # dedup + limit
    out = []
    seen = set()
    for it in items:
        it = " ".join(it.split())
        if it and it not in seen:
            seen.add(it)
            out.append(it)
        if len(out) >= limit:
            break
    return out

def fetch_section_html(title: str, section_index: str):
    data = api_get({
        "action": "parse",
        "format": "json",
        "page": title,
        "prop": "text",
        "section": section_index
    })
    html = data.get("parse", {}).get("text", {}).get("*", "")
    return html

def scrape_city(title: str):
    idxs = find_section_indexes(title)

    categories = {}
    for key, idx in idxs.items():
        html = fetch_section_html(title, idx)
        items = extract_items_from_section_html(html, limit=20)
        if items:
            categories[key] = items

    return {
        "source": "wikivoyage_mediawiki_api",
        "title": title,
        "url": f"https://pt.wikivoyage.org/wiki/{title.replace(' ', '_')}",
        "categories": categories,
    }

if __name__ == "__main__":
    out = scrape_city("Barcelona")
    print(json.dumps(out, ensure_ascii=False, indent=2))
