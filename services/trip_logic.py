from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from _old.api_clients import search_flights, search_hotels
from _old.links import build_flight_link, build_hotel_link
from _old.osm_client import get_city_items
from services.utils import chunk_days, days_between, normalize_city, parse_date_br

ESTILOS_VALIDOS = ["rock", "surfe", "restaurantes"]
RITMOS_VALIDOS = ["leve", "médio", "intenso"]


@dataclass
class TripPreferences:
    nome: str
    destino: str
    origem: str
    data_ida: str
    data_volta: str
    estilos: list[str]
    ritmo: str
    orcamento: float | None = None


def build_itinerary(prefs: TripPreferences) -> dict[str, Any]:
    destino_key = normalize_city(prefs.destino)
    origem_key = (prefs.origem or "").strip()
    ida = parse_date_br(prefs.data_ida)
    volta = parse_date_br(prefs.data_volta)

    items, title, source_url = get_city_items(prefs.destino)
    see_items = items.get("see", [])
    do_items = items.get("do", [])
    eat_items = items.get("eat", [])
    drink_items = items.get("drink", [])

    dias = chunk_days(ida, volta)
    estilos = [s for s in prefs.estilos if s in ESTILOS_VALIDOS]

    blocks = 2 if prefs.ritmo == "leve" else 3 if prefs.ritmo == "médio" else 4
    slots = ["Manhã", "Tarde", "Noite", "Extra"]

    roteiro = []
    for idx, day in enumerate(dias, start=1):
        day_plan = {"dia": idx, "data": day.strftime("%d/%m/%Y"), "atividades": []}

        morning = see_items[(idx - 1) % max(1, len(see_items))] if see_items else "Ponto turístico principal"
        afternoon = do_items[(idx - 1) % max(1, len(do_items))] if do_items else "Passeio recomendado"
        night = eat_items[(idx - 1) % max(1, len(eat_items))] if eat_items else (
            drink_items[(idx - 1) % max(1, len(drink_items))] if drink_items else "Jantar/local noturno"
        )
        extra = drink_items[(idx - 1) % max(1, len(drink_items))] if drink_items else ""

        day_plan["atividades"].append((slots[0], f"📍 {morning}"))
        day_plan["atividades"].append((slots[1], f"{afternoon}"))
        day_plan["atividades"].append((slots[2], f"{night}"))
        if blocks == 4 and extra:
            day_plan["atividades"].append((slots[3], f"{extra}"))

        roteiro.append(day_plan)

    flights = search_flights(origem_key, destino_key, ida, volta)["best_option"]
    hotels = search_hotels(destino_key, ida, volta, prefs.ritmo, prefs.orcamento)["best_option"]

    dias_count = len(dias)
    daily_food = 120 if prefs.ritmo == "leve" else 160 if prefs.ritmo == "médio" else 200
    daily_transport = 60 if destino_key in ["rio de janeiro", "sao paulo"] else 45
    extras = (daily_food + daily_transport) * dias_count

    total = float(extras)
    if flights.get("price_total") is not None:
        total += float(flights["price_total"])
    if hotels.get("price_total") is not None:
        total += float(hotels["price_total"])

    return {
        "destino": destino_key,
        "ida": prefs.data_ida,
        "volta": prefs.data_volta,
        "estilos": estilos,
        "ritmo": prefs.ritmo,
        "roteiro": roteiro,
        "sources": {
            "osm_name": title,
            "osm_url": source_url,
            "license": "ODbL 1.0",
        },
        "links": {
            "voos": build_flight_link(origem_key, destino_key, ida, volta),
            "hoteis": build_hotel_link(destino_key, ida, volta),
        },
        "custos": {
            "passagens": flights,
            "hospedagem": hotels,
            "extras_estimados": {"food_transport": float(extras), "notes": "Estimativa simples (ajuste depois)."},
            "total_estimado": total,
        },
    }
