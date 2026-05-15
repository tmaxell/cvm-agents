"""Segment suggestion agent for campaign audience hypotheses.

The suggester is intentionally lightweight: it uses AdTarget target-group
references and deterministic scoring, so the endpoint can return structured
hypotheses without spending an LLM/tool cycle before the user opens Builder.
"""

from __future__ import annotations

import re
from typing import Any

from schemas import (
    MatchedTargetGroup,
    SegmentHypothesis,
    SegmentSuggestRequest,
    SegmentSuggestResponse,
)
from tools import adtarget


_MAX_HYPOTHESES = 3
_MIN_HYPOTHESES = 2


async def run(request: SegmentSuggestRequest) -> SegmentSuggestResponse:
    """Build 2-3 structured segment hypotheses for a product campaign."""
    target_groups = await _load_target_groups()
    scored_groups = _score_target_groups(request, target_groups)
    hypotheses = _build_hypotheses(request, scored_groups)

    warnings: list[str] = []
    if not target_groups:
        warnings.append("Справочник целевых групп недоступен; гипотезы построены без привязки к существующим ЦГ.")
    elif not any(group["score"] >= 0.35 for group in scored_groups):
        warnings.append("Не найдено сильных совпадений в справочнике ЦГ; проверьте аудиторию перед запуском.")

    return SegmentSuggestResponse(
        summary=(
            f"Подготовлено {len(hypotheses)} гипотезы сегментов для продукта "
            f"«{request.product}» и цели «{request.campaign_goal}»."
        ),
        hypotheses=hypotheses,
        warnings=warnings,
    )


async def _load_target_groups() -> list[dict[str, Any]]:
    try:
        result = await adtarget.list_target_groups()
    except Exception as exc:  # Defensive fallback: endpoint should still answer with unbound hypotheses.
        print(f"[segment_suggester] Target groups lookup failed: {type(exc).__name__}: {exc}")
        return []

    if isinstance(result, dict) and isinstance(result.get("items"), list):
        return [item for item in result["items"] if isinstance(item, dict)]
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)]
    return []


def _score_target_groups(
    request: SegmentSuggestRequest,
    target_groups: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    request_text = _normalise_text(
        " ".join(
            [
                request.product,
                request.campaign_goal,
                _stringify(request.audience_constraints),
                _stringify(request.current_campaign_context),
            ]
        )
    )
    desired_tokens = _tokens(request_text)

    scored: list[dict[str, Any]] = []
    for group in target_groups:
        name = str(group.get("name") or "")
        group_text = _normalise_text(name)
        score = 0.15
        reasons: list[str] = []

        overlap = sorted(desired_tokens.intersection(_tokens(group_text)))
        if overlap:
            score += min(0.35, 0.08 * len(overlap))
            reasons.append("совпали ключевые слова: " + ", ".join(overlap[:4]))

        for pattern, weight, reason in _SCORING_RULES:
            if re.search(pattern, request_text) and re.search(pattern, group_text):
                score += weight
                reasons.append(reason)

        if group.get("status") == "Active":
            score += 0.05
            reasons.append("ЦГ активна в AdTarget")

        scored.append({
            "id": group.get("id"),
            "name": name or "Без названия",
            "clients_count": group.get("clientsCount") or group.get("clients_count"),
            "score": min(score, 0.98),
            "reasons": reasons or ["базовое соответствие контексту кампании"],
        })

    scored.sort(key=lambda item: (item["score"], item.get("clients_count") or 0), reverse=True)
    return scored


def _build_hypotheses(
    request: SegmentSuggestRequest,
    scored_groups: list[dict[str, Any]],
) -> list[SegmentHypothesis]:
    archetypes = _select_archetypes(request)
    if len(archetypes) < _MIN_HYPOTHESES:
        archetypes.extend(_DEFAULT_ARCHETYPES)

    hypotheses: list[SegmentHypothesis] = []
    used_titles: set[str] = set()
    top_groups = scored_groups[:_MAX_HYPOTHESES] or [None] * _MAX_HYPOTHESES

    for index, archetype in enumerate(archetypes):
        if len(hypotheses) >= _MAX_HYPOTHESES:
            break
        if archetype["title"] in used_titles:
            continue
        used_titles.add(archetype["title"])

        primary = top_groups[index] if index < len(top_groups) else None
        alternatives = [group for group in scored_groups if group is not primary][:1]
        matched = [_to_matched_group(group) for group in [primary, *alternatives] if group]

        confidence = 0.62 + (0.18 * min(primary["score"], 1.0) if primary else 0.0)
        hypotheses.append(SegmentHypothesis(
            title=archetype["title"],
            description=archetype["description"].format(product=request.product),
            rationale=archetype["rationale"].format(goal=request.campaign_goal, product=request.product),
            product_fit=archetype["product_fit"].format(product=request.product),
            expected_effect=archetype["expected_effect"],
            audience_filters={
                **archetype["filters"],
                "request_constraints": request.audience_constraints,
            },
            matched_target_groups=matched,
            exclusions=_build_exclusions(request),
            priority=len(hypotheses) + 1,
            confidence=round(min(confidence, 0.92), 2),
        ))

    return hypotheses[:_MAX_HYPOTHESES]


def _to_matched_group(group: dict[str, Any]) -> MatchedTargetGroup:
    return MatchedTargetGroup(
        target_group_id=group.get("id") if isinstance(group.get("id"), int) else None,
        name=str(group.get("name") or "Без названия"),
        clients_count=group.get("clients_count") if isinstance(group.get("clients_count"), int) else None,
        match_score=round(float(group.get("score") or 0), 2),
        match_reasons=list(group.get("reasons") or []),
    )


def _select_archetypes(request: SegmentSuggestRequest) -> list[dict[str, Any]]:
    text = _normalise_text(f"{request.product} {request.campaign_goal} {_stringify(request.audience_constraints)}")
    selected: list[dict[str, Any]] = []
    for pattern, archetype in _ARCHETYPE_RULES:
        if re.search(pattern, text):
            selected.append(archetype)
    selected.extend(_DEFAULT_ARCHETYPES)
    return selected


def _build_exclusions(request: SegmentSuggestRequest) -> list[str]:
    exclusions = [
        "клиенты с активным отказом от маркетинговых коммуникаций",
        "клиенты, уже получавшие аналогичный оффер в текущей кампании",
    ]
    raw_constraints = _normalise_text(_stringify(request.audience_constraints))
    if "churn" in raw_constraints or "отток" in raw_constraints:
        exclusions.append("клиенты с открытыми претензиями или негативным NPS")
    if "vip" in raw_constraints or "arpu" in raw_constraints:
        exclusions.append("клиенты с просроченной задолженностью")
    return exclusions


def _normalise_text(value: str) -> str:
    return value.lower().replace("ё", "е")


def _tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-zа-я0-9]+", value) if len(token) > 2}


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(f"{key} {_stringify(item)}" for key, item in value.items())
    if isinstance(value, list):
        return " ".join(_stringify(item) for item in value)
    return str(value)


_SCORING_RULES = [
    (r"интернет|internet|data|данн|гб|пакет", 0.35, "релевантно интернет-/data-продукту"),
    (r"churn|отток|удерж", 0.30, "подходит для удержания и снижения оттока"),
    (r"спящ|реактив|неактив", 0.30, "подходит для реактивации неактивных клиентов"),
    (r"arpu|монет|выруч|доход|upsell", 0.25, "подходит для монетизации/upsell"),
    (r"нов(ые|ый)|онборд", 0.25, "подходит для онбординга новых клиентов"),
    (r"корп|b2b", 0.25, "релевантно корпоративному сегменту"),
    (r"vip|преми", 0.25, "релевантно премиальному сегменту"),
]

_DATA_ARCHETYPE = {
    "title": "Data-need: высокий потенциал потребления интернета",
    "description": "Клиенты, которым может быть нужен продукт «{product}» из-за высокого или растущего интернет-потребления.",
    "rationale": "Цель «{goal}» лучше проверять на аудитории с явным data-поведением и понятной потребностью в продукте.",
    "product_fit": "«{product}» закрывает обнаруженную потребность в дополнительном или более удобном объёме данных.",
    "expected_effect": "рост конверсии в подключение и снижение нерелевантных контактов",
    "filters": {"usage_signal": "data_usage_high_or_growing", "need_state": "internet_package_need"},
}

_CHURN_ARCHETYPE = {
    "title": "Retention: риск оттока с продуктовым стимулом",
    "description": "Клиенты с признаками оттока, для которых «{product}» может стать поводом остаться активными.",
    "rationale": "Для цели «{goal}» важно проверить сегмент, где продуктовый оффер одновременно решает retention-задачу.",
    "product_fit": "«{product}» используется как ценностный аргумент для удержания и восстановления вовлечённости.",
    "expected_effect": "снижение churn-risk и рост повторной активности",
    "filters": {"risk_signal": "high_churn_or_inactivity", "contact_strategy": "retention_offer"},
}

_MONETIZATION_ARCHETYPE = {
    "title": "Upsell: готовность к апгрейду продукта",
    "description": "Клиенты с потенциалом допродажи, которым можно предложить «{product}» как следующий шаг.",
    "rationale": "Цель «{goal}» требует аудитории с вероятностью покупки, а не только широкой охватной базы.",
    "product_fit": "«{product}» позиционируется как апгрейд текущего потребления или тарифа.",
    "expected_effect": "увеличение ARPU и доли платных подключений",
    "filters": {"value_signal": "medium_high_arpu_or_upsell_ready", "offer_type": "paid_upgrade"},
}

_ONBOARDING_ARCHETYPE = {
    "title": "Onboarding: раннее знакомство с продуктом",
    "description": "Новые клиенты, которым можно встроить «{product}» в первые сценарии использования.",
    "rationale": "Для цели «{goal}» ранний контакт помогает сформировать привычку к продукту до снижения активности.",
    "product_fit": "«{product}» объясняется как простой стартовый сценарий для нового клиента.",
    "expected_effect": "рост ранней активации и долгосрочной вовлечённости",
    "filters": {"lifecycle_stage": "new_customer", "tenure_days": "<=30"},
}

_DEFAULT_ARCHETYPES = [_DATA_ARCHETYPE, _MONETIZATION_ARCHETYPE, _CHURN_ARCHETYPE]
_ARCHETYPE_RULES = [
    (r"интернет|internet|data|данн|гб|пакет", _DATA_ARCHETYPE),
    (r"churn|отток|удерж|спящ|реактив", _CHURN_ARCHETYPE),
    (r"arpu|монет|выруч|доход|upsell|продаж", _MONETIZATION_ARCHETYPE),
    (r"нов(ые|ый)|онборд", _ONBOARDING_ARCHETYPE),
]
