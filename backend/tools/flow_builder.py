"""
Flow Builder — утилиты для построения валидных CampaignFlow.

Все ноды AdTarget требуют uuid v4 как id.
Активности связываются через nextActivityId.
CommonActivity всегда первый в списке.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any


def new_id() -> str:
    return str(uuid.uuid4())


def make_common_activity(
    name: str,
    *,
    begin_date: str | None = None,
    end_date: str | None = None,
    priority: int = 1,
    next_id: str | None = None,
) -> dict[str, Any]:
    """CommonActivity — корень flow (обязателен, всегда первый).

    Если даты не переданы — begin=сейчас, end=+30 дней.
    """
    tz = timezone(timedelta(hours=5))  # UTC+5, как на стенде
    now = datetime.now(tz)

    begin = begin_date or now.isoformat()
    end = end_date or (now + timedelta(days=30)).isoformat()

    return {
        "type": "CommonActivity",
        "id": new_id(),
        "name": name,
        "description": "",
        "position": {"left": 120, "top": 38},
        "nextActivityId": next_id,
        "tagIds": [],
        "errors": [],
        "warnings": [],
        "priority": priority,
        "typeId": None,
        "campaignGroupId": None,
        "schedule": {
            "period": {
                "tzBehavior": "System",
                "beginDate": begin,
                "endDate": end,
            },
            "frequency": {
                "type": "DailyFrequency",
                "periodInDays": 1,
            }
        },
        "settings": {
            "useContactPolicies": False,
            "hasImpactOnContactPolicies": True,
            "useBlackLists": False,
            "communicationLimit": None,
            "businessTransactionLimit": None,
        }
    }


def make_target_group_activity(
    target_group_id: int,
    *,
    next_id: str | None = None,
    use_control_group: bool = True,
) -> dict[str, Any]:
    """TargetGroupActivity — выбор аудитории (ЦГ)."""
    return {
        "type": "TargetGroupActivity",
        "id": new_id(),
        "name": "Target group",
        "position": {"left": 120, "top": 150},
        "nextActivityId": next_id,
        "tagIds": [],
        "errors": [],
        "warnings": [],
        "clientSourceType": "TargetGroup",
        "clientSourceId": target_group_id,
        "acceptClients": False,
        "excludeClients": False,
        "useLocalControlGroup": use_control_group,
        "localControlGroupSettings": {
            "rule": "Online",
            "percent": 10,
            "filters": [],
            "clientParametersDeviationPercent": 5,
        },
        "useUniversalControlGroup": True,
        "useTestGroup": False,
        "testGroupClientSourceId": None,
        "targetGroupSnapshotId": None,
        "isTemplate": False,
    }


def make_push_communication_activity(
    channel_id: int,
    content_type: str,
    message_text: str,
    *,
    next_id: str | None = None,
    sender: str | None = None,
) -> dict[str, Any]:
    """PushCommunicationActivity — отправка сообщения через канал.

    content_type: "SmsContent" | "CustomContent" | "EmailContent" | "UssdContent" | ...
    """
    parameters = [
        {
            "type": "StringContentParameterValue",
            "name": "Text",
            "value": message_text,
            "valueExpression": None,
            "isPriority": False,
            "targetType": "String",
        }
    ]
    if sender:
        parameters.append({
            "type": "StringContentParameterValue",
            "name": "Sender",
            "value": sender,
            "valueExpression": None,
            "isPriority": False,
            "targetType": "String",
        })

    return {
        "type": "PushCommunicationActivity",
        "id": new_id(),
        "name": content_type.replace("Content", " push").strip(),
        "position": {"left": 120, "top": 262},
        "nextActivityId": next_id,
        "tagIds": [],
        "errors": [],
        "warnings": [],
        "channelId": channel_id,
        "contentType": content_type,
        "isNotification": False,
        "defaultSuccessActivityId": None,
        "cases": {},
        "content": {
            "type": content_type,
            "parameters": parameters,
        },
    }


def make_event_activity(
    event_code: str,
    *,
    next_id: str | None = None,
    relevance_minutes: int = 15,
    filters: list | None = None,
) -> dict[str, Any]:
    """EventActivity — триггер по событию."""
    return {
        "type": "EventActivity",
        "id": new_id(),
        "name": event_code,
        "position": {"left": 120, "top": 150},
        "nextActivityId": next_id,
        "tagIds": [],
        "errors": [],
        "warnings": [],
        "eventCode": event_code,
        "eventRelevanceInMinutes": str(relevance_minutes),
        "filters": filters or [],
        "cases": {},
        "defaultSuccessActivityId": None,
        "defaultFailActivityId": None,
        "timeoutParameters": None,
        "haveToCheckSchedule": False,
    }


def make_wait_activity(
    wait_days: int = 1,
    *,
    next_id: str | None = None,
) -> dict[str, Any]:
    """WaitActivity — задержка перед следующим шагом (N дней)."""
    return {
        "type": "WaitActivity",
        "id": new_id(),
        "name": f"Wait {wait_days}d",
        "position": {"left": 120, "top": 150},
        "nextActivityId": next_id,
        "tagIds": [],
        "errors": [],
        "warnings": [],
        "waitingPeriod": {
            "type": "DaysCount",
            "count": wait_days,
        },
    }


def make_business_transaction_activity(
    offer_template_id: int,
    operation_id: str,
    operation_params: list[dict],
    *,
    next_id: str | None = None,
) -> dict[str, Any]:
    """BusinessTransactionActivity — активация продукта / скидки."""
    return {
        "type": "BusinessTransactionActivity",
        "id": new_id(),
        "name": "Business transaction",
        "position": {"left": 120, "top": 262},
        "nextActivityId": next_id,
        "tagIds": [],
        "errors": [],
        "warnings": [],
        "offerTemplateId": offer_template_id,
        "businessOperation": {
            "id": operation_id,
            "parameters": operation_params,
        },
    }


def make_pull_communication_activity(
    channel_id: int,
    content_type: str,
    message_text: str,
    *,
    next_id: str | None = None,
    sender: str | None = None,
) -> dict[str, Any]:
    """PullCommunicationActivity — входящая коммуникация через канал."""
    activity = make_push_communication_activity(
        channel_id,
        content_type,
        message_text,
        next_id=next_id,
        sender=sender,
    )
    activity["type"] = "PullCommunicationActivity"
    activity["name"] = content_type.replace("Content", " pull").strip()
    return activity


def make_response_activity(
    response_code: str | None = None,
    *,
    next_id: str | None = None,
    relevance_minutes: int = 15,
    filters: list | None = None,
) -> dict[str, Any]:
    """ResponseActivity — обработка отклика клиента."""
    return {
        "type": "ResponseActivity",
        "id": new_id(),
        "name": response_code or "Response",
        "position": {"left": 120, "top": 262},
        "nextActivityId": next_id,
        "tagIds": [],
        "errors": [],
        "warnings": [],
        "responseCode": response_code,
        "responseRelevanceInMinutes": str(relevance_minutes),
        "filters": filters or [],
        "cases": {},
        "defaultSuccessActivityId": None,
        "defaultFailActivityId": None,
        "timeoutParameters": None,
        "haveToCheckSchedule": False,
    }


def make_interactive_response_activity(
    response_code: str | None = None,
    *,
    next_id: str | None = None,
    relevance_minutes: int = 15,
    filters: list | None = None,
) -> dict[str, Any]:
    """InteractiveResponseActivity — интерактивный отклик клиента."""
    activity = make_response_activity(
        response_code,
        next_id=next_id,
        relevance_minutes=relevance_minutes,
        filters=filters,
    )
    activity["type"] = "InteractiveResponseActivity"
    activity["name"] = response_code or "Interactive response"
    return activity


def make_or_join_activity(
    *,
    next_id: str | None = None,
) -> dict[str, Any]:
    """OrJoinActivity — объединение нескольких веток flow."""
    return {
        "type": "OrJoinActivity",
        "id": new_id(),
        "name": "OR join",
        "position": {"left": 120, "top": 262},
        "nextActivityId": next_id,
        "tagIds": [],
        "errors": [],
        "warnings": [],
    }


def make_real_time_check_activity(
    *,
    next_id: str | None = None,
    filters: list | None = None,
) -> dict[str, Any]:
    """RealTimeCheckActivity — real-time проверка параметров клиента.

    В репозитории нет валидного экспортированного примера AdTarget для этой
    активности, поэтому сохраняем минимальную структуру, уже используемую
    прототипом, до получения точного образца от аналитика.
    """
    return {
        "type": "RealTimeCheckActivity",
        "id": new_id(),
        "name": "RT Check",
        "position": {"left": 120, "top": 262},
        "nextActivityId": next_id,
        "tagIds": [],
        "errors": [],
        "warnings": [],
        "filters": filters or [],
        "cases": {},
        "defaultSuccessActivityId": None,
        "defaultFailActivityId": None,
    }


def make_realtime_check_activity(
    *,
    next_id: str | None = None,
    filters: list | None = None,
) -> dict[str, Any]:
    """Backward-compatible alias for make_real_time_check_activity."""
    return make_real_time_check_activity(next_id=next_id, filters=filters)


def _content_parameter_value(activity: dict[str, Any], name: str) -> Any:
    """Возвращает значение content-параметра коммуникации по имени."""
    content = activity.get("content")
    if not isinstance(content, dict):
        return None

    parameters = content.get("parameters")
    if not isinstance(parameters, list):
        return None

    for parameter in parameters:
        if isinstance(parameter, dict) and parameter.get("name") == name:
            return parameter.get("value")
    return None


def _build_generated_offers(activities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Формирует UI-friendly список офферов из коммуникационных нод.

    AdTarget хранит текст оффера внутри content.parameters коммуникации,
    а шаблон/операцию активации — в следующей BusinessTransactionActivity.
    Для раскрытия communication-ноды на фронтенде дублируем эти данные в
    flow.offers и связываем запись с activityId.
    """
    by_id = {act.get("id"): act for act in activities if act.get("id")}
    offers: list[dict[str, Any]] = []

    for act in activities:
        if act.get("type") not in {"PushCommunicationActivity", "PullCommunicationActivity"}:
            continue

        text = _content_parameter_value(act, "Text")
        sender = _content_parameter_value(act, "Sender")
        next_activity = by_id.get(act.get("nextActivityId"))
        business_operation = (
            next_activity.get("businessOperation")
            if isinstance(next_activity, dict) and next_activity.get("type") == "BusinessTransactionActivity"
            else None
        )

        content = act.get("content") if isinstance(act.get("content"), dict) else {}

        offers.append({
            "id": f"offer-{act.get('id')}",
            "activityId": act.get("id"),
            "channelId": act.get("channelId"),
            "contentType": act.get("contentType") or content.get("type"),
            "text": text,
            "sender": sender,
            "offerTemplateId": next_activity.get("offerTemplateId") if isinstance(next_activity, dict) else None,
            "businessOperationId": business_operation.get("id") if isinstance(business_operation, dict) else None,
        })

    return offers


# ── Готовый шаблон upsell-кампании с напоминанием ────────────────────────────

def build_upsell_with_reminder_flow(
    *,
    campaign_name: str,
    product: str,
    target_group_id: int,
    target_group_name: str | None = None,
    offer_text: str,
    reminder_text: str | None = None,
    switch_tariff_plan_id: int = 1,
    channel_id: int = 1,
    sender: str = "AdTarget",
    response_source_id: int = 122,
    reminder_timeout_seconds: int = 259_200,  # 3 дня
    response_keyword: str = "Ок",
) -> dict[str, Any]:
    """Сборка upsell-кампании по шаблону из examples/upsell_exp.json.

    Структура (8 активностей):
      Common
        → TargetGroup
          → SMS push (offer_text)
            ├─ default success → Response #1 (filter Ок)
            │                       ├─ case "1" → OrJoin
            │                       └─ timeout (N дней) → SMS push (reminder_text)
            │                                                 → Response #2 (filter Ок)
            │                                                     └─ case "1" → OrJoin
            │                                                                    ↓
            ↓                                                                    │
                                              OrJoin ←──────────────────────────┘
                                                ↓
                                          BusinessTransaction (switchTariffPlan, терминал)
    """
    reminder_text = reminder_text or _default_reminder_text(product)

    # Генерим все id заранее, чтобы прописать переходы в обе стороны (DAG, не line).
    common_id = new_id()
    tg_id = new_id()
    sms_offer_id = new_id()
    response1_id = new_id()
    sms_reminder_id = new_id()
    response2_id = new_id()
    orjoin_id = new_id()
    bt_id = new_id()

    activities: list[dict[str, Any]] = []

    # 1. Common — настройки кампании
    common = make_common_activity(campaign_name)
    common["id"] = common_id
    common["nextActivityId"] = tg_id
    common["position"] = {"left": 266, "top": 38}
    activities.append(common)

    # 2. TargetGroup
    tg = make_target_group_activity(target_group_id=int(target_group_id))
    tg["id"] = tg_id
    tg["nextActivityId"] = sms_offer_id
    tg["position"] = {"left": 266, "top": 179}
    if target_group_name:
        tg["name"] = target_group_name
    activities.append(tg)

    # 3. SMS push с предложением (offer_text)
    sms_offer = make_push_communication_activity(
        channel_id=channel_id, content_type="SmsContent",
        message_text=offer_text, sender=sender,
    )
    sms_offer["id"] = sms_offer_id
    sms_offer["name"] = f"Предложение «{_short(product, 24)}»"
    sms_offer["nextActivityId"] = None
    sms_offer["defaultSuccessActivityId"] = response1_id
    sms_offer["position"] = {"left": 266, "top": 332}
    activities.append(sms_offer)

    # 4. Response #1: ждём «Ок» 3 дня, иначе уходим на reminder
    response1 = _make_keyword_response_activity(
        name="Response",
        response_source_id=response_source_id,
        keyword=response_keyword,
        timeout_seconds=reminder_timeout_seconds,
        timeout_next_id=sms_reminder_id,
        case_match_next_id=orjoin_id,
        linked_communication_id=sms_offer_id,
    )
    response1["id"] = response1_id
    response1["position"] = {"left": 266, "top": 496}
    activities.append(response1)

    # 5. SMS push с напоминанием
    sms_reminder = make_push_communication_activity(
        channel_id=channel_id, content_type="SmsContent",
        message_text=reminder_text, sender=sender,
    )
    sms_reminder["id"] = sms_reminder_id
    sms_reminder["name"] = f"Напоминание «{_short(product, 24)}»"
    sms_reminder["isNotification"] = True
    sms_reminder["nextActivityId"] = None
    sms_reminder["defaultSuccessActivityId"] = response2_id
    sms_reminder["position"] = {"left": 411, "top": 660}
    activities.append(sms_reminder)

    # 6. Response #2: дожидаемся «Ок», timeout не задан
    response2 = _make_keyword_response_activity(
        name=f"Отклик «{_short(product, 24)}»",
        response_source_id=response_source_id,
        keyword=response_keyword,
        timeout_seconds=None,
        timeout_next_id=None,
        case_match_next_id=orjoin_id,
        linked_communication_id=sms_reminder_id,
    )
    response2["id"] = response2_id
    response2["position"] = {"left": 411, "top": 824}
    activities.append(response2)

    # 7. OrJoin — объединение двух веток откликов
    orjoin = make_or_join_activity(next_id=bt_id)
    orjoin["id"] = orjoin_id
    orjoin["name"] = "Or"
    orjoin["position"] = {"left": 266, "top": 1134}
    activities.append(orjoin)

    # 8. BusinessTransaction: switchTariffPlan
    bt = make_business_transaction_activity(
        offer_template_id=0,
        operation_id="switchTariffPlan",
        operation_params=[
            {"name": "newPlanId", "value": str(int(switch_tariff_plan_id)), "valueExpression": None},
            {"name": "Comment", "value": None, "valueExpression": None},
            {"name": "FromNextPeriod", "value": None, "valueExpression": None},
        ],
    )
    bt["id"] = bt_id
    bt["name"] = f"Переключение на «{_short(product, 24)}»"
    bt["nextActivityId"] = None
    # BT — терминальная нода: ExcludeFromCampaign убран сознательно, чтобы
    # визуально совпадать с эталонным макетом (Common → … → OrJoin → BT).
    bt["defaultSuccessActivityId"] = None
    bt["position"] = {"left": 266, "top": 1280}
    activities.append(bt)

    return {
        "activities": activities,
        "offers": _build_generated_offers(activities),
        "subNodes": [
            {"id": f"{response1_id}__1", "type": "ActivityFilter", "position": {"left": 120, "top": 988}},
            {"id": f"{response2_id}__1", "type": "ActivityFilter", "position": {"left": 411, "top": 988}},
        ],
        "zoom": 0.62,
        "position": {"left": 71.0, "top": 784.0},
        "autoAlign": True,
    }


def _make_keyword_response_activity(
    *,
    name: str,
    response_source_id: int,
    keyword: str,
    timeout_seconds: int | None,
    timeout_next_id: str | None,
    case_match_next_id: str,
    linked_communication_id: str,
) -> dict[str, Any]:
    """ResponseActivity, ждущая ключевое слово в отклике.

    Случай `cases["1"]` соответствует фильтру №1 (равенство `keyword`).
    Timeout (если задан) уводит на `timeout_next_id`.
    """
    timeout = None
    if timeout_seconds is not None:
        timeout = {
            "timerType": "WaitForInterval",
            "interval": int(timeout_seconds),
            "waitUntil": None,
            "waitUntilExpression": None,
        }
    return {
        "type": "ResponseActivity",
        "id": new_id(),
        "name": name,
        "position": {"left": 120, "top": 262},
        "nextActivityId": None,
        "tagIds": [],
        "errors": [],
        "warnings": [],
        "defaultSuccessActivityId": None,
        "cases": {"1": case_match_next_id},
        "responseSourceId": int(response_source_id),
        "haveToCheckSchedule": True,
        "timeoutParameters": timeout,
        "timeOutNextActivityId": timeout_next_id,
        "invalidTimeNextActivityId": None,
        "linkedCommunicationActivities": [linked_communication_id],
        "operations": "LTrim, RTrim, CaseInsensitive",
        "filters": [
            {
                "type": "CalculatedResponseFilter",
                "function": "Equals",
                "arguments": [keyword],
                "index": 1,
            }
        ],
        "defaultFailActivityId": None,
    }


def _make_exclude_from_campaign_activity() -> dict[str, Any]:
    """ExcludeFromCampaignActivity — удаляет клиента из текущей кампании по факту BT."""
    return {
        "type": "ExcludeFromCampaignActivity",
        "id": new_id(),
        "name": "Удаление клиентов из текущей кампании",
        "position": {"left": 120, "top": 262},
        "nextActivityId": None,
        "campaigns": [],
        "removeFromCurrentCampaign": True,
        "tagIds": [],
    }


def _default_reminder_text(product: str) -> str:
    """Шаблонный текст напоминания, когда LLM сгенерировал только основной offer-текст."""
    name = (product or "").strip() or "наш тариф"
    return (
        f"Напоминаем: вы можете перейти на «{name}». "
        "Отправьте «Ок» на 999 до {{[c.EndDate]}}."
    )


def _short(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: max(1, limit - 1)] + "…"


def assemble_flow(activities: list[dict[str, Any]]) -> dict[str, Any]:
    """Собирает полный объект flow из списка активностей.

    Автоматически связывает nextActivityId: каждая активность указывает
    на следующую в списке. Последняя → None.
    Позиции расставляются вертикально с шагом 112px.
    """
    for i, act in enumerate(activities):
        act["nextActivityId"] = activities[i + 1]["id"] if i + 1 < len(activities) else None
        act["position"] = {"left": 120, "top": 38 + i * 112}

    return {
        "activities": activities,
        "offers": _build_generated_offers(activities),
        "subNodes": [],
        "zoom": 1,
        "position": {"left": 120.5, "top": 566},
        "autoAlign": True,
    }
