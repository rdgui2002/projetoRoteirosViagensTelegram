from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import requests


NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"


def _get_json(url: str, params: Dict[str, Any]) -> Any:
    headers = {"User-Agent": "ProjetoIAViagens/1.0"}
    resp = requests.get(url, params=params, headers=headers, timeout=25)
    resp.raise_for_status()
    return resp.json()


def geocode_city(city: str) -> Optional[Tuple[float, float]]:
    params = {
        "format": "json",
        "q": city,
        "limit": 1,
        "addressdetails": 0,
    }
    try:
        data = _get_json(NOMINATIM_URL, params)
        if not data:
            return None
        lat = float(data[0]["lat"])
        lon = float(data[0]["lon"])
        return lat, lon
    except Exception:
        logging.exception("OSM: erro no geocode")
        return None


def _overpass_query(query: str) -> Dict[str, Any]:
    headers = {"User-Agent": "ProjetoIAViagens/1.0"}
    resp = requests.post(OVERPASS_URL, data={"data": query}, headers=headers, timeout=40)
    resp.raise_for_status()
    return resp.json()


def _extract_names(elements: List[Dict[str, Any]], limit: int = 40) -> List[str]:
    seen = set()
    items: List[str] = []
    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name")
        if not name:
            continue
        if name in seen:
            continue
        seen.add(name)
        items.append(name)
        if len(items) >= limit:
            break
    return items


def get_city_items(city: str) -> Tuple[Dict[str, List[str]], str, str]:
    coords = geocode_city(city)
    if not coords:
        return {"see": [], "do": [], "eat": [], "drink": []}, city, "https://www.openstreetmap.org"

    lat, lon = coords
    radius = 15000

    see_query = f"""
[out:json][timeout:25];
(
  node(around:{radius},{lat},{lon})[tourism~"attraction|museum|gallery|viewpoint|zoo|theme_park"];
  node(around:{radius},{lat},{lon})[historic~"monument|memorial|castle|ruins|archaeological_site"];
  node(around:{radius},{lat},{lon})[man_made=bridge];
);
out tags;
"""

    do_query = f"""
[out:json][timeout:25];
(
  node(around:{radius},{lat},{lon})[leisure~"park|nature_reserve|beach_resort|garden"];
  node(around:{radius},{lat},{lon})[natural~"beach|peak|waterfall"];
  node(around:{radius},{lat},{lon})[amenity~"theatre|arts_centre|cinema"];
);
out tags;
"""

    eat_query = f"""
[out:json][timeout:25];
(
  node(around:{radius},{lat},{lon})[amenity~"restaurant|cafe|food_court"];
);
out tags;
"""

    drink_query = f"""
[out:json][timeout:25];
(
  node(around:{radius},{lat},{lon})[amenity~"bar|pub|nightclub"];
);
out tags;
"""

    try:
        see = _extract_names(_overpass_query(see_query).get("elements", []), limit=25)
        do = _extract_names(_overpass_query(do_query).get("elements", []), limit=25)
        eat = _extract_names(_overpass_query(eat_query).get("elements", []), limit=25)
        drink = _extract_names(_overpass_query(drink_query).get("elements", []), limit=25)
    except Exception:
        logging.exception("OSM: erro na consulta Overpass")
        return {"see": [], "do": [], "eat": [], "drink": []}, city, "https://www.openstreetmap.org"

    return {"see": see, "do": do, "eat": eat, "drink": drink}, city, "https://www.openstreetmap.org"
