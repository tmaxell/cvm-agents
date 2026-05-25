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

from agents.builder.terminology import (
    GOAL_TERMS,
    LLM_DICTIONARY_HINT,
    is_goal_phrase,
    looks_like_audience,
    looks_like_product,
)
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
    # Пользователь явно сказал «тариф не важен» — кампания собирается без
    # привязки к конкретному продукту, поле product не должно блокировать сборку.
    product_optional: bool = False


# ── Quick deterministic extraction (для уменьшения нагрузки на LLM) ───────────

# Захватываем 1-4 слова после маркера продукта; стоп-точки — знаки препинания и переходные слова.
_PRODUCT_PATTERNS = [
    re.compile(r"(?:продукт[ауеа]?|тариф|услуг[аеу]|пакет)\s+[«\"']?([\w][\w\s\-]{1,40}?)[»\"']?(?=[.,;:!?\n]|\s+(?:для|канал|через|с|и|на)\b|$)", re.IGNORECASE),
]

# Бизнес-цель: «для апсейла», «цель — удержание», «задача — реактивация».
_GOAL_PATTERNS = [
    re.compile(r"для\s+(апсейл\w*|апсэйл\w*|ап[- ]сейл\w*|upsell\w*|кросс[- ]сейл\w*|cross[- ]sell\w*|удержани\w*|retention|реактивац\w*|reactivat\w*|активаци\w*|activation|онбординг\w*|onboarding|лояльност\w*|монетизац\w*|churn|оттока?\w*|конверси\w*|стимулирован\w*)", re.IGNORECASE),
    re.compile(r"(?:цель|задач[аи]|задача)\s*[—\-:]\s*([^.,;\n]{3,80})", re.IGNORECASE),
]

_CHANNEL_MARKERS = {
    "sms":   ("sms", "смс", "смска"),
    "push":  ("mobile push", "пуш", "push-уведомлен", "push уведомлен", " push "),
    "email": ("email", "имейл", "почт", "e-mail"),
    "ussd":  ("ussd",),
}

# Аудитория — захват ограничен 1-5 словами и обязательно требует audience-hint
# в захваченном фрагменте; иначе считаем что это не аудитория.
_AUDIENCE_PATTERNS = [
    re.compile(r"(?:target group|таргет\s*групп[аыу]?|аудитори[яюей]|сегмент[ауе]?)\s+([^.,;\n]{3,80})", re.IGNORECASE),
    re.compile(r"для\s+((?:[\w-]+\s+){0,4}(?:клиент\w*|абонент\w*|пользоват\w*|сегмент\w*|групп\w*|семь\w*|молодёж\w*|молодеж\w*|подрост\w*|пенсион\w*|корпорат\w*|оттока?\w*))", re.IGNORECASE),
]


def _quick_extract(message: str, history: list[dict]) -> CampaignBriefAnalysis:
    """Беглое извлечение полей из текущего сообщения и истории — без LLM."""
    text_parts = [message]
    for h in history[-8:]:
        if h.get("role") == "user":
            text_parts.append(str(h.get("content", "")))
    combined = " ".join(text_parts)
    lower = combined.lower()

    # 1. Goal — выделяется ПЕРВЫМ, чтобы потом отфильтровать его из audience.
    goal: str | None = None
    for pattern in _GOAL_PATTERNS:
        m = pattern.search(combined)
        if m:
            goal_text = m.group(1).strip().rstrip(".,;")
            if len(goal_text) >= 3:
                goal = goal_text[:80]
                break

    # 2. Product — только если захваченный текст не выглядит как goal.
    product: str | None = None
    for pattern in _PRODUCT_PATTERNS:
        m = pattern.search(combined)
        if m:
            cand = m.group(1).strip().rstrip("»\"")
            if cand and not is_goal_phrase(cand):
                product = cand[:80]
                break

    # 3. Channels.
    channels: list[str] = []
    for ch, markers in _CHANNEL_MARKERS.items():
        if any(marker in lower for marker in markers):
            channels.append(ch)
    seen: set[str] = set()
    channels = [c for c in channels if not (c in seen or seen.add(c))]

    # 4. Audience — только если в захваченной фразе есть audience-hint И это не goal.
    audience: dict[str, Any] = {}
    for pattern in _AUDIENCE_PATTERNS:
        m = pattern.search(combined)
        if m:
            desc = m.group(1).strip().rstrip(".,;")
            if (
                len(desc) >= 3
                and not is_goal_phrase(desc)
                and not desc.lower().startswith(("кампан", "продукт", "тариф", "канал"))
                and looks_like_audience(desc)
            ):
                audience = {"description": desc[:120]}
                break

    return CampaignBriefAnalysis(
        product=product,
        goal=goal,
        audience=audience,
        channels=channels,
    )


# ── LLM brief analyzer ────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """Ты — аналитик CVM-кампаний. По диалогу с пользователем извлеки структурированный бриф кампании.

""" + LLM_DICTIONARY_HINT + """

Извлеки:
- product: какой продукт/тариф/услугу продвигаем (например, "Тариф Семейный", "Пакет 5GB"). Если пользователь сказал «тариф не важен» или явно упомянул только цель — оставь null.
- goal: бизнес-цель в нормализованном виде (например "апсейл", "удержание", "реактивация"). Опирайся на словарь GOAL выше — слова из него ВСЕГДА идут в goal, не в audience.
- audience: {description, target_groups[]} — описание ЦА. Опирайся на словарь AUDIENCE.
- channels: массив каналов (sms / push / email / ussd).
- scenario: одно из значений ниже:
    * "single_touch" — простое разовое касание (Push один раз).
    * "trigger_with_activation" — реакция на бизнес-событие + активация продукта.
    * "two_step_with_response" — касание + ожидание отклика + действие.
    * "lifecycle_with_transfer" — многоэтапная с переводом в следующую кампанию.
    * "multi_touch_with_wait" — серия касаний с паузами между ними.
    * "unknown" — пока непонятно.

Определи missing_critical — поля, без которых нельзя качественно собрать кампанию. Включай в этот список ТОЛЬКО реально отсутствующие критичные поля. Минимум:
- "channels" — если канал не упомянут вообще.
- "audience" — если нет ни одной зацепки про аудиторию.
ВАЖНО: product НЕ ВСЕГДА обязателен. Если goal содержит retention/churn/реактивацию или пользователь сказал «тариф не важен» — НЕ добавляй product в missing_critical. Для апсейла продукт нужен (что апсейлим?), для удержания — нет.

Для каждого недостающего поля сгенерируй 1 короткий вопрос на русском в clarifying_questions.

Few-shot примеры:
- «Кампания для апсейла» → goal="апсейл", missing_critical=["product","channels","audience"], product/audience пустые.
- «Кампания для семей с детьми» → audience={"description":"семьи с детьми"}, missing=["product","channels"].
- «Хочу удержание VIP клиентов через SMS» → goal="удержание", audience="VIP клиенты", channels=["sms"], missing=[] (для retention продукт не нужен).
- «Тариф Семейный, SMS» → product="Тариф Семейный", channels=["sms"], missing=["audience"].

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
            if not parsed.goal and quick.goal:
                parsed.goal = quick.goal
            if not parsed.channels and quick.channels:
                parsed.channels = quick.channels
            if not parsed.audience and quick.audience:
                parsed.audience = quick.audience
            _post_process(parsed)
            _recompute_missing(parsed)
            return parsed
    except Exception as exc:
        logger.warning("brief analyzer LLM failed: %s", exc)

    # Fallback на quick + минимальная проверка missing.
    _post_process(quick)
    _recompute_missing(quick)
    return quick


def _post_process(brief: CampaignBriefAnalysis) -> None:
    """Перекладывает мисс-классифицированные поля.

    - Если audience.description выглядит как goal — переносим в goal, чистим audience.
    - Если product выглядит как goal — переносим в goal, чистим product.
    - Если product звучит как «продукт не важен» (от quick-reply «Тариф не важен»
      или явного отказа пользователя) — обнуляем product и помечаем
      product_explicitly_optional=True, чтобы _recompute_missing убрал
      product из missing_critical.
    """
    if brief.audience and brief.audience.get("description"):
        desc = str(brief.audience["description"]).strip()
        if is_goal_phrase(desc) and not looks_like_audience(desc):
            # Это была цель, маскирующаяся под аудиторию.
            if not brief.goal:
                # Нормализуем: оставим первый goal-term из текста.
                brief.goal = _normalize_goal(desc)
            brief.audience = {}
    if brief.product and _looks_like_product_optional(brief.product):
        brief.product = None
        brief.product_optional = True
        note = "Продукт указан как «не важен» — собираем кампанию без привязки к конкретному продукту."
        if note not in brief.notes:
            brief.notes = list(brief.notes) + [note]
    if brief.product and is_goal_phrase(brief.product) and not looks_like_product(brief.product):
        if not brief.goal:
            brief.goal = _normalize_goal(brief.product)
        brief.product = None


_PRODUCT_OPTIONAL_RE = re.compile(
    r"\b("
    # «важен / важна / важно / важный / важный» — корень «важ» + окончание.
    r"не\s*важ[енаоыий]\w*|неважн?[аоыий]\w*|"
    r"любо[йе]|любые|любой\s+продукт\w*|"
    r"на\s+ваш(е|у|ему)\s+усмотрен\w*|"
    r"без\s+разниц\w*|не\s+имеет\s+значен\w*|не\s+принципиал\w*|"
    r"all\s+products|any\s+product"
    r")\b",
    re.IGNORECASE,
)


def _looks_like_product_optional(text: str) -> bool:
    """True, если строка означает «продукт не важен / любой»."""
    return bool(text and _PRODUCT_OPTIONAL_RE.search(text))


def _normalize_goal(text: str) -> str:
    """Возвращает нормализованную форму goal-фразы (например, «апсейл»)."""
    lower = text.lower()
    for term in GOAL_TERMS:
        if term in lower:
            # Берём 1-2 слова от term — обычно достаточно.
            words = term.split()
            return " ".join(words)[:40]
    return text[:80]


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


_FIELD_QUESTIONS: dict[str, str] = {
    "product": "Какой продукт / тариф / услугу продвигаем?",
    "goal": "Какая бизнес-цель: апсейл, удержание, реактивация, активация?",
    "channels": "Через какой канал отправляем коммуникацию: SMS, Push, Email или USSD?",
    "audience": "На какую аудиторию? Например: «активные клиенты», «семьи с детьми», «отток за 30 дней».",
}


def _has_field(brief: CampaignBriefAnalysis, field_name: str) -> bool:
    if field_name == "product":
        # «product» считается заполненным и когда пользователь явно сказал «не важен» —
        # тогда сборка идёт без привязки к конкретному продукту.
        return bool(brief.product) or brief.product_optional
    if field_name == "goal":
        return bool(brief.goal)
    if field_name == "channels":
        return bool(brief.channels)
    if field_name == "audience":
        return bool(brief.audience and brief.audience.get("description"))
    return False


def _recompute_missing(brief: CampaignBriefAnalysis) -> None:
    """Финальная сверка missing_critical с реальным состоянием полей брифа.

    Правила:
    - Если у нас есть goal вроде retention/churn — product необязателен.
    - Если у нас уже заполнен поле — убираем его из missing (даже если LLM по ошибке оставил).
    - Если у нас нет канала или аудитории — обязательно спрашиваем.
    """
    retention_like = bool(brief.goal and any(
        term in brief.goal.lower() for term in ("удержани", "retention", "churn", "отток", "реактивац", "reactivat")
    ))

    must_have: list[str] = []
    if not retention_like and not brief.product and not brief.product_optional:
        must_have.append("product")
    if not brief.channels:
        must_have.append("channels")
    if not (brief.audience and brief.audience.get("description")):
        must_have.append("audience")

    # Берём union LLM-missing и наших, но УБИРАЕМ те, что уже заполнены.
    combined: list[str] = []
    for field_name in (list(brief.missing_critical or []) + must_have):
        if field_name in combined:
            continue
        if _has_field(brief, field_name):
            continue
        combined.append(field_name)

    brief.missing_critical = combined

    # Перегенерируем вопросы под актуальный missing — LLM-вопросы могут содержать уже отвеченные.
    questions: list[str] = []
    used_keywords: set[str] = set()
    # Сначала пробуем переиспользовать LLM-вопросы для полей, которые ещё missing.
    for q in list(brief.clarifying_questions or []):
        ql = q.lower()
        matched_field: str | None = None
        for field_name in combined:
            if field_name in used_keywords:
                continue
            keywords = _QUESTION_KEYWORDS.get(field_name, ())
            if any(kw in ql for kw in keywords):
                matched_field = field_name
                break
        if matched_field:
            questions.append(q)
            used_keywords.add(matched_field)
    # Добавляем стандартные вопросы для непокрытых полей.
    for field_name in combined:
        if field_name in used_keywords:
            continue
        if field_name in _FIELD_QUESTIONS:
            questions.append(_FIELD_QUESTIONS[field_name])
    brief.clarifying_questions = questions


_QUESTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "product": ("продукт", "тариф", "услуг", "пакет", "product"),
    "channels": ("канал", "channel", "sms", "email", "push", "ussd"),
    "audience": ("аудитор", "клиент", "сегмент", "таргет", "audience", "group"),
    "goal": ("цель", "задач", "goal"),
}


def is_ready_to_build(brief: CampaignBriefAnalysis) -> bool:
    """Готов ли бриф к сборке.

    Сборка допустима, если у нас есть:
    - канал И аудитория И (продукт ИЛИ goal вида retention/churn).
    """
    has_channel = bool(brief.channels)
    has_audience = bool(brief.audience and brief.audience.get("description"))
    has_product = bool(brief.product)
    retention_like = bool(brief.goal and any(
        term in brief.goal.lower() for term in ("удержани", "retention", "churn", "отток", "реактивац", "reactivat")
    ))
    # product_optional: пользователь явно сказал «тариф не важен» — продукт
    # не блокирует сборку даже если goal не retention-like.
    has_subject = has_product or retention_like or brief.product_optional
    return has_channel and has_audience and has_subject
