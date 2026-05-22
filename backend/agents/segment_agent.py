"""LLM-backed segment suggestion agent.

The agent asks AdTarget for existing target groups, gives the LLM a compact
reference list, and validates the model output so that every returned audience
hypothesis clearly states whether it is backed by an existing Target Group or is
only a recommendation for a new/manual segment.
"""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from llm import get_llm
from schemas import MatchedTargetGroup, SegmentHypothesis, SegmentSuggestRequest, SegmentSuggestResponse
from tools import adtarget

_MIN_HYPOTHESES = 2
_MAX_HYPOTHESES = 3
_MATCH_THRESHOLD = 0.55
_MAX_TARGET_GROUPS_FOR_PROMPT = 50


class _RawSegmentHypothesis(BaseModel):
    name: str
    audience_description: str
    relevance_reason: str
    selection_criteria: Any = Field(default_factory=dict)
    risk_or_limitation: str
    matched_target_group: Any = None
    is_existing_target_group: bool = False
    segment_source: str = "llm_composed_demo"
    demo_insight: str = ""
    estimated_reach_label: str = ""
    confidence: float = Field(ge=0.0, le=1.0)


class _RawSegmentResponse(BaseModel):
    hypotheses: list[_RawSegmentHypothesis] = Field(min_length=_MIN_HYPOTHESES, max_length=_MAX_HYPOTHESES)


async def suggest_segments(request: SegmentSuggestRequest) -> SegmentSuggestResponse:
    """Suggest 2-3 segment hypotheses using AdTarget groups as LLM context."""
    warnings: list[str] = []
    try:
        target_groups = await _load_target_groups()
    except Exception as exc:
        print(f"[segment_agent] Target groups lookup failed: {type(exc).__name__}: {exc}")
        target_groups = []
        warnings.append("Справочник целевых групп недоступен; гипотезы построены без привязки к существующим ЦГ.")
    compact_groups = _compact_target_groups(target_groups)

    try:
        raw_response = await _ask_llm(request, compact_groups)
    except Exception as exc:
        print(f"[segment_agent] LLM segment suggestion failed: {type(exc).__name__}: {exc}")
        raw_response = _fallback_raw_response(request, compact_groups)
        warnings.append("LLM недоступна; гипотезы построены резервной эвристикой и требуют проверки аналитиком.")

    hypotheses = [
        _to_segment_hypothesis(raw, target_groups, request, index + 1)
        for index, raw in enumerate(raw_response.hypotheses[:_MAX_HYPOTHESES])
    ]

    if len(hypotheses) < _MIN_HYPOTHESES:
        fallback = _fallback_raw_response(request, compact_groups)
        for raw in fallback.hypotheses:
            if len(hypotheses) >= _MIN_HYPOTHESES:
                break
            if raw.name not in {hypothesis.name for hypothesis in hypotheses}:
                hypotheses.append(_to_segment_hypothesis(raw, target_groups, request, len(hypotheses) + 1))

    if not target_groups:
        warnings.append("Справочник целевых групп недоступен; все совпадения с ЦГ помечены как рекомендации.")
    elif not any(hypothesis.is_existing_target_group for hypothesis in hypotheses):
        warnings.append("Не найдено уверенных совпадений с существующими Target Groups; используйте гипотезы как рекомендации.")

    return SegmentSuggestResponse(
        summary=(
            f"Подготовлено {len(hypotheses)} гипотезы сегментов для продукта "
            f"«{request.product}» и цели «{request.campaign_goal}»."
        ),
        hypotheses=hypotheses[:_MAX_HYPOTHESES],
        warnings=_dedupe(warnings),
        recommendation_only=True,
    )


# Backward-compatible entry point used by older route/test imports.
run = suggest_segments


async def _load_target_groups() -> list[dict[str, Any]]:
    result = await adtarget.list_target_groups()
    if isinstance(result, dict) and isinstance(result.get("items"), list):
        return [item for item in result["items"] if isinstance(item, dict)]
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)]
    return []


def _compact_target_groups(target_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for group in target_groups[:_MAX_TARGET_GROUPS_FOR_PROMPT]:
        compact.append({
            "id": group.get("id"),
            "name": group.get("name") or group.get("title") or "Без названия",
            "clients_count": _normalise_clients_count(_clients_count_value(group)),
            "status": group.get("status"),
        })
    return compact


def _clients_count_value(group: dict[str, Any]) -> Any:
    if "clientsCount" in group:
        return group.get("clientsCount")
    return group.get("clients_count")


def _normalise_clients_count(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        cleaned = re.sub(r"[\s\u00a0,_'’]", "", text)
        if re.fullmatch(r"[+-]?\d+", cleaned):
            return int(cleaned)
        try:
            as_float = float(cleaned)
        except ValueError:
            return None
        if as_float.is_integer():
            return int(as_float)
    return None


def _audience_signals(request: SegmentSuggestRequest) -> dict[str, Any] | None:
    """Достаёт собранные сигналы по продукту (NBO / подключившие / похожие)
    из current_campaign_context. Их кладёт туда вызывающий агент."""
    ctx = request.current_campaign_context or {}
    signals = ctx.get("audience_signals")
    return signals if isinstance(signals, dict) else None


async def _ask_llm(request: SegmentSuggestRequest, compact_groups: list[dict[str, Any]]) -> _RawSegmentResponse:
    llm = get_llm(for_tools=False)
    demo_contact_base_profile = _demo_contact_base_profile(request)
    audience_signals = _audience_signals(request)
    response = await llm.ainvoke([
        SystemMessage(content=_segment_system_prompt()),
        HumanMessage(content=json.dumps({
            "request": {**request.model_dump(), "demo_contact_base_profile": demo_contact_base_profile},
            "strategy": request.strategy,
            "existing_target_groups": compact_groups,
            "demo_contact_base_profile": demo_contact_base_profile,
            # Сигналы для подбора аудитории под продукт (см. agents/audience_strategy.py):
            # NBO-аудитория, число подключивших, похожие продукты. Может быть null.
            "audience_signals": audience_signals,
        }, ensure_ascii=False)),
    ])
    content = response.content if hasattr(response, "content") else str(response)
    return _parse_raw_response(content, request, compact_groups)


def _segment_system_prompt() -> str:
    return (
        "Ты CVM-аналитик и предлагаешь сегменты аудитории для маркетинговой кампании. "
        "Верни только валидный JSON без markdown и комментариев. "
        "Строгая схема ответа: {\"hypotheses\":[{\"name\":string,"
        "\"audience_description\":string,\"relevance_reason\":string,"
        "\"selection_criteria\":object|string,\"risk_or_limitation\":string,"
        "\"matched_target_group\":object|null,\"is_existing_target_group\":boolean,"
        "\"segment_source\":\"existing_target_group|llm_composed_demo\","
        "\"demo_insight\":string,\"estimated_reach_label\":string,"
        "\"confidence\":number}]}. "
        "Обязательные ограничения безопасности: "
        "1) не утверждай, что новая Target Group создана, заведена или уже доступна; "
        "2) не оценивай точный размер сегмента и не называй точное количество клиентов, если это не поле "
        "clients_count существующей Target Group из справочника; "
        "3) не утверждай, что проверены согласия, контактные политики, opt-out, frequency cap или юридические "
        "ограничения — можно только указать, что они требуют отдельной проверки; "
        "4) если Target Group не найдена в справочнике existing_target_groups, верни для этой гипотезы "
        "matched_target_group=null, is_existing_target_group=false, segment_source=llm_composed_demo "
        "и текст 'только рекомендация' в risk_or_limitation; "
        "5) если strategy=compose_new или strategy=hybrid, можно предложить новый сегмент на основе "
        "demo_contact_base_profile, но это recommendation-only/demo-only гипотеза, а не созданный сегмент в AdTarget; "
        "6) если strategy=existing_groups, опирайся только на existing_target_groups и не сочиняй новые сегменты; "
        "7) для llm_composed_demo обязательно заполни demo_insight, estimated_reach_label одним из "
        "'Высокий', 'Средний', 'Низкий' без точного real-data размера и явно укажи demo-only/recommendation-only; "
        "8) всегда возвращай ровно 2–3 гипотезы; "
        "9) для каждой гипотезы обязательно заполни risk_or_limitation конкретным риском или ограничением. "
        "Для matched_target_group используй только существующую Target Group из списка и указывай минимум id и name. "
        "Не выдумывай id или названия Target Groups. confidence — число от 0 до 1.\n"
        "ПОДБОР АУДИТОРИИ ПО ПРОДУКТУ: если есть поле audience_signals (не null) — "
        "строй гипотезы строго по выбранному методу audience_signals.chosen_method "
        "и следуй инструкции audience_signals.guidance. "
        "ВСЕ 2-3 гипотезы должны соответствовать одному выбранному методу: "
        "- nbo — аудитории, для которых продукт является лучшим следующим предложением "
        "(если есть поле nbo — опирайся на nbo.description и nbo.estimated_size как demo-оценку; "
        "если nbo пустой — всё равно считай, что NBO-аудитория существует); "
        "- lookalike_existing — look-alike по абонентам, уже подключившим продукт (existing_subscribers); "
        "- lookalike_similar — look-alike по аудитории похожих продуктов (similar_products); "
        "Если chosen_method не задан — используй все доступные сигналы. "
        "В relevance_reason КАЖДОЙ гипотезы явно укажи метод подбора. "
        "Для NBO ориентировочный размер бери из nbo.estimated_size как модельную demo-оценку."
    )


def _parse_raw_response(
    content: Any,
    request: SegmentSuggestRequest | None = None,
    compact_groups: list[dict[str, Any]] | None = None,
) -> _RawSegmentResponse:
    if isinstance(content, list):
        content = "".join(str(part.get("text", part)) if isinstance(part, dict) else str(part) for part in content)
    text = str(content).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        payload = json.loads(match.group(0))

    if isinstance(payload, list):
        payload = {"hypotheses": payload}
    normalised_payload = _normalise_raw_payload(payload, request, compact_groups or [])
    return _RawSegmentResponse.model_validate(normalised_payload)


def _normalise_raw_payload(
    payload: Any,
    request: SegmentSuggestRequest | None,
    compact_groups: list[dict[str, Any]],
) -> dict[str, Any]:
    """Post-process and safely normalize imperfect LLM JSON before schema validation."""
    if not isinstance(payload, dict):
        payload = {}
    hypotheses = payload.get("hypotheses")
    if not isinstance(hypotheses, list):
        hypotheses = []

    normalised = [
        _normalise_raw_hypothesis(item, index + 1, compact_groups)
        for index, item in enumerate(hypotheses[:_MAX_HYPOTHESES])
        if isinstance(item, dict)
    ]

    if len(normalised) < _MIN_HYPOTHESES:
        fallback = _fallback_raw_response(request, compact_groups) if request else _generic_raw_response(compact_groups)
        existing_names = {_normalise_text(item.get("name")) for item in normalised}
        for raw in fallback.hypotheses:
            if len(normalised) >= _MIN_HYPOTHESES:
                break
            raw_item = raw.model_dump()
            if _normalise_text(raw_item.get("name")) in existing_names:
                continue
            normalised.append(_normalise_raw_hypothesis(raw_item, len(normalised) + 1, compact_groups))

    return {"hypotheses": normalised[:_MAX_HYPOTHESES]}


def _normalise_raw_hypothesis(
    item: dict[str, Any],
    index: int,
    compact_groups: list[dict[str, Any]],
) -> dict[str, Any]:
    candidate = item.get("matched_target_group")
    matched_group = _compact_group_match(candidate, compact_groups)
    risk = _safe_text(item.get("risk_or_limitation"))
    if not risk:
        risk = "Требуется отдельная проверка применимости сегмента, контактных ограничений и юридических требований."
    if matched_group is None and "только рекомендация" not in _normalise_text(risk):
        risk = f"{risk} Это только рекомендация: Target Group не найдена в справочнике."

    name = _safe_text(item.get("name")) or f"Гипотеза сегмента {index}"
    audience_description = _safe_text(item.get("audience_description") or item.get("description"))
    relevance_reason = _safe_text(item.get("relevance_reason") or item.get("rationale"))

    return {
        "name": name,
        "audience_description": audience_description or "Сегмент требует дополнительного описания перед запуском кампании.",
        "relevance_reason": relevance_reason or "Релевантность требует подтверждения на данных кампании.",
        "selection_criteria": item.get("selection_criteria") if item.get("selection_criteria") is not None else {},
        "risk_or_limitation": risk,
        "matched_target_group": matched_group,
        "is_existing_target_group": bool(matched_group),
        "segment_source": _normalise_segment_source(item.get("segment_source"), bool(matched_group)),
        "demo_insight": _safe_text(item.get("demo_insight")),
        "estimated_reach_label": _normalise_reach_label(item.get("estimated_reach_label")),
        "confidence": _normalise_confidence(item.get("confidence")),
    }


def _compact_group_match(candidate: Any, compact_groups: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidate_match = _candidate_matched_model(candidate)
    if not candidate_match:
        return None
    for group in compact_groups:
        group_id = group.get("id")
        group_name = str(group.get("name") or "")
        id_matches = (
            candidate_match.target_group_id is not None
            and str(candidate_match.target_group_id) == str(group_id)
        )
        name_matches = bool(
            candidate_match.name
            and group_name
            and _normalise_text(candidate_match.name) == _normalise_text(group_name)
        )
        clients_count = _normalise_clients_count(group.get("clients_count"))
        if id_matches and (not candidate_match.name or name_matches):
            return {"id": group_id, "name": group_name, "clients_count": clients_count}
        if candidate_match.target_group_id is None and name_matches:
            return {"id": group_id, "name": group_name, "clients_count": clients_count}
    return None


def _safe_text(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return ""
    replacements = [
        (r"(?i)(target group|целевая группа|цг)[^.!?]{0,40}(создан[аыо]?|заведен[аыо]?|сформирован[аыо]?)",
         "Target Group не создана автоматически; требуется отдельная настройка"),
        (r"(?i)(создан[аыо]?|заведен[аыо]?|сформирован[аыо]?)[^.!?]{0,40}(target group|целевая группа|цг)",
         "Target Group не создана автоматически; требуется отдельная настройка"),
        (r"(?i)(согласия|consent|opt[- ]?out|контактн\w* политик\w*|frequency cap|юридическ\w* ограничен\w*)[^.!?]{0,40}(проверен[ыао]?|учтен[ыао]?|валидирован[ыао]?)",
         "согласия, контактные политики и юридические ограничения требуют отдельной проверки"),
        (r"(?i)(проверен[ыао]?|учтен[ыао]?|валидирован[ыао]?)[^.!?]{0,40}(согласия|consent|opt[- ]?out|контактн\w* политик\w*|frequency cap|юридическ\w* ограничен\w*)",
         "согласия, контактные политики и юридические ограничения требуют отдельной проверки"),
        (r"(?i)(target group|целевая группа|цг)[^.!?]{0,50}(уже доступн[аыо]?|доступн[аыо]? для запуск[а-я]*)",
         "Target Group требует отдельной настройки перед использованием"),
        (r"(?i)(уже доступн[аыо]?|доступн[аыо]? для запуск[а-я]*)[^.!?]{0,50}(target group|целевая группа|цг)",
         "Target Group требует отдельной настройки перед использованием"),
        (r"(?i)(target group|целевая группа|цг)[^.!?]{0,80}(готов[аыо]? к запуск[а-я]*)",
         "Target Group требует отдельной настройки перед запуском"),
        (r"(?i)(готов[аыо]? к запуск[а-я]*)[^.!?]{0,80}(target group|целевая группа|цг)",
         "Target Group требует отдельной настройки перед запуском"),
        (r"(?i)(цг|target group|целевая группа) сформирован[аыо]?",
         "Target Group не создана автоматически; требуется отдельная настройка"),
    ]
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text)
    text = re.sub(
        r"(?i)(уже доступн[аыо]?|доступн[аыо]? для запуск[а-я]*)[^.!?]*",
        "доступность Target Group требует отдельной проверки",
        text,
    )
    text = re.sub(
        r"(?i)(готов[аыо]? к запуск[а-я]*)[^.!?]*",
        "готовность к запуску требует отдельной проверки",
        text,
    )
    text = re.sub(
        r"(?i)(?:точн(?:ый|ая|ое) размер сегмента|размер сегмента|сегмент (?:составит|составляет|включает))[^.!?]*(?:\d[\d\s]*(?:клиент|абонент|пользователь)[а-я]*)",
        "размер сегмента требует отдельного расчёта",
        text,
    )
    return text.strip()


def _normalise_segment_source(value: Any, has_matched_group: bool) -> str:
    if has_matched_group:
        return "existing_target_group"
    return "llm_composed_demo"


def _normalise_reach_label(value: Any) -> str:
    label = _safe_text(value)
    allowed = {"высокий": "Высокий", "средний": "Средний", "низкий": "Низкий"}
    normalised = _normalise_text(label)
    return allowed.get(normalised, label if label in allowed.values() else "Средний")


def _normalise_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        confidence = 0.5
    return round(max(0.0, min(confidence, 1.0)), 2)


def _generic_raw_response(compact_groups: list[dict[str, Any]]) -> _RawSegmentResponse:
    return _RawSegmentResponse(hypotheses=[
        _RawSegmentHypothesis(
            name="Базовая рекомендательная гипотеза",
            audience_description="Клиенты с потенциальной релевантностью к кампании.",
            relevance_reason="Гипотеза требует подтверждения на доступных данных.",
            selection_criteria={},
            risk_or_limitation="Требуется отдельная проверка размера, контактных ограничений и юридических требований.",
            matched_target_group=compact_groups[0] if compact_groups else None,
            is_existing_target_group=bool(compact_groups),
            segment_source="existing_target_group" if compact_groups else "llm_composed_demo",
            demo_insight="Базовая эвристика без prod-интеграции.",
            estimated_reach_label="Средний",
            confidence=0.5,
        ),
        _RawSegmentHypothesis(
            name="Альтернативная рекомендательная гипотеза",
            audience_description="Похожая аудитория для тестового запуска без автоматического создания Target Group.",
            relevance_reason="Подходит для ручной аналитической проверки перед запуском.",
            selection_criteria={},
            risk_or_limitation="Это только рекомендация: Target Group должна быть подтверждена по справочнику перед использованием.",
            matched_target_group=None,
            is_existing_target_group=False,
            segment_source="llm_composed_demo",
            demo_insight="Демо-гипотеза для ручной проверки аналитиком.",
            estimated_reach_label="Низкий",
            confidence=0.45,
        ),
    ])


def _to_segment_hypothesis(
    raw: _RawSegmentHypothesis,
    target_groups: list[dict[str, Any]],
    request: SegmentSuggestRequest,
    priority: int,
) -> SegmentHypothesis:
    selection_criteria = _normalise_selection_criteria(raw.selection_criteria, request)
    candidate_match = _candidate_matched_model(raw.matched_target_group)

    hypothesis = SegmentHypothesis(
        name=raw.name.strip(),
        audience_description=raw.audience_description.strip(),
        relevance_reason=raw.relevance_reason.strip(),
        selection_criteria=selection_criteria,
        risk_or_limitation=raw.risk_or_limitation.strip(),
        matched_target_group=candidate_match,
        is_existing_target_group=bool(raw.is_existing_target_group and candidate_match),
        segment_source=raw.segment_source,
        demo_insight=raw.demo_insight.strip(),
        estimated_reach_label=_normalise_reach_label(raw.estimated_reach_label),
        confidence=round(max(0.0, min(raw.confidence, 1.0)), 2),
        # Legacy fields retained for the current UI/tests.
        title=raw.name.strip(),
        description=raw.audience_description.strip(),
        rationale=raw.relevance_reason.strip(),
        product_fit=f"Сегмент применим для продукта «{request.product}».",
        expected_effect=f"Поддерживает цель кампании: {request.campaign_goal}.",
        audience_filters=selection_criteria,
        matched_target_groups=[],
        exclusions=_build_exclusions(request),
        priority=priority,
    )

    matched = _match_existing_target_group(hypothesis, target_groups)
    if matched is None:
        recommendation_note = "Это только рекомендация: уверенного совпадения с существующей Target Group не найдено."
        if recommendation_note not in hypothesis.risk_or_limitation:
            hypothesis.risk_or_limitation = f"{hypothesis.risk_or_limitation} {recommendation_note}".strip()
        hypothesis.matched_target_group = None
        hypothesis.matched_target_groups = []
        hypothesis.is_existing_target_group = False
        hypothesis.segment_source = "llm_composed_demo"
        if "recommendation-only/demo-only" not in hypothesis.risk_or_limitation:
            hypothesis.risk_or_limitation = f"{hypothesis.risk_or_limitation} recommendation-only/demo-only.".strip()
        if not hypothesis.demo_insight:
            hypothesis.demo_insight = "Новый демо-сегмент требует ручной сборки и валидации вне AdTarget."
    else:
        hypothesis.matched_target_group = matched
        hypothesis.matched_target_groups = [matched]
        hypothesis.is_existing_target_group = True
        hypothesis.segment_source = "existing_target_group"
        if not hypothesis.demo_insight:
            hypothesis.demo_insight = "Совпадение подтверждено справочником Target Groups; дополнительные политики требуют проверки."

    return hypothesis


def _candidate_matched_model(candidate: Any) -> MatchedTargetGroup | None:
    if not candidate:
        return None
    if isinstance(candidate, MatchedTargetGroup):
        return candidate.model_copy(update={
            "clients_count": _normalise_clients_count(candidate.clients_count),
        })
    if isinstance(candidate, dict):
        raw_id = candidate.get("target_group_id") or candidate.get("id")
        try:
            target_group_id = int(raw_id) if raw_id is not None else None
        except (TypeError, ValueError):
            target_group_id = None
        name = str(candidate.get("name") or candidate.get("title") or "").strip()
        if target_group_id is None and not name:
            return None
        return MatchedTargetGroup(
            target_group_id=target_group_id,
            name=name,
            clients_count=_normalise_clients_count(_clients_count_value(candidate)),
            match_score=0.0,
            match_reasons=[],
        )
    name = str(candidate).strip()
    if not name:
        return None
    return MatchedTargetGroup(name=name, match_score=0.0, match_reasons=[])


def _score_target_group_match(hypothesis: SegmentHypothesis, target_group: dict[str, Any]) -> float:
    """Score how well a hypothesis-confirmed candidate matches one AdTarget group."""
    group_id = target_group.get("id")
    group_name = str(target_group.get("name") or target_group.get("title") or "")
    candidate = hypothesis.matched_target_group

    score = 0.0
    if candidate:
        if candidate.target_group_id is not None and group_id is not None:
            score += 0.55 if str(candidate.target_group_id) == str(group_id) else 0.0
        candidate_name = candidate.name or ""
        if candidate_name and group_name:
            if _normalize_segment_text(candidate_name) == _normalize_segment_text(group_name):
                score += 0.55
            else:
                candidate_tokens = _tokens(candidate_name)
                group_tokens = _tokens(group_name)
                if candidate_tokens and group_tokens:
                    score += 0.35 * (len(candidate_tokens & group_tokens) / len(candidate_tokens | group_tokens))

    hypothesis_tokens = _tokens(" ".join([
        hypothesis.name,
        hypothesis.audience_description,
        hypothesis.relevance_reason,
        _stringify(hypothesis.selection_criteria),
    ]))
    group_tokens = _tokens(group_name)
    if hypothesis_tokens and group_tokens:
        score += min(0.15, 0.03 * len(hypothesis_tokens & group_tokens))

    return round(max(0.0, min(score, 1.0)), 4)


def _match_existing_target_group(
    hypothesis: SegmentHypothesis,
    target_groups: list[dict[str, Any]],
) -> MatchedTargetGroup | None:
    """Return a verified AdTarget group match, or None for recommendation-only hypotheses."""
    candidate = hypothesis.matched_target_group
    if not candidate or not target_groups:
        return None

    groups_by_id = {str(group.get("id")): group for group in target_groups if group.get("id") is not None}
    candidate_name = candidate.name or ""

    if candidate.target_group_id is not None:
        group = groups_by_id.get(str(candidate.target_group_id))
        if group is None:
            return None
        group_name = str(group.get("name") or group.get("title") or "")
        if candidate_name and _normalize_segment_text(candidate_name) != _normalize_segment_text(group_name):
            return None
        score = _score_target_group_match(hypothesis, group)
        if score < _MATCH_THRESHOLD:
            return None
        return _matched_model(group, score, ["Target Group id подтверждён справочником AdTarget"])

    best_group: dict[str, Any] | None = None
    best_score = 0.0
    for group in target_groups:
        group_name = str(group.get("name") or group.get("title") or "")
        if not candidate_name or not group_name:
            continue
        score = _score_target_group_match(hypothesis, group)
        if score > best_score:
            best_group = group
            best_score = score

    if best_group is None or best_score < _MATCH_THRESHOLD:
        return None
    return _matched_model(best_group, best_score, ["название Target Group подтверждено справочником AdTarget"])


def _resolve_matched_group(candidate: Any, target_groups: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, float, list[str]]:
    candidate_match = _candidate_matched_model(candidate)
    if not candidate_match:
        return None, 0.0, []
    hypothesis = SegmentHypothesis(
        matched_target_group=candidate_match,
        confidence=0.0,
        priority=1,
    )
    matched = _match_existing_target_group(hypothesis, target_groups)
    if not matched:
        best_score = max((_score_target_group_match(hypothesis, group) for group in target_groups), default=0.0)
        return None, best_score, []
    for group in target_groups:
        if str(group.get("id")) == str(matched.target_group_id):
            return group, matched.match_score, matched.match_reasons
    return None, matched.match_score, matched.match_reasons


def _matched_model(group: dict[str, Any], score: float, reasons: list[str]) -> MatchedTargetGroup:
    return MatchedTargetGroup(
        target_group_id=group.get("id"),
        name=str(group.get("name") or group.get("title") or "Без названия"),
        clients_count=_normalise_clients_count(_clients_count_value(group)),
        match_score=round(max(0.0, min(score, 1.0)), 2),
        match_reasons=reasons,
    )


def _normalise_selection_criteria(criteria: Any, request: SegmentSuggestRequest) -> dict[str, Any]:
    if isinstance(criteria, dict):
        normalised = dict(criteria)
    elif isinstance(criteria, list):
        normalised = {"criteria": criteria}
    elif criteria:
        normalised = {"criteria": str(criteria)}
    else:
        normalised = {}
    if request.audience_constraints:
        normalised.setdefault("request_constraints", request.audience_constraints)
    if request.demo_contact_base_profile:
        normalised.setdefault("demo_contact_base_profile_used", True)
    return normalised


def _fallback_raw_response(request: SegmentSuggestRequest, compact_groups: list[dict[str, Any]]) -> _RawSegmentResponse:
    primary = compact_groups[0] if compact_groups else None
    secondary = compact_groups[1] if len(compact_groups) > 1 else None
    hypotheses = [
        _RawSegmentHypothesis(
            name=f"Высокий потенциал для {request.product}",
            audience_description=f"Клиенты, для которых продукт «{request.product}» может закрыть текущую потребность.",
            relevance_reason=f"Сегмент напрямую связан с целью кампании: {request.campaign_goal}.",
            selection_criteria={"product_interest": request.product, "goal": request.campaign_goal},
            risk_or_limitation="Нужна проверка размера и частотных ограничений перед запуском.",
            matched_target_group=primary,
            is_existing_target_group=bool(primary),
            segment_source="existing_target_group" if primary else "llm_composed_demo",
            demo_insight="Резервная гипотеза на базе продукта и цели кампании.",
            estimated_reach_label="Средний",
            confidence=0.68 if primary else 0.52,
        ),
        _RawSegmentHypothesis(
            name="Похожие клиенты без недавнего контакта",
            audience_description="Клиенты с релевантным профилем, которых можно безопасно протестировать без перегруза коммуникациями.",
            relevance_reason="Подходит для A/B-проверки спроса и контроля инкрементального эффекта.",
            selection_criteria={"exclude_recent_contacts": True, "constraints": request.audience_constraints},
            risk_or_limitation="Это только рекомендация: уверенного совпадения с существующей Target Group не найдено.",
            matched_target_group=secondary,
            is_existing_target_group=bool(secondary),
            segment_source="existing_target_group" if secondary else "llm_composed_demo",
            demo_insight="Резервная demo-only гипотеза для A/B-проверки.",
            estimated_reach_label="Низкий",
            confidence=0.6 if secondary else 0.5,
        ),
    ]
    return _RawSegmentResponse(hypotheses=hypotheses)


def _build_exclusions(request: SegmentSuggestRequest) -> list[str]:
    exclusions = ["клиенты с активным opt-out по выбранным каналам"]
    days = request.audience_constraints.get("exclude_recent_contacts_days") if request.audience_constraints else None
    if days:
        exclusions.append(f"клиенты с коммуникацией за последние {days} дней")
    return exclusions


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-zа-я0-9]+", _normalize_segment_text(text)))


def _normalize_segment_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("ё", "е").strip().lower())


def _normalise_text(text: str | None) -> str:
    return _normalize_segment_text(text or "")


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(f"{key} {_stringify(item)}" for key, item in value.items())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_stringify(item) for item in value)
    return str(value)


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


def _demo_contact_base_profile(request: SegmentSuggestRequest) -> dict[str, Any]:
    if request.demo_contact_base_profile:
        return request.demo_contact_base_profile
    try:
        from tools.mock_data import MOCK_CONTACT_BASE_PROFILE
    except ImportError:
        return {}
    return MOCK_CONTACT_BASE_PROFILE
