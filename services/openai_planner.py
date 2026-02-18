from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import httpx


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
    (r"\bviewpoints?\b", "mirante"),
    (r"\bsunset\b", "por do sol"),
    (r"\bold town\b", "centro historico"),
    (r"\bday trip\b", "bate-volta"),
    (r"\bevents?\b", "evento"),
    (r"\bfood\b", "gastronomia"),
    (r"\bdrinks?\b", "bebidas"),
)


class OpenAIServiceUnavailableError(RuntimeError):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _env_float(name: str, default: str) -> float:
    raw = _env(name, default)
    try:
        return float(raw)
    except ValueError:
        logging.warning("Valor invalido em %s=%r. Usando %s", name, raw, default)
        return float(default)


def _fold_text(text: str) -> str:
    raw = (text or "").strip().lower()
    normalized = unicodedata.normalize("NFKD", raw)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _expected_slots(ritmo: str) -> List[str]:
    r = _fold_text(ritmo)
    if r == "leve":
        return ["Manha", "Tarde"]
    if "medio" in r:
        return ["Manha", "Tarde", "Noite"]
    return ["Manha", "Tarde", "Noite", "Extra"]


def _normalize_slot(slot: str) -> Optional[str]:
    low = _fold_text(slot)
    if not low:
        return None
    if "manha" in low or "morning" in low:
        return "Manha"
    if "tarde" in low or "afternoon" in low:
        return "Tarde"
    if "noite" in low or "night" in low or "evening" in low:
        return "Noite"
    if "extra" in low:
        return "Extra"
    if "evento" in low:
        return "Evento local"
    return None


def _extract_json_blob(text: str) -> str:
    content = (text or "").strip()
    if not content:
        return "{}"

    if content.startswith("```"):
        content = content.strip("`")
        if "\n" in content:
            content = content.split("\n", 1)[1]
        content = content.rsplit("\n", 1)[0]

    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end < start:
        return "{}"
    return content[start : end + 1]


def _clean_activity_text(value: str, max_len: int = 180) -> str:
    text = " ".join((value or "").strip().split())
    if len(text) <= max_len:
        return text
    short = text[:max_len].rsplit(" ", 1)[0].strip()
    return short if short else text[:max_len]


def _clean_narrative_text(value: str, max_len: int = 700) -> str:
    text = " ".join((value or "").strip().split())
    if len(text) <= max_len:
        return text

    cut = text[:max_len].strip()
    punctuation_positions = [cut.rfind(". "), cut.rfind("! "), cut.rfind("? ")]
    last_sentence = max(punctuation_positions)
    if last_sentence >= int(max_len * 0.45):
        return cut[: last_sentence + 1].strip()

    comma_pos = cut.rfind(",")
    if comma_pos >= int(max_len * 0.60):
        cut = cut[:comma_pos].strip()

    cut = cut.rstrip(".!,;: ")
    return f"{cut}."


def _force_portuguese_terms(text: str) -> str:
    out = text or ""
    for pattern, replacement in _PT_REPLACEMENTS:
        out = re.sub(pattern, replacement, out, flags=re.IGNORECASE)
    return out.strip()


def _fallback_pool(categories: Dict[str, List[str]]) -> List[str]:
    out: List[str] = []
    seen = set()
    for key in ("see", "do", "eat", "drink"):
        for item in categories.get(key, []):
            text = _force_portuguese_terms(_clean_activity_text(str(item)))
            if not text:
                continue
            low = text.lower()
            if low in seen:
                continue
            seen.add(low)
            out.append(text)
            if len(out) >= 80:
                return out
    return out


def _pick_raw_days(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    if isinstance(raw.get("dias"), list):
        return [d for d in raw["dias"] if isinstance(d, dict)]
    if isinstance(raw.get("days"), list):
        return [d for d in raw["days"] if isinstance(d, dict)]
    return []


def _validate_plan(
    raw: Dict[str, Any],
    *,
    ida: date,
    volta: date,
    ritmo: str,
    categories: Dict[str, List[str]],
) -> Optional[List[Dict[str, Any]]]:
    days = max(1, (volta - ida).days)
    expected_slots = _expected_slots(ritmo)
    slot_target = 2 if _fold_text(ritmo) == "intenso" else 1
    raw_days = _pick_raw_days(raw)
    fallback_items = _fallback_pool(categories)
    fallback_idx = 0

    if not raw_days:
        return None

    def next_fallback_item(used_output: set[str]) -> str:
        nonlocal fallback_idx
        if fallback_items:
            attempts = 0
            while attempts < len(fallback_items):
                candidate = fallback_items[fallback_idx % len(fallback_items)]
                fallback_idx += 1
                attempts += 1
                if candidate.lower() not in used_output:
                    return candidate
        base = "Passeio guiado por pontos relevantes da cidade"
        if base.lower() not in used_output:
            return base
        return f"{base} {len(used_output) + 1}"

    out: List[Dict[str, Any]] = []
    for i in range(days):
        target_date = ida + timedelta(days=i)
        day = raw_days[i] if i < len(raw_days) else {}
        raw_acts = day.get("atividades")
        if not isinstance(raw_acts, list):
            raw_acts = day.get("activities")
        if not isinstance(raw_acts, list):
            raw_acts = []

        slot_buckets: Dict[str, List[str]] = {slot: [] for slot in expected_slots}
        unassigned: List[str] = []
        seen_input = set()

        def push_item(slot_text: str, activity_text: str) -> None:
            text = _force_portuguese_terms(_clean_activity_text(activity_text))
            if not text:
                return
            low = text.lower()
            if low in seen_input:
                return
            seen_input.add(low)

            slot = _normalize_slot(slot_text)
            if slot in slot_buckets:
                slot_buckets[slot].append(text)
                return
            unassigned.append(text)

        for raw_act in raw_acts:
            if isinstance(raw_act, dict):
                slot_text = str(raw_act.get("slot") or raw_act.get("periodo") or raw_act.get("period") or "")
                multi_programs = raw_act.get("programas") or raw_act.get("activities") or raw_act.get("itens")
                if isinstance(multi_programs, list):
                    for item in multi_programs:
                        push_item(slot_text, str(item))
                    continue

                activity_text = str(
                    raw_act.get("atividade")
                    or raw_act.get("descricao")
                    or raw_act.get("description")
                    or raw_act.get("texto")
                    or raw_act.get("text")
                    or ""
                )
                push_item(slot_text, activity_text)
            elif isinstance(raw_act, (list, tuple)) and len(raw_act) >= 2:
                push_item(str(raw_act[0] or ""), str(raw_act[1] or ""))
            elif isinstance(raw_act, str):
                push_item("", raw_act)

        plan_acts: List[tuple[str, str]] = []
        used_output = set()
        for slot in expected_slots:
            added = 0
            while added < slot_target:
                candidate = ""
                for text in slot_buckets.get(slot, []):
                    if text.lower() not in used_output:
                        candidate = text
                        break
                if not candidate:
                    for text in unassigned:
                        if text.lower() not in used_output:
                            candidate = text
                            break
                if not candidate:
                    candidate = next_fallback_item(used_output)

                used_output.add(candidate.lower())
                plan_acts.append((slot, candidate))
                added += 1

        raw_day_text = str(
            day.get("texto_dia")
            or day.get("narrativa")
            or day.get("resumo_dia")
            or day.get("resumo")
            or day.get("descricao")
            or ""
        )
        texto_dia = _force_portuguese_terms(_clean_narrative_text(raw_day_text, max_len=700))
        if texto_dia and texto_dia[-1] not in ".!?":
            texto_dia += "."

        out.append(
            {
                "dia": i + 1,
                "data": target_date.strftime("%d/%m/%Y"),
                "atividades": plan_acts,
                "texto_dia": texto_dia,
            }
        )

    return out


def _compact_categories(categories: Dict[str, List[str]]) -> Dict[str, List[str]]:
    compact: Dict[str, List[str]] = {}
    for key in ("see", "do", "eat", "drink"):
        clean_items: List[str] = []
        for item in categories.get(key, []):
            txt = _clean_activity_text(str(item), max_len=140)
            if txt:
                clean_items.append(txt)
            if len(clean_items) >= 18:
                break
        compact[key] = clean_items
    return compact


def _compact_events(eventos: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    compact: List[Dict[str, str]] = []
    for ev in eventos[:10]:
        title = _clean_activity_text(str(ev.get("title") or ""), max_len=120)
        if not title:
            continue
        compact.append(
            {
                "title": title,
                "start_date": str(ev.get("start_date") or ""),
                "end_date": str(ev.get("end_date") or ""),
                "source": _clean_activity_text(str(ev.get("source") or ""), max_len=40),
            }
        )
    return compact


def _event_period(start: str, end: str) -> str:
    s = (start or "").strip()
    e = (end or "").strip()
    if s and e and s != e:
        return f"{s} -> {e}"
    if s:
        return s
    if e:
        return e
    return "data nao informada"


def _validate_event_summaries(raw: Dict[str, Any], eventos_locais: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    raw_events = raw.get("eventos_gerais")
    if not isinstance(raw_events, list):
        raw_events = raw.get("eventos_periodo")
    if not isinstance(raw_events, list):
        raw_events = []

    out: List[Dict[str, str]] = []
    seen = set()

    for item in raw_events:
        if not isinstance(item, dict):
            continue
        period = _clean_activity_text(
            str(item.get("periodo") or item.get("period") or item.get("data") or ""),
            max_len=50,
        ) or "data nao informada"
        title = _force_portuguese_terms(
            _clean_activity_text(str(item.get("titulo") or item.get("title") or ""), max_len=120)
        )
        description = _force_portuguese_terms(
            _clean_activity_text(str(item.get("descricao") or item.get("description") or ""), max_len=180)
        )
        if not title:
            continue
        key = (period + "|" + title).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"periodo": period, "titulo": title, "descricao": description})
        if len(out) >= 12:
            return out

    if out:
        return out

    for ev in eventos_locais:
        title = _force_portuguese_terms(
            _clean_activity_text(str(ev.get("title") or ""), max_len=120)
        )
        if not title:
            continue
        category = _force_portuguese_terms(
            _clean_activity_text(str(ev.get("category") or ""), max_len=40)
        )
        description = _force_portuguese_terms(
            _clean_activity_text(str(ev.get("description") or ""), max_len=180)
        )
        if category:
            description = f"{category}. {description}".strip()
        period = _event_period(str(ev.get("start_date") or ""), str(ev.get("end_date") or ""))
        key = (period + "|" + title).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"periodo": period, "titulo": title, "descricao": description})
        if len(out) >= 12:
            break
    return out


def _compact_gostos(gostos: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for gosto in gostos:
        txt = _clean_activity_text(str(gosto), max_len=40).lower()
        if not txt or txt in seen:
            continue
        seen.add(txt)
        out.append(txt)
        if len(out) >= 12:
            break
    return out


def _extract_completion_text(data: Dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content") or ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for part in content:
            if isinstance(part, dict):
                parts.append(str(part.get("text") or ""))
            else:
                parts.append(str(part))
        return "".join(parts)
    return str(content)


async def build_ai_day_by_day_plan(
    *,
    nome: str,
    destino: str,
    ida: date,
    volta: date,
    ritmo: str,
    gostos: List[str],
    categories: Dict[str, List[str]],
    eventos_locais: List[Dict[str, Any]],
) -> Dict[str, Any]:
    api_key = _env("OPENAI_API_KEY") or _env("OPENAI_KEY") or _env("OPENIA_API_KEY")
    if not api_key:
        logging.error("OpenAI indisponivel: chave de API ausente.")
        raise OpenAIServiceUnavailableError("missing_api_key")

    model = _env("OPENAI_MODEL", "gpt-4o-mini")
    api_base = _env("OPENAI_API_BASE", "https://api.openai.com/v1").rstrip("/")
    timeout = _env_float("OPENAI_TIMEOUT", "45")
    total_days = max(1, (volta - ida).days)

    payload = {
        "nome": nome,
        "destino": destino,
        "data_ida": ida.strftime("%d/%m/%Y"),
        "data_volta": volta.strftime("%d/%m/%Y"),
        "dias": total_days,
        "ritmo": ritmo,
        "gostos_pessoais": _compact_gostos(gostos),
        "categorias": _compact_categories(categories),
        "eventos_locais": _compact_events(eventos_locais),
    }

    system_prompt = (
        "Voce e um especialista em roteiros de viagem. "
        "Responda somente com JSON valido. "
        "Nao use markdown. "
        "Crie um roteiro dia a dia realista e objetivo, sem inventar datas fora do periodo. "
        "Escreva tudo em portugues do Brasil, sem misturar ingles. "
        "Nao cite fontes, links ou idioma. "
        "Seja especifico com nomes de estabelecimentos e pontos conhecidos. "
        "Use linguagem natural, leve e humana."
    )
    user_prompt = (
        "Monte um roteiro para o usuario com este contexto:\n"
        f"{json.dumps(payload, ensure_ascii=False)}\n\n"
        "Formato obrigatorio:\n"
        "{\n"
        '  "dias": [\n'
        "    {\n"
        '      "dia": 1,\n'
        '      "data": "DD/MM/AAAA",\n'
        '      "texto_dia": "Paragrafo natural com fluxo do dia, sem usar rotulos fixos.",\n'
        '      "atividades": [\n'
        '        {"slot": "Manha", "atividade": "..."},\n'
        '        {"slot": "Tarde", "atividade": "..."}\n'
        "      ]\n"
        "    }\n"
        "  ],\n"
        '  "eventos_gerais": [\n'
        '    {"periodo": "DD/MM/AAAA -> DD/MM/AAAA", "titulo": "...", "descricao": "..."}\n'
        "  ]\n"
        "}\n\n"
        "Slots validos: Manha, Tarde, Noite, Extra. "
        "Respeite o ritmo: leve=2 slots, medio=3 slots, intenso=4 slots por dia. "
        "Todas as descricoes devem estar em portugues do Brasil.\n"
        "Priorize os gostos pessoais informados no contexto.\n"
        "Exemplos de priorizacao: praia -> praias e quiosques; museus -> museus e centros culturais; "
        "gastronomia -> restaurantes e botecos.\n"
        "Se ritmo for intenso, inclua pelo menos 2 programas por periodo (Manha, Tarde, Noite, Extra) "
        "e valorize mais a noite com bares, botecos e casas de show.\n"
        "No campo texto_dia, escreva um paragrafo fluido do plano diario, com variacao de estilo e sem repetir "
        "sempre os mesmos conectores.\n"
        "Evite formato robotico do tipo 'Manha: ... Tarde: ... Noite: ...' na narrativa.\n"
        "Nao corte frases no meio; finalize sempre com sentencas completas.\n"
        "Evite recomendacoes genericas. Prefira nomes de lugares, bairros e estabelecimentos.\n"
        "No campo eventos_gerais, resuma eventos do periodo em portugues."
    )

    request_json = {
        "model": model,
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{api_base}/chat/completions",
                headers=headers,
                json=request_json,
            )
    except Exception as exc:
        logging.exception("Falha de rede ao chamar OpenAI para gerar roteiro.")
        raise OpenAIServiceUnavailableError("network_error", str(exc)) from exc

    if response.status_code >= 400:
        reason = "api_error"
        detail = ""
        try:
            payload = response.json()
            err = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(err, dict):
                code = str(err.get("code") or "")
                message = str(err.get("message") or "")
                detail = f"{code} | {message}".strip(" |")
                low = f"{code} {message}".lower()
                if "insufficient_quota" in low or "quota" in low or response.status_code == 402:
                    reason = "insufficient_quota"
                elif response.status_code in (401, 403):
                    reason = "auth_error"
                elif response.status_code >= 500:
                    reason = "upstream_error"
        except Exception:
            detail = response.text[:300]

        if reason == "insufficient_quota":
            logging.error("OpenAI sem creditos. Detalhes: %s", detail)
        else:
            logging.error(
                "OpenAI indisponivel (%s) status=%s detalhes=%s",
                reason,
                response.status_code,
                detail,
            )
        raise OpenAIServiceUnavailableError(reason, detail)

    try:
        data = response.json()
    except Exception as exc:
        logging.error("Resposta invalida da OpenAI (JSON).")
        raise OpenAIServiceUnavailableError("invalid_json", str(exc)) from exc

    try:
        content = _extract_completion_text(data)
        raw_json = _extract_json_blob(content)
        parsed = json.loads(raw_json)
    except Exception as exc:
        logging.error("OpenAI retornou conteudo invalido para roteiro.")
        raise OpenAIServiceUnavailableError("invalid_response", str(exc)) from exc

    roteiro = _validate_plan(
        parsed,
        ida=ida,
        volta=volta,
        ritmo=ritmo,
        categories=categories,
    )
    if not roteiro:
        logging.error("OpenAI retornou roteiro vazio/invalido.")
        raise OpenAIServiceUnavailableError("empty_plan")

    eventos_gerais = _validate_event_summaries(parsed, eventos_locais)

    return {
        "roteiro": roteiro,
        "eventos_gerais": eventos_gerais,
        "model": str(data.get("model") or model),
    }

