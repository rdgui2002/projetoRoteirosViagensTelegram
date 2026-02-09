# services/itinerary_builder.py
from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, Any, List

def _days(ida: date, volta: date) -> List[date]:
    n = max(1, (volta - ida).days)
    return [ida + timedelta(days=i) for i in range(n)]

def build_day_by_day(categories: Dict[str, List[str]], ida: date, volta: date, ritmo: str) -> List[Dict[str, Any]]:
    see = categories.get("see", [])
    do = categories.get("do", [])
    eat = categories.get("eat", [])
    drink = categories.get("drink", [])

    blocks = 2 if ritmo == "leve" else 3 if ritmo == "médio" else 4
    slots = ["Manhã", "Tarde", "Noite", "Extra"]

    used = set()
    def pick(arr):
        for x in arr:
            k = x.lower()
            if k not in used:
                used.add(k)
                return x
        return None

    roteiro = []
    for i, d in enumerate(_days(ida, volta), start=1):
        plan = {"dia": i, "data": d.strftime("%d/%m/%Y"), "atividades": []}

        # Manhã: SEE
        a = pick(see) or (see[0] if see else None)
        plan["atividades"].append((slots[0], f"📍 {a}" if a else "📍 Passeio central / atração principal"))

        if blocks >= 2:
            b = pick(do) or pick(see)
            plan["atividades"].append((slots[1], f"✨ {b}" if b else "✨ Caminhada por bairro famoso / parque"))

        if blocks >= 3:
            c = pick(eat) or pick(drink)
            plan["atividades"].append((slots[2], f"🍽️ {c}" if c else "🍽️ Jantar em lugar bem avaliado"))

        if blocks >= 4:
            e = pick(do) or pick(see)
            plan["atividades"].append((slots[3], f"➕ {e}" if e else "➕ Extra opcional"))

        roteiro.append(plan)

    return roteiro
