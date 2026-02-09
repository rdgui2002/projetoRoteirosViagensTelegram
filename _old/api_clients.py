from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Any

import requests

from services.utils import normalize_key

AMADEUS_TOKEN: str | None = None
AMADEUS_TOKEN_EXPIRES_AT = 0.0
HB_DEST_CACHE: dict[str, str] = {}
HB_HOTELS_CACHE: dict[str, list[str]] = {}


def _api_unavailable(provider: str, summary: str) -> dict[str, Any]:
    return {
        "provider": provider,
        "best_option": {
            "provider": provider,
            "summary": summary,
            "price_total": None,
            "currency": "BRL",
            "notes": "API indisponível ou sem dados.",
        },
    }


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


def _amadeus_get_token() -> str | None:
    global AMADEUS_TOKEN, AMADEUS_TOKEN_EXPIRES_AT
    now = time.time()
    if AMADEUS_TOKEN and now < AMADEUS_TOKEN_EXPIRES_AT - 30:
        return AMADEUS_TOKEN

    client_id = os.getenv("AMADEUS_CLIENT_ID")
    client_secret = os.getenv("AMADEUS_CLIENT_SECRET")
    if not client_id or not client_secret:
        logging.warning("Amadeus: credenciais ausentes")
        return None

    base = os.getenv("AMADEUS_BASE_URL", "https://test.api.amadeus.com")
    url = f"{base}/v1/security/oauth2/token"
    try:
        resp = requests.post(
            url,
            data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        AMADEUS_TOKEN = data.get("access_token")
        expires_in = int(data.get("expires_in", 1799))
        AMADEUS_TOKEN_EXPIRES_AT = now + expires_in
        logging.info("Amadeus: token obtido")
        return AMADEUS_TOKEN
    except Exception:
        logging.exception("Amadeus: falha ao obter token")
        return None


def search_flights(origem: str, destino: str, ida, volta) -> dict[str, Any]:
    token = _amadeus_get_token()
    orig_iata = _city_to_iata(origem)
    dest_iata = _city_to_iata(destino)
    if not token or not orig_iata or not dest_iata:
        return _api_unavailable("amadeus", f"{origem.title()} <-> {destino.title()} (Amadeus)")

    base = os.getenv("AMADEUS_BASE_URL", "https://test.api.amadeus.com")
    url = f"{base}/v2/shopping/flight-offers"
    params = {
        "originLocationCode": orig_iata,
        "destinationLocationCode": dest_iata,
        "departureDate": ida.isoformat(),
        "returnDate": volta.isoformat(),
        "adults": 1,
        "currencyCode": "BRL",
        "max": 10,
    }
    try:
        logging.info("Amadeus: buscando voos %s -> %s %s/%s", orig_iata, dest_iata, ida, volta)
        resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        offers = data.get("data", [])
        if not offers:
            logging.warning("Amadeus: sem ofertas")
            return _api_unavailable("amadeus", f"{origem.title()} <-> {destino.title()} (Amadeus)")

        best_price = None
        for offer in offers:
            price = offer.get("price", {}).get("grandTotal")
            if price is None:
                continue
            try:
                price_f = float(price)
            except Exception:
                continue
            if best_price is None or price_f < best_price:
                best_price = price_f

        if best_price is None:
            logging.warning("Amadeus: ofertas sem preço")
            return _api_unavailable("amadeus", f"{origem.title()} <-> {destino.title()} (Amadeus)")

        currency = offers[0].get("price", {}).get("currency", "BRL")
        return {
            "provider": "amadeus",
            "best_option": {
                "provider": "amadeus",
                "summary": f"{orig_iata} <-> {dest_iata} (Amadeus)",
                "price_total": float(best_price),
                "currency": currency,
                "notes": "Oferta real via Amadeus.",
            },
        }
    except Exception:
        logging.exception("Amadeus: erro ao buscar ofertas")
        return _api_unavailable("amadeus", f"{origem.title()} <-> {destino.title()} (Amadeus)")


def _hb_headers() -> dict[str, str] | None:
    api_key = os.getenv("HOTELBEDS_HOTEL_API_KEY") or os.getenv("APIHOTELBEDSHOTELAPI")
    secret = os.getenv("HOTELBEDS_HOTEL_API_SECRET") or os.getenv("HOTELBEDS_SECRET")
    if not api_key or not secret:
        logging.warning("Hotelbeds: credenciais ausentes")
        return None
    timestamp = str(int(time.time()))
    signature = hashlib.sha256((api_key + secret + timestamp).encode("utf-8")).hexdigest()
    return {"Api-key": api_key, "X-Signature": signature}


def _hb_base_url() -> str:
    return os.getenv("HOTELBEDS_BASE_URL", "https://api.test.hotelbeds.com")


def _hb_get_destination_code(city: str) -> str | None:
    key = normalize_key(city)
    if key in HB_DEST_CACHE:
        return HB_DEST_CACHE[key]

    headers = _hb_headers()
    if not headers:
        return None

    base = _hb_base_url()
    page_from = 1
    page_size = 100
    while page_from <= 1000:
        page_to = page_from + page_size - 1
        url = f"{base}/hotel-content-api/1.0/locations/destinations"
        params = {
            "fields": "all",
            "language": "ENG",
            "from": page_from,
            "to": page_to,
            "countryCodes": "BR",
        }
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            destinations = data.get("destinations") or data.get("destination") or data.get("data") or data
            if isinstance(destinations, dict) and "destinations" in destinations:
                destinations = destinations.get("destinations", [])
            if not isinstance(destinations, list):
                destinations = []
            for d in destinations:
                name = normalize_key(d.get("name", ""))
                code = d.get("code")
                if code and name and (key == name or key in name or name in key):
                    HB_DEST_CACHE[key] = code
                    return code
            if len(destinations) < page_size:
                break
        except Exception:
            logging.exception("Hotelbeds: erro ao buscar destinos")
            break
        page_from += page_size
    return None


def _hb_get_hotels(dest_code: str) -> list[str]:
    if dest_code in HB_HOTELS_CACHE:
        return HB_HOTELS_CACHE[dest_code]

    headers = _hb_headers()
    if not headers:
        return []

    base = _hb_base_url()
    url = f"{base}/hotel-content-api/1.0/hotels"
    params = {
        "fields": "basic",
        "language": "ENG",
        "from": 1,
        "to": 100,
        "destinationCode": dest_code,
    }
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        hotels_block = data.get("hotels") or data
        hotels_list = hotels_block.get("hotels") if isinstance(hotels_block, dict) else hotels_block
        if not isinstance(hotels_list, list):
            hotels_list = []
        codes = [h.get("code") for h in hotels_list if h.get("code")]
        HB_HOTELS_CACHE[dest_code] = codes
        return codes
    except Exception:
        logging.exception("Hotelbeds: erro ao buscar hotéis")
        return []


def search_hotels(destino: str, checkin, checkout, ritmo: str, orcamento) -> dict[str, Any]:
    headers = _hb_headers()
    if not headers:
        return _api_unavailable("hotelbeds", f"Hotel em {destino.title()} (Hotelbeds)")

    dest_code = _hb_get_destination_code(destino)
    if not dest_code:
        return _api_unavailable("hotelbeds", f"Hotel em {destino.title()} (Hotelbeds)")

    hotel_codes = _hb_get_hotels(dest_code)
    if not hotel_codes:
        return _api_unavailable("hotelbeds", f"Hotel em {destino.title()} (Hotelbeds)")

    base = _hb_base_url()
    url = f"{base}/hotel-api/1.0/hotels"
    payload = {
        "stay": {"checkIn": checkin.isoformat(), "checkOut": checkout.isoformat()},
        "occupancies": [{"rooms": 1, "adults": 2, "children": 0}],
        "hotels": {"hotel": hotel_codes[:30]},
    }
    try:
        logging.info("Hotelbeds: buscando hotéis %s %s/%s", dest_code, checkin, checkout)
        resp = requests.post(url, headers={**headers, "Content-Type": "application/json"}, json=payload, timeout=25)
        resp.raise_for_status()
        data = resp.json()
        hotels = data.get("hotels", {}).get("hotels", [])
        best_price = None
        for h in hotels:
            for room in h.get("rooms", []):
                for rate in room.get("rates", []):
                    price = rate.get("net") or rate.get("sellingRate")
                    if price is None:
                        continue
                    try:
                        price_f = float(price)
                    except Exception:
                        continue
                    if best_price is None or price_f < best_price:
                        best_price = price_f
        if best_price is None:
            logging.warning("Hotelbeds: sem preço")
            return _api_unavailable("hotelbeds", f"Hotel em {destino.title()} (Hotelbeds)")

        noites = max(1, (checkout - checkin).days)
        nightly = best_price / max(1, noites)
        return {
            "provider": "hotelbeds",
            "best_option": {
                "provider": "hotelbeds",
                "summary": f"Hotel em {destino.title()} (Hotelbeds)",
                "price_total": float(best_price),
                "nights": noites,
                "nightly": float(nightly),
                "currency": "BRL",
                "notes": "Oferta real via Hotelbeds.",
            },
        }
    except Exception:
        logging.exception("Hotelbeds: erro ao buscar disponibilidade")
        return _api_unavailable("hotelbeds", f"Hotel em {destino.title()} (Hotelbeds)")
