"""LLM-планировщик и детерминистический сборщик flow по плану.

Подход:
1. LLM получает каталог нод и описание задачи → возвращает JSON-план: список шагов,
   каждый шаг = {type, name, params}.
2. Детерминистический сборщик принимает план и собирает валидный flow через helpers
   из tools/flow_builder.py. Связи проставляются автоматически по правилам каталога.

Это устраняет основную проблему предыдущего LangGraph builder'а: LLM выдаёт ПЛАН
(маленький стабильный JSON), а собственно сборка JSON-флоу делается кодом.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from agents.builder.catalog import NODE_CATALOG, catalog_for_llm
from llm import get_llm
from tools.flow_builder import (
    assemble_flow,
    make_business_transaction_activity,
    make_common_activity,
    make_event_activity,
    make_or_join_activity,
    make_pull_communication_activity,
    make_push_communication_activity,
    make_real_time_check_activity,
    make_response_activity,
    make_target_group_activity,
    make_wait_activity,
)

logger = logging.getLogger(__name__)


_PLANNER_SYSTEM = """Ты — планировщик AdTarget CVM кампаний. По бизнес-брифу пользователя верни JSON-план шагов flow.

Доступные ноды (используй только эти типы):
{catalog}

Правила сборки:
- Первая нода ВСЕГДА CommonActivity (название кампании), вторая — TargetGroupActivity.
- После Push/Pull-коммуникации обычно идёт ResponseActivity или WaitActivity или BusinessTransactionActivity.
- BusinessTransaction нужен когда продвигаем активацию / начисление / отключение продукта.
- WaitActivity — пауза между касаниями.
- Не более 10 шагов в плане для типовых сценариев.

Сценарий ⇒ структура (используй scenario из брифа):
- single_touch              → Common → TG → Push (один SMS/Email/Push).
- trigger_with_activation   → Common → TG → Push (приглашение) → Event (триггер: Charge/TopUp/Activation) → BusinessTransaction (addBusinessProduct).
- two_step_with_response    → Common → TG → Push → Response → BusinessTransaction → Push (подтверждение).
- multi_touch_with_wait     → Common → TG → Push → Wait → Push (повторное касание) → Wait → Push (финал).
- lifecycle_with_transfer   → Common → TG → Push → Event → BT → Push → Wait → BT (откл) → TransferToCampaign → Wait → ExcludeFromCampaign.
- unknown                   → выбери оптимальный 4-6 шаговый сценарий, опираясь на product/goal/channels.

Channel → content_type mapping:
- sms   → "SmsContent"
- email → "EmailContent"
- push  → "PushContent"
- ussd  → "UssdContent"

Для каждой ноды используй понятное имя на русском в name (не «SMS push», а «Приглашение Тариф Семейный»).

Верни строго JSON одной строкой:
{{
  "campaign_name": "<человеческое название кампании>",
  "summary": "<1 предложение: почему такой сценарий выбран>",
  "steps": [
    {{"type":"CommonActivity", "name":"<...>"}},
    {{"type":"TargetGroupActivity", "name":"<описание аудитории>"}},
    {{"type":"PushCommunicationActivity", "name":"SMS приглашение", "content_type":"SmsContent", "text":"<смс-текст с подстановкой продукта>"}},
    {{"type":"EventActivity", "name":"<eventCode>", "event_code":"Charge", "wait_days":4}},
    {{"type":"BusinessTransactionActivity", "name":"Активация ...", "operation":"addBusinessProduct", "amount":1000}},
    {{"type":"WaitActivity", "name":"Wait 7d", "wait_days":7}}
  ]
}}

Для PushCommunicationActivity всегда указывай content_type и text (1-2 предложения с упоминанием продукта).
Для BusinessTransactionActivity указывай operation: addBusinessProduct / removeBusinessProduct / charge.
Для EventActivity указывай event_code (Charge, TopUp, Activation, Deactivation) и опц. wait_days (timeout).
Для WaitActivity указывай wait_days или wait_hours.
"""


def _system_prompt() -> str:
    return _PLANNER_SYSTEM.replace("{catalog}", catalog_for_llm())


async def plan_flow_with_llm(
    goal: str,
    *,
    history: list[dict] | None = None,
    brief: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Запрашивает у LLM план кампании. Возвращает {campaign_name, summary, steps} или None.

    brief — структурированный бриф из brief.analyze_brief() (опционально).
    """
    try:
        llm = get_llm(temperature=0.1)
        messages: list[Any] = [SystemMessage(content=_system_prompt())]
        if history:
            for h in history[-4:]:
                if h.get("role") == "user":
                    messages.append(HumanMessage(content=str(h.get("content", ""))[:300]))
        payload: dict[str, Any] = {"goal": goal}
        if brief:
            payload["brief"] = brief
        messages.append(HumanMessage(content=json.dumps(payload, ensure_ascii=False)))
        result = await llm.ainvoke(messages)
        raw = getattr(result, "content", str(result))
        text = raw if isinstance(raw, str) else json.dumps(raw)
        return _parse_plan(text)
    except Exception as exc:
        logger.warning("planner LLM failed: %s", exc)
        return None


def _parse_plan(text: str) -> dict[str, Any] | None:
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
    steps = payload.get("steps")
    if not isinstance(steps, list) or not steps:
        return None
    # Минимальная валидация: тип шага должен быть из каталога.
    sanitized = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        t = step.get("type")
        if t in NODE_CATALOG:
            sanitized.append(step)
    if not sanitized:
        return None
    return {
        "campaign_name": str(payload.get("campaign_name") or "Новая кампания")[:120],
        "summary": str(payload.get("summary") or "")[:300],
        "steps": sanitized,
    }


# ── Детерминистический сборщик ────────────────────────────────────────────────

def assemble_flow_from_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Из плана строит JSON flow с правильно проставленными связями."""
    campaign_name = str(plan.get("campaign_name") or "Новая кампания")
    raw_steps = plan.get("steps") or []

    # Гарантируем что первая нода — CommonActivity, вторая — TargetGroupActivity.
    if not raw_steps or raw_steps[0].get("type") != "CommonActivity":
        raw_steps = [{"type": "CommonActivity", "name": campaign_name}] + list(raw_steps)
    if len(raw_steps) < 2 or raw_steps[1].get("type") != "TargetGroupActivity":
        raw_steps = [raw_steps[0]] + [{"type": "TargetGroupActivity", "name": "Target group"}] + list(raw_steps[1:])

    activities = [_make_activity(step) for step in raw_steps]
    activities = [a for a in activities if a is not None]
    if not activities:
        return {"activities": [], "offers": [], "subNodes": [], "zoom": 1, "position": {"left": 120, "top": 0}, "autoAlign": True}

    # assemble_flow проставит nextActivityId по порядку + позиции.
    # Но для нод с success/fail (Push/Event/BT) AdTarget использует другие поля.
    # Сначала зальём через assemble_flow, потом перепишем поля переходов по правильным ключам.
    flow = assemble_flow(activities)
    _rewire_transitions(activities)
    return flow


def _make_activity(step: dict[str, Any]) -> dict[str, Any] | None:
    t = step.get("type")
    name = str(step.get("name") or "").strip()
    try:
        if t == "CommonActivity":
            return make_common_activity(name or "Новая кампания")
        if t == "TargetGroupActivity":
            tg_id = int(step.get("target_group_id") or 1)
            return make_target_group_activity(target_group_id=tg_id)
        if t == "PushCommunicationActivity":
            content_type = str(step.get("content_type") or "SmsContent")
            text = str(step.get("text") or "Здравствуйте! Это сообщение по нашей кампании.")
            activity = make_push_communication_activity(
                channel_id=int(step.get("channel_id") or 1),
                content_type=content_type,
                message_text=text,
                sender=step.get("sender"),
            )
            if name:
                activity["name"] = name
            return activity
        if t == "PullCommunicationActivity":
            activity = make_pull_communication_activity(
                channel_id=int(step.get("channel_id") or 1),
                content_type=str(step.get("content_type") or "SmsContent"),
                message_text=str(step.get("text") or ""),
            )
            if name:
                activity["name"] = name
            return activity
        if t == "EventActivity":
            event_code = str(step.get("event_code") or "Charge")
            wait_days = int(step.get("wait_days") or step.get("wait_hours", 0) // 24 or 3)
            activity = make_event_activity(event_code=event_code, relevance_minutes=15)
            # timeout — добавим вручную в формате AdTarget (interval в секундах)
            activity["timeoutParameters"] = {
                "timerType": "WaitForInterval",
                "interval": max(60, wait_days * 86400),
                "waitUntil": None,
                "waitUntilExpression": None,
            }
            if name:
                activity["name"] = name
            return activity
        if t == "BusinessTransactionActivity":
            operation = str(step.get("operation") or "addBusinessProduct")
            amount = step.get("amount")
            bp_id = str(step.get("bp_id") or step.get("product_id") or "demo_product")
            params: list[dict[str, Any]] = [
                {"name": "bpId", "value": bp_id, "valueExpression": None},
            ]
            if amount is not None:
                params.append({"name": "Amount", "value": str(amount), "valueExpression": None})
            activity = make_business_transaction_activity(
                offer_template_id=int(step.get("offer_template_id") or 1),
                operation_id=operation,
                operation_params=params,
            )
            if name:
                activity["name"] = name
            return activity
        if t == "WaitActivity":
            wait_days = int(step.get("wait_days") or 0)
            wait_hours = int(step.get("wait_hours") or 0)
            if wait_days <= 0 and wait_hours > 0:
                wait_days = max(1, wait_hours // 24)
            if wait_days <= 0:
                wait_days = 1
            activity = make_wait_activity(wait_days=wait_days)
            if name:
                activity["name"] = name
            return activity
        if t == "RealTimeCheckActivity":
            activity = make_real_time_check_activity()
            if name:
                activity["name"] = name
            return activity
        if t == "ResponseActivity":
            activity = make_response_activity(response_code=name or None)
            if name:
                activity["name"] = name
            return activity
        if t == "InteractiveResponseActivity":
            from tools.flow_builder import make_interactive_response_activity
            activity = make_interactive_response_activity(response_code=name or None)
            if name:
                activity["name"] = name
            return activity
        if t == "OrJoinActivity":
            activity = make_or_join_activity()
            if name:
                activity["name"] = name
            return activity
    except Exception as exc:
        logger.warning("failed to build activity %s: %s", t, exc)
        return None
    return None


def _rewire_transitions(activities: list[dict[str, Any]]) -> None:
    """Переставляет переходы с nextActivityId на правильное поле в зависимости от типа.

    assemble_flow() ставит nextActivityId для всех, но Push/BT/Event/Response используют
    defaultSuccessActivityId, а WaitActivity — timeOutNextActivityId.
    """
    for i, act in enumerate(activities):
        next_id = activities[i + 1]["id"] if i + 1 < len(activities) else None
        t = act.get("type")
        if t in {
            "PushCommunicationActivity",
            "PullCommunicationActivity",
            "BusinessTransactionActivity",
            "EventActivity",
            "ResponseActivity",
            "InteractiveResponseActivity",
        }:
            act["nextActivityId"] = None
            act["defaultSuccessActivityId"] = next_id
        elif t == "WaitActivity":
            act["nextActivityId"] = None
            act["timeOutNextActivityId"] = next_id
        elif t == "RealTimeCheckActivity":
            act["nextActivityId"] = None
            act["defaultSuccessActivityId"] = next_id
        # CommonActivity / TargetGroup / OrJoin / Transfer / Exclude — оставляем nextActivityId.
