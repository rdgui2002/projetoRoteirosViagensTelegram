from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from services.openai_planner import OpenAIServiceUnavailableError, build_ai_day_by_day_plan
from services.utils import parse_date_br

# Nome mantido por compatibilidade com imports antigos.
RITMOS_VALIDOS = ["leve", "medio", "intenso"]


@dataclass
class TripPreferences:
    nome: str
    destino: str
    data_ida: str
    data_volta: str
    ritmo: str
    gostos: List[str] = field(default_factory=list)


class DestinationIsCountryError(ValueError):
    """Mantido para compatibilidade com o fluxo do bot."""


class AIServiceUnavailableError(RuntimeError):
    """Servico de IA indisponivel para gerar roteiro."""


def _fold_text(text: str) -> str:
    raw = (text or "").strip().lower()
    normalized = unicodedata.normalize("NFKD", raw)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _normalize_ritmo(ritmo: str) -> str:
    low = _fold_text(ritmo)
    if low == "leve":
        return "leve"
    if "medio" in low:
        return "medio"
    if "intenso" in low:
        return "intenso"
    return ""


def _clean_space(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


async def detect_country_destination(destino: str) -> Optional[str]:
    del destino
    return None


async def build_itinerary_from_wikivoyage(prefs: TripPreferences) -> Dict[str, Any]:
    ritmo = _normalize_ritmo(prefs.ritmo)
    if not ritmo:
        raise ValueError("Ritmo invalido. Use: leve / medio / intenso")

    ida = parse_date_br(prefs.data_ida)
    volta = parse_date_br(prefs.data_volta)
    if volta <= ida:
        raise ValueError("A data de volta precisa ser depois da ida.")

    destino = _clean_space(prefs.destino)
    if len(destino) < 2:
        raise ValueError("Destino invalido. Informe uma cidade ou regiao.")

    try:
        ai_result = await build_ai_day_by_day_plan(
            nome=prefs.nome,
            destino=destino,
            ida=ida,
            volta=volta,
            ritmo=ritmo,
            gostos=prefs.gostos,
            categories={},
            eventos_locais=[],
        )
    except OpenAIServiceUnavailableError as exc:
        if exc.reason == "insufficient_quota":
            logging.error("OpenAI sem creditos para gerar roteiro.")
        else:
            logging.error("Falha no servico OpenAI. reason=%s detail=%s", exc.reason, exc.detail)
        raise AIServiceUnavailableError(exc.reason) from exc

    roteiro = ai_result.get("roteiro")
    if not isinstance(roteiro, list) or not roteiro:
        logging.error("OpenAI retornou roteiro vazio para destino=%s", destino)
        raise AIServiceUnavailableError("empty_plan")

    eventos_gerais_pt = ai_result.get("eventos_gerais")
    if not isinstance(eventos_gerais_pt, list):
        eventos_gerais_pt = []

    return {
        "nome": prefs.nome,
        "destino": destino,
        "ida": prefs.data_ida,
        "volta": prefs.data_volta,
        "ritmo": ritmo,
        "lang": "pt",
        "url": "",
        "roteiro": roteiro,
        "categories": {},
        "eventos_locais": [],
        "eventos_gerais_pt": eventos_gerais_pt,
        "planner": "openai",
        "planner_model": str(ai_result.get("model") or ""),
    }
