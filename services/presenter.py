from __future__ import annotations
from typing import Dict, Any, List


def format_trip_output(trip: Dict[str, Any]) -> str:
    destino = trip.get("destino", "Destino")
    ida = trip.get("ida", "")
    volta = trip.get("volta", "")
    ritmo = trip.get("ritmo", "")
    lang = trip.get("lang", "pt")
    url = trip.get("url", "")

    lines: List[str] = []
    lines.append(f"🗺️ Roteiro em {destino}")
    lines.append(f"📅 Datas: {ida} → {volta}")
    lines.append(f"⚡ Ritmo: {ritmo}")
    lines.append("")
    lines.append("Roteiro dia a dia (baseado no Wikivoyage):")
    lines.append("")

    for d in trip.get("roteiro", []):
        lines.append(f"Dia {d['dia']} — {d['data']}")
        for slot, act in d.get("atividades", []):
            lines.append(f" - {slot}: {act}")
        lines.append("")

    if url:
        lines.append(f"Fonte: {url}")
    lines.append(f"Idioma da fonte: {lang.upper()}")
    return "\n".join(lines)


def trip_to_pdf_lines(trip: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    out.append(f"Destino: {trip.get('destino','')}")
    out.append(f"Datas: {trip.get('ida','')} → {trip.get('volta','')}")
    out.append(f"Ritmo: {trip.get('ritmo','')}")
    out.append("")
    for d in trip.get("roteiro", []):
        out.append(f"Dia {d['dia']} — {d['data']}")
        for slot, act in d.get("atividades", []):
            out.append(f"  - {slot}: {act}")
        out.append("")
    out.append(f"Fonte: {trip.get('url','')}")
    return out
