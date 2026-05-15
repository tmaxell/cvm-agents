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
from pydantic import BaseModel, Field, ValidationError

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
            "clients_count": group.get("clientsCount") or group.get("clients_count"),
            "status": group.get("status"),
        })
    return compact


async def _ask_llm(request: SegmentSuggestRequest, compact_groups: list[dict[str, Any]]) -> _RawSegmentResponse:
    llm = get_llm(for_tools=False)
    response = await llm.ainvoke([
        SystemMessage(content=(
            "Ты CVM-аналитик и предлагаешь сегменты аудитории для маркетинговой кампании. "
            "Верни только валидный JSON без markdown и комментариев. "
            "Строгая схема ответа: {\"hypotheses\":[{\"name\":string,"
            "\"audience_description\":string,\"relevance_reason\":string,"
            "\"selection_criteria\":object|string,\"risk_or_limitation\":string,"
            "\"matched_target_group\":object|null,\"is_existing_target_group\":boolean,"
            "\"confidence\":number}]}. "
            "Верни строго 2–3 гипотезы. Для matched_target_group используй только существующую Target Group "
            "из списка и указывай минимум id и name. Если нет уверенного совпадения с Target Group, "
            "поставь matched_target_group=null, is_existing_target_group=false и явно напиши в "
            "risk_or_limitation, что это только рекомендация, а не существующая ЦГ. "
            "Не выдумывай id или названия Target Groups. confidence — число от 0 до 1."
        )),
        HumanMessage(content=json.dumps({
            "request": request.model_dump(),
            "existing_target_groups": compact_groups,
        }, ensure_ascii=False)),
    ])
    content = response.content if hasattr(response, "content") else str(response)
    return _parse_raw_response(content)


def _parse_raw_response(content: Any) -> _RawSegmentResponse:
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
    try:
        return _RawSegmentResponse.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"LLM returned invalid segment JSON: {exc}") from exc


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
    else:
        hypothesis.matched_target_group = matched
        hypothesis.matched_target_groups = [matched]
        hypothesis.is_existing_target_group = True

    return hypothesis


def _candidate_matched_model(candidate: Any) -> MatchedTargetGroup | None:
    if not candidate:
        return None
    if isinstance(candidate, MatchedTargetGroup):
        return candidate
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
            clients_count=candidate.get("clientsCount") or candidate.get("clients_count"),
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
        clients_count=group.get("clientsCount") or group.get("clients_count"),
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
