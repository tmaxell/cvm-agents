"""Intent classifier for unified chat: правила сначала, LLM если не уверены."""

from __future__ import annotations

import json
import logging
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


_RULES: list[tuple[IntentName, tuple[str, ...]]] = [
    ("campaign_attention", (
        "требу", "вниман", "attention", "что не так", "проблем", "анализ кампани",
        "report", "ранжир", "приорите", "horror", "топ кампаний",
    )),
    ("build_campaign", (
        "созда", "build", "собери кампан", "собрать кампан", "новую кампан",
        "сделай кампан", "запусти кампан", "оформи кампан",
    )),
    ("suggest_segments", (
        "сегмент", "аудитори", "цг ", "target group", "выбор аудитори", "кого таргетировать",
    )),
    ("refine_campaign", (
        "доработ", "оптимиз", "улучш", "refine", "optimize", "поправ", "доделай",
    )),
]


def _rule_match(message: str) -> IntentDecision | None:
    text = message.strip().lower()
    if not text:
        return None
    matched: list[tuple[IntentName, str]] = []
    for intent, patterns in _RULES:
        for p in patterns:
            if p in text:
                matched.append((intent, p))
                break
    if len(matched) == 1:
        intent, pattern = matched[0]
        return IntentDecision(intent=intent, confidence=0.92, reason=f"rule:{pattern}")
    return None


_LLM_PROMPT = """Classify the user's message into ONE intent for a marketing CVM assistant.
Available intents:
- campaign_attention   — user asks which campaigns need attention / report / problems
- build_campaign       — user wants to create a new campaign
- suggest_segments     — user wants to build/find an audience segment
- refine_campaign      — user wants to improve / optimize an existing campaign
- documentation_qa     — any other question (docs, how-to, definitions)

Return strict JSON: {"intent": "<one of above>", "confidence": 0..1, "reason": "..."}.
No prose."""


async def classify_intent(message: str) -> IntentDecision:
    """Возвращает intent сообщения. Сначала правила, потом LLM, потом fallback."""
    rule = _rule_match(message)
    if rule is not None:
        return rule
    try:
        llm = get_llm(temperature=0)
        result = await llm.ainvoke([
            SystemMessage(content=_LLM_PROMPT),
            HumanMessage(content=message),
        ])
        raw = getattr(result, "content", str(result))
        text = raw if isinstance(raw, str) else json.dumps(raw)
        # extract JSON
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:].strip()
        payload = json.loads(text)
        intent = payload.get("intent", "documentation_qa")
        valid = {"campaign_attention", "build_campaign", "suggest_segments", "refine_campaign", "documentation_qa"}
        if intent not in valid:
            intent = "documentation_qa"
        return IntentDecision(
            intent=intent,
            confidence=float(payload.get("confidence", 0.6)),
            reason=str(payload.get("reason", "llm")),
        )
    except Exception as exc:
        logger.warning("intent llm_classify failed: %s", exc)
        return IntentDecision(intent="documentation_qa", confidence=0.4, reason=f"fallback:{type(exc).__name__}")
