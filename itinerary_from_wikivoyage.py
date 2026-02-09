from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List

SLOTS_LEVE = ["Manhã", "Tarde"]
SLOTS_MEDIO = ["Manhã", "Tarde", "Noite"]
SLOTS_INTENSO = ["Manhã", "Tarde", "Noite", "Extra"]


def _days(ida: date, volta: date) -> List[date]:
    n = max(1, (volta - ida).days)
    return [ida + timedelta(days=i) for i in range(n)]


def pick(items: List[Dict[str, Any]], used: set[str], fallback: Dict[str, Any] | None = None) -> Dict[str, Any] | None:
    for it in items:
        k = it.get("name", "").lower()
        if k and k not in used:
            used.add(k)
            return it
    return fallback


def _is_nightlife(text: str) -> bool:
    t = text.lower()
    keywords = [
        "bar", "bares", "pub", "boate", "balada", "clube", "nightclub",
        "discoteca", "lounge", "karaok", "cocktail", "cervej",
    ]
    return any(k in t for k in keywords)

def _is_sports(text: str) -> bool:
    t = text.lower()
    keywords = [
        "estádio", "estadio", "arena", "stadium", "arena", "futebol",
        "football", "soccer", "basquete", "basketball", "ginásio", "ginasio",
        "sports", "esporte", "clube esportivo",
    ]
    return any(k in t for k in keywords)


def build_itinerary(
    payload_clean: Dict[str, Any],
    ida: date,
    volta: date,
    ritmo: str,
    prefer: List[str],
) -> List[Dict[str, Any]]:
    cats = payload_clean.get("categories_clean", {}) or {}

    see = cats.get("see", [])
    do = cats.get("do", [])
    eat = cats.get("eat", [])
    drink = cats.get("drink", [])
    all_items = see + do + eat + drink
    nightlife = [
        d for d in all_items
        if _is_nightlife((d.get("name", "") + " " + d.get("desc", "")))
    ]
    sports = [
        d for d in all_items
        if _is_sports((d.get("name", "") + " " + d.get("desc", "")))
    ]

    prefer = [p.strip().lower() for p in (prefer or [])]
    want_food = "restaurantes" in prefer or "food" in prefer

    slots = SLOTS_MEDIO
    if ritmo == "leve":
        slots = SLOTS_LEVE
    elif ritmo == "intenso":
        slots = SLOTS_INTENSO

    used: set[str] = set()
    days = _days(ida, volta)
    roteiro: List[Dict[str, Any]] = []

    for i, d in enumerate(days, start=1):
        plan = {"dia": i, "data": d.strftime("%d/%m/%Y"), "atividades": []}

        # Manhã: sempre algo “See”
        morning = pick(see, used, fallback=(see[0] if see else None))
        if morning:
            text = morning["name"] if not morning.get("desc") else f"{morning['name']} — {morning['desc']}"
            plan["atividades"].append(("Manhã", f"📍 {text}"))
        else:
            plan["atividades"].append(("Manhã", "📍 Passeio central / ponto turístico principal"))

        # Tarde: “Do” (ou outro see)
        if len(slots) >= 2:
            afternoon = pick(do, used, fallback=pick(see, used))
            if afternoon:
                text = afternoon["name"] if not afternoon.get("desc") else f"{afternoon['name']} — {afternoon['desc']}"
                plan["atividades"].append(("Tarde", f"✨ {text}"))
            else:
                plan["atividades"].append(("Tarde", "✨ Caminhada por bairro famoso / parque"))

        # Noite: comida/bebida (se existir)
        if len(slots) >= 3:
            if want_food and eat:
                dinner = pick(eat, used, fallback=pick(nightlife, used) or pick(drink, used))
                if dinner:
                    text = dinner["name"] if not dinner.get("desc") else f"{dinner['name']} — {dinner['desc']}"
                    plan["atividades"].append(("Noite", f"🍽️ {text}"))
                else:
                    plan["atividades"].append(("Noite", "🍽️ Restaurante local bem avaliado"))
            else:
                night = pick(
                    nightlife,
                    used,
                    fallback=pick(sports, used)
                    or pick(drink, used)
                    or pick(eat, used)
                    or pick(do, used)
                    or pick(see, used),
                )
                if night:
                    text = night["name"] if not night.get("desc") else f"{night['name']} — {night['desc']}"
                    plan["atividades"].append(("Noite", f"🌙 {text}"))
                else:
                    plan["atividades"].append(("Noite", "🌙 Passeio noturno / bar local"))

        # Extra: mais um Do/See
        if len(slots) >= 4:
            extra = pick(do, used, fallback=pick(see, used))
            if extra:
                text = extra["name"] if not extra.get("desc") else f"{extra['name']} — {extra['desc']}"
                plan["atividades"].append(("Extra", f"➕ {text}"))
            else:
                plan["atividades"].append(("Extra", "➕ Atividade extra opcional"))

        roteiro.append(plan)

    return roteiro
