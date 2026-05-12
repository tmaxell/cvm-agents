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
        "offers": [],
        "subNodes": [],
        "zoom": 1,
        "position": {"left": 120.5, "top": 566},
        "autoAlign": True,
    }
