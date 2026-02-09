from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, List, Optional

import re
import httpx
from bs4 import BeautifulSoup

from services.utils import parse_date_br

RITMOS_VALIDOS = ["leve", "médio", "intenso"]


@dataclass
class TripPreferences:
    nome: str
    destino: str
    data_ida: str
    data_volta: str
    ritmo: str


class DestinationIsCountryError(ValueError):
    """Quando o usuário digita um país/região ao invés de uma cidade."""
    pass


def _clean_space(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _strip_coords(s: str) -> str:
    return re.sub(r"^\s*-?\d{1,3}\.\d+\s+-?\d{1,3}\.\d+\s+\d+\s+", "", s).strip()


def _strip_update(s: str) -> str:
    return re.sub(r"\(\s*updated.*?\)\s*", "", s, flags=re.IGNORECASE).strip()


def _shorten(s: str, max_len: int = 190) -> str:
    s = _clean_space(s)
    if len(s) <= max_len:
        return s
    cut = s[:max_len].rsplit(" ", 1)[0]
    return cut + "…"


async def _mw_get(lang: str, params: Dict[str, Any]) -> Dict[str, Any]:
    api = f"https://{lang}.wikivoyage.org/w/api.php"
    headers = {"User-Agent": "TravelPlannerBot/0.3"}
    async with httpx.AsyncClient(timeout=30, headers=headers) as client:
        r = await client.get(api, params=params)
        r.raise_for_status()
        return r.json()


async def _resolve_title(lang: str, query: str) -> Optional[str]:
    """
    Resolve título com segurança:
    1) tenta título exato
    2) search top 5 e escolhe melhor
    """
    q = (query or "").strip()
    if not q:
        return None

    # 1) título exato
    data = await _mw_get(lang, {"action": "query", "format": "json", "titles": q})
    pages = (data.get("query", {}).get("pages") or {})
    for _, page in pages.items():
        if "missing" not in page:
            return page.get("title")

    # 2) search
    data = await _mw_get(lang, {
        "action": "query",
        "format": "json",
        "list": "search",
        "srsearch": q,
        "srlimit": 5,
        "srprop": "",
    })
    results = (data.get("query", {}).get("search") or [])
    if not results:
        return None

    qlow = q.lower()

    # Preferir: contém query e não tem parênteses
    for r in results:
        t = (r.get("title") or "")
        if qlow in t.lower() and "(" not in t:
            return t

    return results[0].get("title")


async def _get_langlink_title(src_lang: str, title: str, target_lang: str = "pt") -> Optional[str]:
    data = await _mw_get(src_lang, {
        "action": "query",
        "format": "json",
        "prop": "langlinks",
        "titles": title,
        "lllang": target_lang,
        "lllimit": 1,
    })
    pages = (data.get("query", {}).get("pages") or {})
    for _, page in pages.items():
        lls = page.get("langlinks") or []
        if lls:
            return lls[0].get("*")
    return None


async def detect_country_destination(destino: str) -> Optional[str]:
    """
    Detecta país/região sem confundir cidade.
    Heurística: tem "cidades/regiões/outros destinos" e pouco "ver/fazer".
    """
    q = (destino or "").strip()
    if not q:
        return None

    for lang in ("pt", "en"):
        title = await _resolve_title(lang, q)
        if not title:
            continue

        data = await _mw_get(lang, {
            "action": "parse",
            "format": "json",
            "page": title,
            "prop": "sections",
        })
        sections = data.get("parse", {}).get("sections", []) or []
        lines = [(s.get("line") or "").strip().lower() for s in sections]

        country_signals = ["cidades", "cities", "regiões", "regions", "outros destinos", "other destinations"]
        attraction_signals = ["ver", "see", "fazer", "do", "atrações", "attractions", "pontos turísticos"]

        country_hits = sum(1 for sig in country_signals if any(sig in ln for ln in lines))
        attraction_hits = sum(1 for sig in attraction_signals if any(sig in ln for ln in lines))

        if country_hits >= 2 and attraction_hits <= 1:
            return (
                f"🌍 {title} parece ser um país/região.\n"
                "Pra eu montar um roteiro dia a dia de verdade, me diga uma CIDADE.\n\n"
                "Exemplos:\n"
                "• Cairo\n"
                "• Barcelona\n"
                "• Rio de Janeiro"
            )

    return None


def _is_top_level_section(sec: Dict[str, Any]) -> bool:
    try:
        return int(sec.get("toclevel", 999)) == 1
    except Exception:
        return False


async def _find_section_indexes(lang: str, title: str) -> Dict[str, str]:
    """
    Mais flexível: usa 'contém', não igualdade.
    """
    data = await _mw_get(lang, {
        "action": "parse",
        "format": "json",
        "page": title,
        "prop": "sections",
    })
    sections = data.get("parse", {}).get("sections", []) or []

    want: Dict[str, Optional[str]] = {"see": None, "do": None, "eat": None, "drink": None}

    for s in sections:
        if not _is_top_level_section(s):
            continue

        line = (s.get("line") or "").strip().lower()
        idx = s.get("index")

        # EN
        if "see" in line and want["see"] is None:
            want["see"] = idx
        if "do" in line and want["do"] is None:
            want["do"] = idx
        if "eat" in line and want["eat"] is None:
            want["eat"] = idx
        if "drink" in line and want["drink"] is None:
            want["drink"] = idx

        # PT
        if "ver" in line and want["see"] is None:
            want["see"] = idx
        if "fazer" in line and want["do"] is None:
            want["do"] = idx
        if "comer" in line and want["eat"] is None:
            want["eat"] = idx
        if "beber" in line and want["drink"] is None:
            want["drink"] = idx

    return {k: v for k, v in want.items() if v is not None}


async def _fetch_section_html(lang: str, title: str, section_index: str) -> str:
    data = await _mw_get(lang, {
        "action": "parse",
        "format": "json",
        "page": title,
        "prop": "text",
        "section": section_index,
    })
    return data.get("parse", {}).get("text", {}).get("*", "") or ""


def _extract_items_from_html(html: str, limit: int = 70) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    items: List[str] = []

    # listings (melhor)
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
            items.append(_shorten(raw, 200))
        elif name:
            raw = _strip_coords(_strip_update(name))
            items.append(_shorten(raw, 120))

    # fallback UL/LI
    if len(items) < 12:
        for li in soup.select("ul > li"):
            txt = li.get_text(" ", strip=True)
            txt = _strip_coords(_strip_update(txt))
            txt = _shorten(txt, 200)
            if txt:
                items.append(txt)

    # dedup + limit
    out: List[str] = []
    seen = set()
    for it in items:
        it = _clean_space(it)
        k = it.lower()
        if it and k not in seen:
            seen.add(k)
            out.append(it)
        if len(out) >= limit:
            break
    return out


_GENERIC_DAYTRIP_WORDS = [
    "bate-volta", "day trip", "excursão", "excursion",
    "arredores", "surroundings", "próximo", "nearby",
    "fora da cidade", "outside the city",
    "outras cidades", "other cities",
]

def _looks_like_daytrip_or_outside(item: str) -> bool:
    low = item.lower()
    return any(w in low for w in _GENERIC_DAYTRIP_WORDS)

def _destination_specific_blacklist(destino: str) -> List[str]:
    d = destino.lower()
    if "rio de janeiro" in d or d in ("rio", "rj"):
        return [
            "angra", "ilha grande", "paraty", "mangaratiba", "buzios", "búzios",
            "arraial do cabo", "cabo frio", "niterói", "petrópolis", "teresópolis",
        ]
    return []

def _filter_items_for_city(items: List[str], destino: str) -> List[str]:
    blacklist = _destination_specific_blacklist(destino)

    tourist_keywords = (
        "praia", "beach", "parque", "park", "museu", "museum", "mirante",
        "cristo", "redentor", "pão de açúcar", "jardim", "botânico",
        "escadaria", "lapa", "ipanema", "copacabana", "leblon", "centro", "bairro",
        "catedral", "templo", "palácio", "forte", "estádio", "teatro",
        "mercado", "feira", "avenida", "boulevard",
    )

    out = []
    for it in items:
        low = it.lower()

        if _looks_like_daytrip_or_outside(it):
            continue
        if any(b in low for b in blacklist):
            continue

        if len(it) <= 25 and not any(k in low for k in tourist_keywords):
            keep_short = any(x in low for x in ["copacabana", "ipanema", "leblon", "lapa", "centro"])
            if not keep_short:
                continue

        out.append(it)

    final = []
    seen = set()
    for it in out:
        k = it.lower()
        if k not in seen:
            seen.add(k)
            final.append(it)
    return final


def _best_food_item(eat: List[str], drink: List[str], fallback_lists: List[List[str]], used: set) -> Optional[str]:
    def pick(arr):
        for x in arr:
            k = x.lower()
            if k not in used:
                used.add(k)
                return x
        return None

    x = pick(eat)
    if x:
        return x
    x = pick(drink)
    if x:
        return x

    keywords = (
        "restaurant", "restaurante", "bar", "café", "cafe",
        "tapas", "pizza", "burger", "beer", "wine",
        "bistro", "pub", "boteco", "rodízio", "rodizio",
        "churr", "steak",
    )
    for arr in fallback_lists:
        for item in arr:
            low = item.lower()
            if any(k in low for k in keywords) and low not in used:
                used.add(low)
                return item
    return None


def _build_day_by_day(categories: Dict[str, List[str]], ida, volta, ritmo: str) -> List[Dict[str, Any]]:
    see = categories.get("see", [])
    do = categories.get("do", [])
    eat = categories.get("eat", [])
    drink = categories.get("drink", [])

    days = max(1, (volta - ida).days)
    blocks = 2 if ritmo == "leve" else 3 if ritmo == "médio" else 4
    used = set()

    def pick_unique(arr: List[str]) -> Optional[str]:
        for x in arr:
            k = x.lower()
            if k not in used:
                used.add(k)
                return x
        return None

    roteiro: List[Dict[str, Any]] = []
    from datetime import timedelta

    for i in range(days):
        d = ida + timedelta(days=i)
        plan = {"dia": i + 1, "data": d.strftime("%d/%m/%Y"), "atividades": []}

        a = pick_unique(see) or pick_unique(do)
        if a:
            plan["atividades"].append(("Manhã", a))

        if blocks >= 2:
            b = pick_unique(do) or pick_unique(see)
            if b:
                plan["atividades"].append(("Tarde", b))

        if blocks >= 3:
            c = _best_food_item(eat, drink, [do, see], used)
            if c:
                plan["atividades"].append(("Noite", c))

        if blocks >= 4:
            e = pick_unique(see) or pick_unique(do)
            if e:
                plan["atividades"].append(("Extra", e))

        roteiro.append(plan)

    return roteiro


async def build_itinerary_from_wikivoyage(prefs: TripPreferences) -> Dict[str, Any]:
    if prefs.ritmo not in RITMOS_VALIDOS:
        raise ValueError("Ritmo inválido. Use: leve / médio / intenso")

    ida = parse_date_br(prefs.data_ida)
    volta = parse_date_br(prefs.data_volta)
    if volta <= ida:
        raise ValueError("A data de volta precisa ser depois da ida.")

    query = prefs.destino.strip()
    categories: Dict[str, List[str]] = {}

    msg_country = await detect_country_destination(query)
    if msg_country:
        raise DestinationIsCountryError(msg_country)

    # 1) PT
    pt_title = await _resolve_title("pt", query)
    if pt_title:
        idxs = await _find_section_indexes("pt", pt_title)
        if idxs:
            sections_html = {k: await _fetch_section_html("pt", pt_title, v) for k, v in idxs.items()}
            for k, html in sections_html.items():
                categories[k] = _extract_items_from_html(html, limit=70)

            categories["see"] = _filter_items_for_city(categories.get("see", []), pt_title)
            categories["do"] = _filter_items_for_city(categories.get("do", []), pt_title)
            categories["eat"] = _filter_items_for_city(categories.get("eat", []), pt_title)
            categories["drink"] = _filter_items_for_city(categories.get("drink", []), pt_title)

            roteiro = _build_day_by_day(categories, ida, volta, prefs.ritmo)
            return {
                "nome": prefs.nome,
                "destino": pt_title,
                "ida": prefs.data_ida,
                "volta": prefs.data_volta,
                "ritmo": prefs.ritmo,
                "lang": "pt",
                "url": f"https://pt.wikivoyage.org/wiki/{pt_title.replace(' ', '_')}",
                "roteiro": roteiro,
                "categories": categories,
            }

    # 2) EN fallback
    en_title = await _resolve_title("en", query) or query
    idxs = await _find_section_indexes("en", en_title)
    if not idxs:
        raise ValueError("Não consegui achar seções de roteiro para esse destino. Tenta outra cidade.")

    sections_html = {k: await _fetch_section_html("en", en_title, v) for k, v in idxs.items()}
    for k, html in sections_html.items():
        categories[k] = _extract_items_from_html(html, limit=70)

    pt_from_en = await _get_langlink_title("en", en_title, target_lang="pt")
    display_dest = pt_from_en or en_title

    categories["see"] = _filter_items_for_city(categories.get("see", []), display_dest)
    categories["do"] = _filter_items_for_city(categories.get("do", []), display_dest)
    categories["eat"] = _filter_items_for_city(categories.get("eat", []), display_dest)
    categories["drink"] = _filter_items_for_city(categories.get("drink", []), display_dest)

    roteiro = _build_day_by_day(categories, ida, volta, prefs.ritmo)
    return {
        "nome": prefs.nome,
        "destino": display_dest,
        "ida": prefs.data_ida,
        "volta": prefs.data_volta,
        "ritmo": prefs.ritmo,
        "lang": "en",
        "url": f"https://en.wikivoyage.org/wiki/{en_title.replace(' ', '_')}",
        "roteiro": roteiro,
        "categories": categories,
    }
