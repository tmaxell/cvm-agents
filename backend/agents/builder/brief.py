"""Brief analyzer — LLM извлекает структурированный бриф кампании и проверяет полноту.

Цикл взаимодействия:
1. Пользователь даёт запрос («Собери кампанию для Тарифа Семейный, канал SMS»).
2. analyze_brief() читает текущее сообщение + историю и возвращает Brief +
   список недостающих критичных полей + готовые вопросы пользователю.
3. Если есть missing_critical → BuilderAgent отдаёт needs_input с вопросами
   и НЕ собирает flow.
4. После ответа пользователя history содержит ответы — повторный analyze_brief
   уже наполняет бриф, и Builder строит flow с planner.

Минимальные критичные поля:
- product (что продвигаем / тариф / услуга)
- channels (как доносим: SMS/Email/Push/USSD) — хотя бы один
- audience (целевая аудитория) — может быть «все клиенты», но лучше уточнить

Поле goal/scenario желательное, но не блокирующее: при отсутствии — берём
безопасный дефолт «single_touch_with_trigger».
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage

from llm import get_llm

logger = logging.getLogger(__name__)


ScenarioShape = Literal[
    "single_touch",                # одно касание (Push)
    "trigger_with_activation",     # Event → BT (например, начисление при событии)
    "two_step_with_response",      # Push → Response → BT
    "lifecycle_with_transfer",     # многоэтапная с TransferToCampaign
    "multi_touch_with_wait",       # цепочка касаний с Wait
    "unknown",
]


@dataclass(slots=True)
class CampaignBriefAnalysis:
    product: str | None = None
    goal: str | None = None
    audience: dict[str, Any] = field(default_factory=dict)   # {description, target_groups}
    channels: list[str] = field(default_factory=list)        # ["sms", "email", ...]
    scenario: ScenarioShape = "unknown"
    notes: list[str] = field(default_factory=list)
    missing_critical: list[str] = field(default_factory=list)
    clarifying_questions: list[str] = field(default_factory=list)
    confidence: float = 0.0


# ── Quick deterministic extraction (для уменьшения нагрузки на LLM) ───────────

_PRODUCT_PATTERNS = [
    re.compile(r"(?:продукт[ауеа]?|тариф|услуг[аеу]|пакет[а-я]*)\s+[«\"']?([^.,;\n«\"']+?)[»\"']?(?:[.,;]|$)", re.IGNORECASE),
]
_CHANNEL_MARKERS = {
    "sms":   ("sms", "смс", "смска"),
    "push":  ("mobile push", "пуш", "push-уведомлен", "push уведомлен"),
    "email": ("email", "имейл", "почт"),
    "ussd":  ("ussd",),
}
_AUDIENCE_MARKERS = [
    re.compile(r"(?:для|target group|таргет\s*групп[аыу]?|аудитори[яюей]|сегмент[ауе]?|клиент[ауов]*)\s+([^.,;\n]+)", re.IGNORECASE),
]


def _quick_extract(message: str, history: list[dict]) -> CampaignBriefAnalysis:
    """Беглое извлечение полей из текущего сообщения и истории — без LLM."""
    text_parts = [message]
    for h in history[-8:]:
        if h.get("role") == "user":
            text_parts.append(str(h.get("content", "")))
    combined = " ".join(text_parts)
    lower = combined.lower()

    product: str | None = None
    for pattern in _PRODUCT_PATTERNS:
        m = pattern.search(combined)
        if m:
            product = m.group(1).strip().rstrip("»\"")
            break

    channels: list[str] = []
    for ch, markers in _CHANNEL_MARKERS.items():
        if any(marker in lower for marker in markers):
            channels.append(ch)
    # Уникализируем сохраняя порядок.
    seen = set()
    channels = [c for c in channels if not (c in seen or seen.add(c))]

    audience: dict[str, Any] = {}
    for pattern in _AUDIENCE_MARKERS:
        m = pattern.search(combined)
        if m:
            desc = m.group(1).strip().rstrip(".,;")
            # Отфильтровываем явно технические слова.
            if len(desc) >= 3 and not desc.lower().startswith(("кампан", "продукт", "тариф", "канал")):
                audience = {"description": desc[:120]}
                break

    return CampaignBriefAnalysis(
        product=product,
        audience=audience,
        channels=channels,
    )


# ── LLM brief analyzer ────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """Ты — аналитик CVM-кампаний. По диалогу с пользователем извлеки структурированный бриф кампании.

Извлеки:
- product: какой продукт/тариф/услугу продвигаем (например, "Тариф Семейный", "Пакет 5GB").
- goal: бизнес-цель ("увеличение продаж", "удержание клиентов", "активация подарка").
- audience: {description, target_groups[]} — описание ЦА.
- channels: массив каналов (sms / push / email / ussd) — какие нужно использовать.
- scenario: одно из значений ниже:
    * "single_touch" — простое разовое касание (Push один раз).
    * "trigger_with_activation" — реакция на бизнес-событие + активация продукта (Event → BT).
    * "two_step_with_response" — касание + ожидание отклика + действие (Push → Response → BT).
    * "lifecycle_with_transfer" — многоэтапная с переводом в следующую кампанию.
    * "multi_touch_with_wait" — серия касаний с паузами между ними.
    * "unknown" — пока непонятно.

Определи missing_critical — поля, без которых нельзя качественно собрать кампанию. Включай в этот список ТОЛЬКО реально отсутствующие критичные поля. Минимум:
- "product" — если непонятно ЧТО продвигаем.
- "channels" — если канал не упомянут вообще.
- "audience" — если нет ни одной зацепки про аудиторию и контекст ничего не подсказывает.

Для каждого недостающего поля сгенерируй 1 короткий вопрос на русском в clarifying_questions.

Верни строго JSON одной строкой:
{"product":"...","goal":"...","audience":{"description":"...","target_groups":[]},"channels":["sms"],"scenario":"...","missing_critical":["..."],"clarifying_questions":["..."],"notes":["..."],"confidence":0.7}

Если поля нет — поставь null или пустой массив."""


async def analyze_brief(message: str, history: list[dict]) -> CampaignBriefAnalysis:
    """Главная точка входа. Сначала быстрая эвристика, потом LLM для уточнения."""
    quick = _quick_extract(message, history)

    try:
        llm = get_llm(temperature=0)
        history_excerpt = []
        for h in history[-8:]:
            role = h.get("role")
            if role in {"user", "assistant"}:
                content = str(h.get("content", ""))[:400]
                history_excerpt.append({"role": role, "content": content})
        history_excerpt.append({"role": "user", "content": message})

        prompt_input = {
            "messages": history_excerpt,
            "quick_extraction": {
                "product": quick.product,
                "channels": quick.channels,
                "audience": quick.audience,
            },
        }
        result = await llm.ainvoke([
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=json.dumps(prompt_input, ensure_ascii=False)),
        ])
        raw = getattr(result, "content", str(result))
        text = raw if isinstance(raw, str) else json.dumps(raw)
        parsed = _parse_brief(text)
        if parsed is not None:
            # Сливаем — отдаём приоритет LLM, но не теряем quick если LLM что-то упустил.
            if not parsed.product and quick.product:
                parsed.product = quick.product
            if not parsed.channels and quick.channels:
                parsed.channels = quick.channels
            if not parsed.audience and quick.audience:
                parsed.audience = quick.audience
            _recompute_missing(parsed)
            return parsed
    except Exception as exc:
        logger.warning("brief analyzer LLM failed: %s", exc)

    # Fallback на quick + минимальная проверка missing.
    _recompute_missing(quick)
    return quick


def _parse_brief(text: str) -> CampaignBriefAnalysis | None:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    channels = payload.get("channels") or []
    if not isinstance(channels, list):
        channels = []
    channels = [str(c).lower().strip() for c in channels if isinstance(c, (str, int))]
    channels = [c for c in channels if c in {"sms", "email", "push", "ussd"}]

    audience = payload.get("audience")
    if not isinstance(audience, dict):
        audience = {}

    scenario = payload.get("scenario") or "unknown"
    if scenario not in {"single_touch", "trigger_with_activation", "two_step_with_response",
                        "lifecycle_with_transfer", "multi_touch_with_wait", "unknown"}:
        scenario = "unknown"

    missing = payload.get("missing_critical") or []
    if not isinstance(missing, list):
        missing = []
    missing = [str(m) for m in missing if isinstance(m, str)]

    questions = payload.get("clarifying_questions") or []
    if not isinstance(questions, list):
        questions = []
    questions = [str(q).strip() for q in questions if isinstance(q, str) and q.strip()]

    confidence_val = payload.get("confidence", 0.5)
    try:
        confidence = float(confidence_val)
    except (TypeError, ValueError):
        confidence = 0.5

    return CampaignBriefAnalysis(
        product=_clean_str(payload.get("product")),
        goal=_clean_str(payload.get("goal")),
        audience=audience,
        channels=channels,
        scenario=scenario,  # type: ignore[arg-type]
        missing_critical=missing,
        clarifying_questions=questions,
        notes=[str(n) for n in (payload.get("notes") or []) if isinstance(n, str)][:5],
        confidence=max(0.0, min(1.0, confidence)),
    )


def _clean_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _recompute_missing(brief: CampaignBriefAnalysis) -> None:
    """Пересчитывает missing_critical если LLM не справился или его не вызывали.

    Правило: для качественной сборки нужны product + (channels или audience).
    Если у нас есть хотя бы один из (channels, audience), product становится критичным;
    если нет ни того ни другого — спрашиваем оба.
    """
    missing: list[str] = []
    questions: list[str] = []
    if not brief.product:
        missing.append("product")
        questions.append("Какой продукт / тариф / услугу продвигаем?")
    if not brief.channels:
        missing.append("channels")
        questions.append("Через какой канал отправляем коммуникацию: SMS, Push, Email или USSD?")
    if not brief.audience or not brief.audience.get("description"):
        missing.append("audience")
        questions.append("На какую аудиторию (целевая группа или критерий)? Например: «активные клиенты», «семьи с детьми», «отток за 30 дней».")

    # Если LLM уже указал какие-то поля — не дублируем.
    if brief.missing_critical:
        existing = set(brief.missing_critical)
        for m in missing:
            if m not in existing:
                brief.missing_critical.append(m)
        if not brief.clarifying_questions:
            brief.clarifying_questions = questions
    else:
        brief.missing_critical = missing
        brief.clarifying_questions = questions


def is_ready_to_build(brief: CampaignBriefAnalysis) -> bool:
    """Готов ли бриф к сборке. Допускаем сборку если product + (channels OR audience)."""
    has_product = bool(brief.product)
    has_channel = bool(brief.channels)
    has_audience = bool(brief.audience and brief.audience.get("description"))
    return has_product and (has_channel or has_audience)
