"""Модификация существующего draft_flow: добавление активности.

Парсит запрос вида «добавь SMS», «добавь бизнес-транзакцию», «вставь Wait перед коммуникацией».
Возвращает обновлённый flow с правильно проставленными связями.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from agents.builder.planner import _make_activity, _rewire_transitions
from tools.flow_builder import assemble_flow

logger = logging.getLogger(__name__)


# Триггеры для определения, что нужно добавить.
_ADD_PATTERNS: list[tuple[str, dict[str, Any]]] = [
    (r"\bsms\s+(коммуник|push|сообщ)", {"type": "PushCommunicationActivity", "content_type": "SmsContent"}),
    (r"\bsms\b",                       {"type": "PushCommunicationActivity", "content_type": "SmsContent"}),
    (r"\bemail|почт",                  {"type": "PushCommunicationActivity", "content_type": "EmailContent"}),
    (r"\bpush\b|пуш",                  {"type": "PushCommunicationActivity", "content_type": "PushContent"}),
    (r"\bussd\b",                      {"type": "PushCommunicationActivity", "content_type": "UssdContent"}),
    (r"бизнес[ -]?транзакц|business\s*transaction|bt\b",
                                       {"type": "BusinessTransactionActivity", "operation": "addBusinessProduct"}),
    (r"\bевент|event|событи",         {"type": "EventActivity", "event_code": "Charge"}),
    (r"\bwait|пауз|задержк|ожидан",   {"type": "WaitActivity", "wait_days": 1}),
    (r"\bresponse|отклик",             {"type": "ResponseActivity"}),
    (r"interactive|интерактив",        {"type": "InteractiveResponseActivity"}),
    (r"real[ -]?time|чек|проверк",    {"type": "RealTimeCheckActivity"}),
    (r"transfer|перевод в кампан",    {"type": "TransferToCampaignActivity"}),
    (r"exclude|исключен",              {"type": "ExcludeFromCampaignActivity"}),
]


def detect_add_intent(message: str) -> dict[str, Any] | None:
    """Если сообщение похоже на «добавь X», вернёт описание шага для _make_activity."""
    if not message:
        return None
    lower = message.lower()
    if not any(verb in lower for verb in ("добав", "вставь", "вставить", "встав ", "add ", "append")):
        return None
    for pattern, step_template in _ADD_PATTERNS:
        if re.search(pattern, lower):
            step = dict(step_template)
            step["name"] = _suggest_name(step["type"], lower)
            if step["type"] == "PushCommunicationActivity":
                step.setdefault("text", _suggest_sms_text(lower))
            return step
    return None


def _suggest_name(activity_type: str, lower_msg: str) -> str:
    if activity_type == "PushCommunicationActivity":
        if "email" in lower_msg or "почт" in lower_msg:
            return "Email push"
        if "ussd" in lower_msg:
            return "USSD push"
        if "push" in lower_msg and "sms" not in lower_msg:
            return "Mobile push"
        return "SMS push"
    if activity_type == "BusinessTransactionActivity":
        return "Business transaction"
    if activity_type == "WaitActivity":
        return "Wait"
    if activity_type == "EventActivity":
        return "Event"
    if activity_type == "ResponseActivity":
        return "Response"
    if activity_type == "InteractiveResponseActivity":
        return "Interactive response"
    if activity_type == "RealTimeCheckActivity":
        return "Real-time check"
    if activity_type == "TransferToCampaignActivity":
        return "Transfer to campaign"
    if activity_type == "ExcludeFromCampaignActivity":
        return "Exclude from campaign"
    return activity_type


def _suggest_sms_text(lower_msg: str) -> str:
    if "подарок" in lower_msg or "gift" in lower_msg:
        return "Поздравляем! Вам начислен подарок. Подробности у нас."
    if "тариф" in lower_msg:
        return "Спецпредложение по вашему тарифу. Узнайте подробности."
    return "Уведомление по нашей кампании. Подробности уточняйте."


def append_activity_to_flow(flow: dict[str, Any], step: dict[str, Any]) -> dict[str, Any] | None:
    """Создаёт новую активность из step и добавляет её В КОНЕЦ цепочки.

    Связи перестраиваются через assemble_flow + _rewire_transitions (как в planner).
    """
    activities = list(flow.get("activities") or [])
    if not activities:
        return None
    new_activity = _make_activity(step)
    if new_activity is None:
        return None
    activities.append(new_activity)
    new_flow = assemble_flow(activities)
    _rewire_transitions(activities)
    return new_flow
