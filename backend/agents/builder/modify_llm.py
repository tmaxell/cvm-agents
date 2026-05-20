"""LLM-driven flow modifier: гибкая модификация draft_flow по NL-запросу.

Подход «LLM-as-planner + deterministic executor»:
1. LLM получает компактное представление текущего flow + запрос пользователя + историю.
2. Возвращает JSON-план операций (insert_after/insert_before/append/replace/remove).
3. Детерминистический executor валидирует и применяет операции, перестраивая связи.

Это даёт гибкость (LLM понимает контекст и многошаговые модификации) при сохранении
надёжности (executor проверяет каждую операцию, не доверяет LLM на 100%).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from agents.builder.catalog import NODE_CATALOG, catalog_for_llm
from agents.builder.planner import _make_activity, _rewire_transitions
from llm import get_llm
from tools.flow_builder import assemble_flow

logger = logging.getLogger(__name__)


_VALID_OPS = {"insert_after", "insert_before", "append", "replace", "remove"}


_SYSTEM_PROMPT = """Ты — редактор draft_flow CVM-кампаний в AdTarget. Получаешь текущий flow (список активностей) и пользовательский запрос на изменение. Возвращаешь JSON-план операций для модификации.

Каталог активностей:
{catalog}

Операции (op):
- "insert_after"  — вставить новую активность ПОСЛЕ якорной.
- "insert_before" — вставить новую активность ПЕРЕД якорной.
- "append"       — добавить в конец цепочки.
- "replace"      — заменить узел.
- "remove"       — удалить узел.

Anchor (для insert/replace/remove):
- {"id": "<id из current_flow.activities[i].id>"} — точечная привязка.
- {"type": "<ActivityType>", "match": "first"|"last"} — по типу узла.

Activity spec (для insert/append/replace) — это step с обязательным полем "type":
- PushCommunicationActivity → {"type":"PushCommunicationActivity","name":"<...>","content_type":"SmsContent"|"PushContent"|"EmailContent"|"UssdContent","text":"<сообщение>"}
- BusinessTransactionActivity → {"type":"BusinessTransactionActivity","name":"<...>","operation":"addBusinessProduct"|"removeBusinessProduct"|"charge","amount":1000,"bp_id":"<id>"}
- EventActivity → {"type":"EventActivity","name":"<...>","event_code":"Charge","wait_days":3}
- WaitActivity → {"type":"WaitActivity","name":"Wait","wait_days":7}
- RealTimeCheckActivity → {"type":"RealTimeCheckActivity","name":"<что проверяем>"}
- ResponseActivity → {"type":"ResponseActivity","name":"<...>"}
- InteractiveResponseActivity → {"type":"InteractiveResponseActivity","name":"<...>"}
- OrJoinActivity → {"type":"OrJoinActivity","name":"Or"}

Если запрос содержит несколько действий («добавь проверку и если успех — push»), возвращай НЕСКОЛЬКО operations по порядку.

Если запрос — это вопрос, оценка или просьба рекомендаций (не модификация) — возвращай operations=[].

Few-shot примеры:

1) request="Добавь Push после транзакции"
{"summary":"Добавляю Push после первой BusinessTransaction.","operations":[
  {"op":"insert_after","anchor":{"type":"BusinessTransactionActivity","match":"first"},"activity":{"type":"PushCommunicationActivity","name":"Уведомление","content_type":"PushContent","text":"Поздравляем! Услуга подключена."}}
]}

2) request="Добавь проверку после транзакции, и если успешно — отправь пуш"
{"summary":"Вставляю RealTimeCheck после BT и Push после проверки.","operations":[
  {"op":"insert_after","anchor":{"type":"BusinessTransactionActivity","match":"first"},"activity":{"type":"RealTimeCheckActivity","name":"Проверка активации"}},
  {"op":"insert_after","anchor":{"type":"RealTimeCheckActivity","match":"last"},"activity":{"type":"PushCommunicationActivity","name":"Уведомление об активации","content_type":"PushContent","text":"Тариф успешно активирован!"}}
]}

3) request="Убери первый push"
{"summary":"Удаляю первый PushCommunicationActivity.","operations":[
  {"op":"remove","anchor":{"type":"PushCommunicationActivity","match":"first"}}
]}

4) request="Замени email на sms"
{"summary":"Заменяю Email push на SMS push.","operations":[
  {"op":"replace","anchor":{"type":"PushCommunicationActivity","match":"first"},"activity":{"type":"PushCommunicationActivity","name":"SMS push","content_type":"SmsContent","text":"Сообщение"}}
]}

Возвращай строго JSON одной строкой: {"summary":"...","operations":[...]}.
"""


def _system_prompt() -> str:
    return _SYSTEM_PROMPT.replace("{catalog}", catalog_for_llm())


def _compact_flow_summary(flow: dict[str, Any]) -> list[dict[str, Any]]:
    """Сжатое представление flow для LLM — только важные поля."""
    out: list[dict[str, Any]] = []
    for i, a in enumerate(flow.get("activities") or []):
        if not isinstance(a, dict):
            continue
        item: dict[str, Any] = {
            "index": i,
            "id": a.get("id"),
            "type": a.get("type"),
            "name": a.get("name") or "",
        }
        if a.get("type") in ("PushCommunicationActivity", "PullCommunicationActivity"):
            content = a.get("content") or {}
            item["content_type"] = content.get("type") or a.get("contentType")
        if a.get("type") == "BusinessTransactionActivity":
            bo = a.get("businessOperation") or {}
            item["operation"] = bo.get("id")
        if a.get("type") == "EventActivity":
            item["event_code"] = a.get("eventCode")
        out.append(item)
    return out


async def plan_modifications_with_llm(
    *,
    message: str,
    flow: dict[str, Any],
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Запрашивает у LLM план модификаций. Возвращает {summary, operations} или None."""
    summary = _compact_flow_summary(flow)
    if not summary:
        return None

    try:
        llm = get_llm(temperature=0.1)
        messages: list[Any] = [SystemMessage(content=_system_prompt())]
        if history:
            for h in history[-4:]:
                if h.get("role") == "user":
                    messages.append(HumanMessage(content=str(h.get("content", ""))[:300]))
        payload = {
            "user_request": message,
            "current_flow": summary,
        }
        messages.append(HumanMessage(content=json.dumps(payload, ensure_ascii=False)))
        result = await llm.ainvoke(messages)
        raw = getattr(result, "content", str(result))
        text = raw if isinstance(raw, str) else json.dumps(raw)
        return _parse_plan(text)
    except Exception as exc:
        logger.warning("modify_llm planner failed: %s", exc)
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
    ops_raw = payload.get("operations")
    if not isinstance(ops_raw, list):
        return None
    operations: list[dict[str, Any]] = []
    for op in ops_raw:
        if not isinstance(op, dict):
            continue
        op_name = op.get("op")
        if op_name not in _VALID_OPS:
            continue
        # Валидация: activity нужно для add/replace ops.
        if op_name in {"insert_after", "insert_before", "append", "replace"}:
            activity = op.get("activity")
            if not isinstance(activity, dict) or activity.get("type") not in NODE_CATALOG:
                continue
        if op_name in {"insert_after", "insert_before", "replace", "remove"}:
            anchor = op.get("anchor")
            if not isinstance(anchor, dict):
                continue
            if "id" not in anchor and "type" not in anchor:
                continue
        operations.append(op)
    if not operations:
        return None
    return {
        "summary": str(payload.get("summary") or "")[:300],
        "operations": operations,
    }


# ── Executor ──────────────────────────────────────────────────────────────────

def _find_anchor_index(activities: list[dict[str, Any]], anchor: dict[str, Any]) -> int | None:
    if "id" in anchor and isinstance(anchor["id"], str):
        for i, a in enumerate(activities):
            if a.get("id") == anchor["id"]:
                return i
        return None
    if "type" in anchor:
        target_type = anchor["type"]
        match = anchor.get("match", "first")
        if match == "last":
            for i in range(len(activities) - 1, -1, -1):
                if activities[i].get("type") == target_type:
                    return i
        else:
            for i, a in enumerate(activities):
                if a.get("type") == target_type:
                    return i
        return None
    return None


def apply_modifications(flow: dict[str, Any], plan: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    """Применяет план операций к flow. Возвращает (новый_flow, список_описаний_применённых_операций)."""
    activities = list(flow.get("activities") or [])
    if not activities:
        return None, []

    applied: list[str] = []

    for op in plan.get("operations") or []:
        op_name = op.get("op")
        anchor = op.get("anchor") or {}
        activity_spec = op.get("activity") or {}

        if op_name == "append":
            new_act = _make_activity(activity_spec)
            if new_act is None:
                continue
            activities.append(new_act)
            applied.append(f"добавлен {activity_spec.get('type')} «{new_act.get('name', '')}» в конец")

        elif op_name == "insert_after":
            idx = _find_anchor_index(activities, anchor)
            if idx is None:
                continue
            new_act = _make_activity(activity_spec)
            if new_act is None:
                continue
            activities.insert(idx + 1, new_act)
            applied.append(
                f"вставлен {activity_spec.get('type')} «{new_act.get('name', '')}» после {activities[idx].get('type')}"
            )

        elif op_name == "insert_before":
            idx = _find_anchor_index(activities, anchor)
            if idx is None or idx == 0:  # перед CommonActivity не вставляем
                continue
            new_act = _make_activity(activity_spec)
            if new_act is None:
                continue
            activities.insert(idx, new_act)
            applied.append(
                f"вставлен {activity_spec.get('type')} «{new_act.get('name', '')}» перед {activities[idx + 1].get('type')}"
            )

        elif op_name == "replace":
            idx = _find_anchor_index(activities, anchor)
            if idx is None or idx == 0:  # CommonActivity не заменяем
                continue
            new_act = _make_activity(activity_spec)
            if new_act is None:
                continue
            old_type = activities[idx].get("type")
            activities[idx] = new_act
            applied.append(f"заменён {old_type} → {new_act.get('type')} «{new_act.get('name', '')}»")

        elif op_name == "remove":
            idx = _find_anchor_index(activities, anchor)
            # Не позволяем удалять CommonActivity и TargetGroupActivity.
            if idx is None or idx <= 1:
                continue
            removed = activities.pop(idx)
            applied.append(f"удалён {removed.get('type')} «{removed.get('name', '')}»")

    if not applied:
        return None, []

    new_flow = assemble_flow(activities)
    _rewire_transitions(activities)
    return new_flow, applied
