"""Intent classifier — LLM-first роутер с few-shot примерами.

Стратегия:
1. Очень узкие правила-shortcuts ловят только однозначные command-style фразы
   («создай кампанию X», «доработай кампанию 24», «сохрани сегмент»).
   Эти правила НЕ срабатывают на вопросах («как создать кампанию?»).
2. Во всех остальных случаях вызываем LLM с system-промптом + few-shot примерами,
   которые явно показывают разницу «как создать» (docs) vs «создай» (builder).
3. Если LLM недоступен — возвращаемся к documentation_qa (безопасный default).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage

from llm import get_llm

logger = logging.getLogger(__name__)


IntentName = Literal[
    "campaign_attention",
    "build_campaign",
    "suggest_segments",
    "refine_campaign",
    "documentation_qa",
]


@dataclass(slots=True)
class IntentDecision:
    intent: IntentName
    confidence: float
    reason: str = ""


_VALID_INTENTS = {
    "campaign_attention",
    "build_campaign",
    "suggest_segments",
    "refine_campaign",
    "documentation_qa",
}


# Очень узкие command-style паттерны: ловят однозначные команды.
# Регэксы используют границы слов, чтобы НЕ совпадать с «как создать», «когда создавать».
_RULES: list[tuple[IntentName, list[re.Pattern[str]]]] = [
    (
        "build_campaign",
        [
            re.compile(r"\b(создай|собери|сделай|оформи|запусти сборку)\s+(новую\s+)?кампани", re.IGNORECASE),
            re.compile(r"\b(build|make|create)\s+(a\s+|new\s+)?campaign", re.IGNORECASE),
        ],
    ),
    (
        "suggest_segments",
        [
            re.compile(r"\b(собери|предложи|подбери|найди)\s+сегмент", re.IGNORECASE),
            re.compile(r"\b(подбери|найди)\s+аудитори", re.IGNORECASE),
            re.compile(r"\b(suggest|propose)\s+(a\s+|new\s+)?segment", re.IGNORECASE),
        ],
    ),
    (
        "refine_campaign",
        [
            re.compile(r"\b(доработай|оптимизируй|улучши|поправь)\s+(этот\s+|эту\s+|текущ)?\s*(кампани|флоу|flow|draft)", re.IGNORECASE),
            re.compile(r"\b(refine|optimize|improve)\s+(this\s+|the\s+)?campaign", re.IGNORECASE),
            # «добавь / вставь» SMS, БТ, Wait, Event, Response — модификация существующего флоу.
            re.compile(r"\b(добавь|вставь|вставить|добавить)\b.*\b(sms|email|push|ussd|коммуникац|бизнес[ -]?транзакц|wait|пауз|event|событи|response|отклик|interactive|real[ -]?time|чек|transfer|exclude)", re.IGNORECASE),
            re.compile(r"\b(add|append|insert)\b.*\b(sms|email|push|ussd|communication|business|transaction|wait|event|response|interactive|real[ -]?time|check|transfer|exclude)", re.IGNORECASE),
        ],
    ),
    (
        "campaign_attention",
        [
            re.compile(r"\bкак(ие|им)\s+кампани\w*\s+(требу|нужн|плохо|на риске)", re.IGNORECASE),
            re.compile(r"\b(топ|анализ|обзор|отчет)\s+кампани", re.IGNORECASE),
            re.compile(r"\bчто\s+(не\s+так|плохо|с)\s+кампани", re.IGNORECASE),
            re.compile(r"\bкампани\w*\s+(под\s+риском|на\s+риске|с\s+проблем)", re.IGNORECASE),
            re.compile(r"\bкакие\s+кампании\b", re.IGNORECASE),
            re.compile(r"\bcampaigns?\s+(need|require)\s+attention", re.IGNORECASE),
        ],
    ),
]


def _rule_match(message: str) -> IntentDecision | None:
    text = message.strip()
    if not text:
        return None
    for intent, patterns in _RULES:
        for pattern in patterns:
            if pattern.search(text):
                return IntentDecision(intent=intent, confidence=0.94, reason=f"rule:{pattern.pattern[:40]}")
    return None


_SYSTEM_PROMPT = """Ты — роутер запросов в мультиагентной CVM-системе. По одному сообщению пользователя выбери ровно один intent.

Intents:
- campaign_attention — обзор/анализ существующих кампаний (что требует внимания, ранжирование, отчет).
- build_campaign     — пользователь просит СОЗДАТЬ новую кампанию (повелительное наклонение, поручение). Это НЕ вопрос «как».
- suggest_segments   — собрать гипотезы сегментов / аудиторий.
- refine_campaign    — улучшить / доработать существующий черновик или кампанию по id.
- documentation_qa   — вопрос «как / что / почему / зачем», запрос на объяснение функций платформы, гайды. Сюда же относятся ВСЕ вопросы вида «Как создать ...», «Как настроить ...», «Что такое ...».

Правило: вопрос про «как создать / how to create» — это ВСЕГДА documentation_qa, а не build_campaign.

Few-shot:
- «Как создать кампанию в AdTarget?» → documentation_qa
- «Что такое контрольная группа?» → documentation_qa
- «Как настроить EventActivity?» → documentation_qa
- «Создай кампанию для семейных клиентов через SMS» → build_campaign
- «Собери кампанию по утилизации пакета данных» → build_campaign
- «Какие кампании сейчас требуют внимания?» → campaign_attention
- «Покажи топ-5 кампаний с проблемами» → campaign_attention
- «Доработай кампанию 24» → refine_campaign
- «Оптимизируй текущий флоу» → refine_campaign
- «Собери сегмент активных клиентов» → suggest_segments
- «Подбери аудиторию под пакет 5GB» → suggest_segments

Ответ — строго JSON одной строкой без markdown:
{"intent":"<one>","confidence":<0..1>,"reason":"<≤80 chars>"}"""


async def classify_intent(message: str, history: list[dict] | None = None) -> IntentDecision:
    """Возвращает intent. Сначала узкие rules, потом LLM с few-shot."""
    rule = _rule_match(message)
    if rule is not None:
        return rule
    try:
        llm = get_llm(temperature=0)
        messages = [SystemMessage(content=_SYSTEM_PROMPT)]
        # Подмешиваем 1-2 последних сообщения для контекста (важно для follow-ups).
        if history:
            for h in history[-4:]:
                role = h.get("role")
                if role == "user":
                    messages.append(HumanMessage(content=str(h.get("content", ""))[:500]))
        messages.append(HumanMessage(content=f"CLASSIFY:\n{message}"))
        result = await llm.ainvoke(messages)
        raw = getattr(result, "content", str(result))
        text = raw if isinstance(raw, str) else json.dumps(raw)
        decision = _parse_decision(text)
        if decision is not None:
            return decision
    except Exception as exc:
        logger.warning("intent llm_classify failed: %s", exc)
    return IntentDecision(intent="documentation_qa", confidence=0.4, reason="fallback")


def _parse_decision(text: str) -> IntentDecision | None:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").lstrip("json").strip()
    # Иногда LLM добавляет лишнее — выкусим первый JSON-объект.
    match = re.search(r"\{.*?\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    intent = payload.get("intent")
    if intent not in _VALID_INTENTS:
        return None
    try:
        confidence = float(payload.get("confidence", 0.6))
    except (TypeError, ValueError):
        confidence = 0.6
    return IntentDecision(
        intent=intent,  # type: ignore[arg-type]
        confidence=max(0.0, min(1.0, confidence)),
        reason=str(payload.get("reason", "llm"))[:80],
    )
