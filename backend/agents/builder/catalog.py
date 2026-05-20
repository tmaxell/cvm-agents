"""Каталог activity-нод AdTarget и правила их связей.

Каждая нода описывает:
- какое поле использовать для перехода (next/cases/success/fail/timeout),
- какие активности могут идти ПОСЛЕ неё (валидные следующие шаги),
- человекочитаемое описание для LLM-планировщика.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ActivityType = Literal[
    "CommonActivity",
    "TargetGroupActivity",
    "EventActivity",
    "FilterActivity",
    "WaitActivity",
    "PushCommunicationActivity",
    "PullCommunicationActivity",
    "BusinessTransactionActivity",
    "ResponseActivity",
    "InteractiveResponseActivity",
    "RealTimeCheckActivity",
    "OrJoinActivity",
    "TransferToCampaignActivity",
    "ExcludeFromCampaignActivity",
]

# Какие поля являются «следующим шагом» для каждой активности.
# Используется для распределения цепочки и для валидации.
TRANSITION_FIELDS: dict[str, list[str]] = {
    "CommonActivity":              ["nextActivityId"],
    "TargetGroupActivity":         ["nextActivityId"],
    "WaitActivity":                ["timeOutNextActivityId", "nextActivityId"],
    "FilterActivity":              ["cases", "defaultSuccessActivityId"],
    "EventActivity":               ["defaultSuccessActivityId", "timeOutNextActivityId"],
    "RealTimeCheckActivity":       ["cases", "defaultSuccessActivityId", "defaultFailActivityId"],
    "PushCommunicationActivity":   ["defaultSuccessActivityId", "defaultFailActivityId"],
    "PullCommunicationActivity":   ["defaultSuccessActivityId", "defaultFailActivityId", "timeOutNextActivityId"],
    "InteractiveResponseActivity": ["cases", "defaultSuccessActivityId", "timeOutNextActivityId"],
    "ResponseActivity":            ["cases", "defaultSuccessActivityId"],
    "BusinessTransactionActivity": ["defaultSuccessActivityId", "defaultFailActivityId"],
    "OrJoinActivity":              ["nextActivityId"],
    "TransferToCampaignActivity":  ["nextActivityId"],
    "ExcludeFromCampaignActivity": ["nextActivityId"],
}


@dataclass(slots=True)
class NodeSpec:
    type: str
    label: str
    purpose: str
    can_follow: tuple[str, ...]    # какие активности валидны ПОСЛЕ этой
    must_appear_after: tuple[str, ...] = field(default_factory=tuple)  # обязательные предшественники


# Минимальные правила связей. «(...)» означает «любая нода действия».
ACTION_LIKE = (
    "PushCommunicationActivity", "PullCommunicationActivity",
    "BusinessTransactionActivity",
    "EventActivity", "WaitActivity",
    "RealTimeCheckActivity", "FilterActivity",
    "TransferToCampaignActivity", "ExcludeFromCampaignActivity",
    "OrJoinActivity",
)


NODE_CATALOG: dict[str, NodeSpec] = {
    "CommonActivity": NodeSpec(
        type="CommonActivity",
        label="Common",
        purpose="Корневая нода — параметры кампании (название, расписание, приоритет). Обязательна, всегда первая.",
        can_follow=("TargetGroupActivity",),
    ),
    "TargetGroupActivity": NodeSpec(
        type="TargetGroupActivity",
        label="Target group",
        purpose="Выбор аудитории по существующей таргет-группе или ClientDataSource.",
        can_follow=ACTION_LIKE,
        must_appear_after=("CommonActivity",),
    ),
    "EventActivity": NodeSpec(
        type="EventActivity",
        label="Event",
        purpose="Триггер по бизнес-событию (eventCode). Ждёт событие или timeout, потом продолжает success-веткой.",
        can_follow=ACTION_LIKE + ("ResponseActivity", "InteractiveResponseActivity"),
    ),
    "FilterActivity": NodeSpec(
        type="FilterActivity",
        label="Filter",
        purpose="Условный сплит по выражению. Несколько cases и default.",
        can_follow=ACTION_LIKE + ("ResponseActivity", "InteractiveResponseActivity"),
    ),
    "WaitActivity": NodeSpec(
        type="WaitActivity",
        label="Wait",
        purpose="Пауза между касаниями: фиксированный интервал или waitUntil-выражение.",
        can_follow=ACTION_LIKE,
    ),
    "PushCommunicationActivity": NodeSpec(
        type="PushCommunicationActivity",
        label="Push communication",
        purpose="Отправка сообщения наружу: SMS / Email / Push / USSD / Custom. Текст в content.localizedContents.",
        can_follow=ACTION_LIKE + ("ResponseActivity", "InteractiveResponseActivity"),
    ),
    "PullCommunicationActivity": NodeSpec(
        type="PullCommunicationActivity",
        label="Pull communication",
        purpose="Запрос на входящую коммуникацию.",
        can_follow=ACTION_LIKE + ("ResponseActivity", "InteractiveResponseActivity"),
    ),
    "BusinessTransactionActivity": NodeSpec(
        type="BusinessTransactionActivity",
        label="Business transaction",
        purpose="Выполнение бизнес-операции: активация продукта, начисление, отключение. operations: addBusinessProduct / removeBusinessProduct / charge.",
        can_follow=ACTION_LIKE,
    ),
    "ResponseActivity": NodeSpec(
        type="ResponseActivity",
        label="Response",
        purpose="Ожидание отклика по предыдущей коммуникации (callback). Cases по результату.",
        can_follow=ACTION_LIKE + ("OrJoinActivity",),
    ),
    "InteractiveResponseActivity": NodeSpec(
        type="InteractiveResponseActivity",
        label="Interactive response",
        purpose="Интерактивное ожидание ответа клиента (например, USSD). Cases по варианту ответа.",
        can_follow=ACTION_LIKE + ("OrJoinActivity",),
    ),
    "RealTimeCheckActivity": NodeSpec(
        type="RealTimeCheckActivity",
        label="Real-time check",
        purpose="Синхронная проверка (балансы, флаги). Cases — ветки по результату проверки.",
        can_follow=ACTION_LIKE + ("PushCommunicationActivity", "BusinessTransactionActivity"),
    ),
    "OrJoinActivity": NodeSpec(
        type="OrJoinActivity",
        label="Or",
        purpose="Слияние нескольких параллельных веток в одну продолжающуюся.",
        can_follow=ACTION_LIKE,
    ),
    "TransferToCampaignActivity": NodeSpec(
        type="TransferToCampaignActivity",
        label="Transfer to campaign",
        purpose="Перевод клиента в другую кампанию (этап жизненного цикла).",
        can_follow=("WaitActivity", "ExcludeFromCampaignActivity"),
    ),
    "ExcludeFromCampaignActivity": NodeSpec(
        type="ExcludeFromCampaignActivity",
        label="Exclude from campaign",
        purpose="Исключение клиента из указанной кампании.",
        can_follow=ACTION_LIKE,
    ),
}


def catalog_for_llm() -> str:
    """Компактное описание каталога для system-prompt LLM-планировщика."""
    rows: list[str] = []
    for spec in NODE_CATALOG.values():
        follow = ", ".join(spec.can_follow) or "—"
        rows.append(f"- **{spec.type}** ({spec.label}): {spec.purpose} Может предшествовать: {follow}.")
    return "\n".join(rows)


def is_valid_transition(from_type: str, to_type: str) -> bool:
    spec = NODE_CATALOG.get(from_type)
    if spec is None:
        return True
    return to_type in spec.can_follow
