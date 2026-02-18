from __future__ import annotations

import re
from typing import Any, Dict, List


_PT_REPLACEMENTS = (
    (r"\bmorning\b", "manha"),
    (r"\bafternoon\b", "tarde"),
    (r"\bevening\b", "noite"),
    (r"\bnight\b", "noite"),
    (r"\bbeaches?\b", "praia"),
    (r"\bmuseums?\b", "museu"),
    (r"\bnightlife\b", "vida noturna"),
    (r"\brestaurants?\b", "restaurante"),
    (r"\bmarkets?\b", "mercado"),
    (r"\bparks?\b", "parque"),
    (r"\bwalking tour\b", "passeio a pe"),
    (r"\bsunset\b", "por do sol"),
    (r"\bday trip\b", "bate-volta"),
    (r"\bevents?\b", "evento"),
)


def _pt_text(text: str) -> str:
    out = text or ""
    for pattern, replacement in _PT_REPLACEMENTS:
        out = re.sub(pattern, replacement, out, flags=re.IGNORECASE)
    return out.strip()


def _event_period_label(event: Dict[str, Any]) -> str:
    start = str(event.get("start_date") or "").strip()
    end = str(event.get("end_date") or "").strip()
    if start and end and start != end:
        return f"{start} -> {end}"
    if start:
        return start
    if end:
        return end
    return "data nao informada"


def _build_natural_day_text(day: Dict[str, Any]) -> str:
    explicit = _pt_text(
        str(
            day.get("texto_dia")
            or day.get("narrativa")
            or day.get("resumo_dia")
            or day.get("resumo")
            or ""
        )
    )
    if explicit:
        return explicit

    raw_activities = day.get("atividades", []) or []
    activities: List[str] = []
    for item in raw_activities:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            text = _pt_text(str(item[1]))
        else:
            text = _pt_text(str(item))
        if text:
            activities.append(text)

    if not activities:
        return ""

    day_num = int(day.get("dia") or 1)
    openers = [
        "Para comecar o dia",
        "Uma boa forma de iniciar o roteiro",
        "No inicio do dia",
        "Comece o seu dia",
    ]
    mids = ["depois", "na sequencia", "logo em seguida", "mais tarde"]
    closers = [
        "e feche o dia com",
        "encerrando o dia com",
        "para terminar o dia com",
        "finalizando o dia com",
    ]

    opener = openers[(day_num - 1) % len(openers)]
    mid = mids[(day_num - 1) % len(mids)]
    closer = closers[(day_num - 1) % len(closers)]

    if len(activities) == 1:
        return f"{opener}, voce pode ir a {activities[0]}."
    if len(activities) == 2:
        return f"{opener}, voce pode ir a {activities[0]}; {mid}, vale incluir {activities[1]}."

    text = f"{opener}, voce pode ir a {activities[0]}; {mid}, vale incluir {activities[1]}"
    for activity in activities[2:-1]:
        text += f", depois passar por {activity}"
    text += f", {closer} {activities[-1]}."
    return text


def format_trip_output(trip: Dict[str, Any]) -> str:
    destino = trip.get("destino", "Destino")
    ida = trip.get("ida", "")
    volta = trip.get("volta", "")
    ritmo = trip.get("ritmo", "")
    lines: List[str] = []
    lines.append(f"Roteiro em {destino}")
    lines.append(f"Datas: {ida} -> {volta}")
    lines.append(f"Ritmo: {ritmo}")
    lines.append("")
    lines.append("Roteiro dia a dia:")
    lines.append("")

    for d in trip.get("roteiro", []):
        lines.append(f"Dia {d['dia']} - {d['data']}")
        day_text = _build_natural_day_text(d)
        if day_text:
            lines.append(f" {day_text}")
        lines.append("")

    eventos_pt = trip.get("eventos_gerais_pt", []) or []
    if eventos_pt:
        lines.append("Eventos e tradicoes locais no periodo:")
        lines.append("")
        for ev in eventos_pt:
            period = _pt_text(str(ev.get("periodo") or "").strip() or "data nao informada")
            title = _pt_text(str(ev.get("titulo") or ""))
            description = _pt_text(str(ev.get("descricao") or ""))
            lines.append(f" - [{period}] {title}")
            if description:
                lines.append(f"   {description}")
        lines.append("")
    else:
        eventos = trip.get("eventos_locais", []) or []
        if not eventos:
            return "\n".join(lines)
        lines.append("Eventos e tradicoes locais no periodo:")
        lines.append("")
        for ev in eventos:
            period = _event_period_label(ev)
            title = _pt_text(str(ev.get("title") or ""))
            category = _pt_text(str(ev.get("category") or ""))
            description = _pt_text(str(ev.get("description") or ""))

            line = f" - [{period}] {title}"
            if category:
                line += f" ({category})"
            lines.append(line)
            if description:
                lines.append(f"   {description}")
        lines.append("")
    return "\n".join(lines)


def trip_to_pdf_lines(trip: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    out.append(f"Destino: {trip.get('destino', '')}")
    out.append(f"Datas: {trip.get('ida', '')} -> {trip.get('volta', '')}")
    out.append(f"Ritmo: {trip.get('ritmo', '')}")
    out.append("")

    for d in trip.get("roteiro", []):
        out.append(f"Dia {d['dia']} - {d['data']}")
        day_text = _build_natural_day_text(d)
        if day_text:
            out.append(f"  {day_text}")
        out.append("")

    eventos_pt = trip.get("eventos_gerais_pt", []) or []
    if eventos_pt:
        out.append("Eventos e tradicoes locais no periodo:")
        for ev in eventos_pt:
            period = _pt_text(str(ev.get("periodo") or "").strip() or "data nao informada")
            title = _pt_text(str(ev.get("titulo") or ""))
            description = _pt_text(str(ev.get("descricao") or ""))
            out.append(f" - [{period}] {title}")
            if description:
                out.append(f"   {description}")
        out.append("")
    else:
        eventos = trip.get("eventos_locais", []) or []
        if not eventos:
            return out
        out.append("Eventos e tradicoes locais no periodo:")
        for ev in eventos:
            period = _event_period_label(ev)
            title = _pt_text(str(ev.get("title") or ""))
            category = _pt_text(str(ev.get("category") or ""))
            line = f" - [{period}] {title}"
            if category:
                line += f" ({category})"
            out.append(line)
        out.append("")
    return out
