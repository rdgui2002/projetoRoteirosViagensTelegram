from __future__ import annotations

import datetime as dt

from services.utils import normalize_key


def _city_to_iata(city: str) -> str | None:
    if not city:
        return None
    c = city.strip()
    if len(c) == 3 and c.isalpha():
        return c.upper()
    mapping = {
        "rio de janeiro": "RIO",
        "sao paulo": "SAO",
        "florianopolis": "FLN",
        "salvador": "SSA",
        "belem": "BEL",
    }
    return mapping.get(normalize_key(c))


def _origm_fallback(text: str) -> str | None:
    t = normalize_key(text)
    if not t:
        return None
    return t.upper()[:3]


def build_flight_link(origem: str, destino: str, ida: dt.date, volta: dt.date) -> str:
    o = _city_to_iata(origem) or _origm_fallback(origem)
    d = _city_to_iata(destino) or _origm_fallback(destino)
    if not o or not d:
        return "indisponível"
    return (
        "https://www.google.com/travel/flights?q="
        f"Flights%20to%20{d}%20from%20{o}%20on%20{ida.isoformat()}%20through%20{volta.isoformat()}"
    )


def build_hotel_link(destino: str, checkin: dt.date, checkout: dt.date) -> str:
    dest = destino.replace(" ", "%20")
    return (
        "https://www.booking.com/searchresults.html?"
        f"ss={dest}&checkin={checkin.isoformat()}&checkout={checkout.isoformat()}"
    )
