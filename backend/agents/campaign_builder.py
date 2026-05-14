"""
F2 — Campaign Builder Agent

LangGraph ReAct-агент с tool use.
Получает бизнес-цель («создай кампанию по утилизации пакета данных»)
и автономно:
  1. Запрашивает справочники (ЦГ, каналы, события, шаблоны, типы/группы кампаний)
  2. Уточняет недостающие параметры у пользователя
  3. Собирает валидный flow из активностей (через flow_builder.py)
  4. Валидирует его через API
  5. Создаёт кампанию (POST /Campaigns)
  6. По запросу — запускает (PUT /Campaigns/start)

Точка входа: run(request: BuilderRequest) -> BuilderResponse
"""

import json
import os
import re
from dataclasses import dataclass
from typing import Annotated, Any

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage, trim_messages
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langchain_core.tools import tool
from typing_extensions import TypedDict

from llm import get_llm
from schemas import BuilderRequest, BuilderResponse
from tools import adtarget
from tools.flow_builder import (
    make_common_activity,
    make_target_group_activity,
    make_push_communication_activity,
    make_event_activity,
    make_business_transaction_activity,
    make_wait_activity,
    make_real_time_check_activity,
    make_response_activity,
    make_pull_communication_activity,
    make_or_join_activity,
    make_interactive_response_activity,
    assemble_flow,
)

SUPPORTED_ACTIVITY_TYPES = {
    "PushCommunicationActivity",
    "PullCommunicationActivity",
    "EventActivity",
    "WaitActivity",
    "BusinessTransactionActivity",
    "RealTimeCheckActivity",
    "ResponseActivity",
    "InteractiveResponseActivity",
    "OrJoinActivity",
}

SUPPORTED_ACTIVITY_TYPES_TEXT = ", ".join(sorted(SUPPORTED_ACTIVITY_TYPES))


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _resolve_channel_id(hint_id: int, required_content_type: str) -> int:
    """Возвращает id канала с нужным contentType.

    Если hint_id уже соответствует contentType — возвращает его.
    Иначе ищет первый канал с нужным contentType в справочнике.
    """
    try:
        channels = await adtarget.list_channels()
        # Validate hint_id first
        for ch in channels:
            if ch.get("id") == hint_id and ch.get("contentType") == required_content_type:
                return hint_id  # correct match
        # Fallback: find first channel with required contentType
        for ch in channels:
            if ch.get("contentType") == required_content_type:
                resolved = ch["id"]
                print(f"[campaign_builder] Channel ID mismatch: hint={hint_id}, "
                      f"resolved {required_content_type} → id={resolved}")
                return resolved
    except Exception:
        pass
    return hint_id  # return as-is if lookup failed


# ── LangChain Tools ───────────────────────────────────────────────────────────

def _api_error(tool_name: str, e: Exception) -> str:
    """Форматирует ошибку API в читаемый JSON для LLM."""
    import httpx
    if isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout)):
        msg = "AdTarget API недоступен (нет подключения к стенду). Возможно, нужен VPN."
    elif isinstance(e, httpx.HTTPStatusError):
        msg = f"AdTarget API вернул ошибку {e.response.status_code}."
    else:
        msg = f"Ошибка: {type(e).__name__}: {e}"
    return json.dumps({"error": msg, "tool": tool_name}, ensure_ascii=False)



def _normalize_text(text: str | None) -> str:
    return (text or "").strip().lower()


def _is_memory_only_request(goal: str) -> bool:
    """Определяет сообщения, где пользователь только задаёт контекст для будущей сборки."""
    text = _normalize_text(goal)
    if not text:
        return False

    memory_markers = ("запомни", "учти", "контекст", "remember", "note that")
    build_markers = (
        "собер", "созда", "постро", "сгенер", "запусти", "build",
        "create", "assemble", "generate", "добавь", "добавить", "add",
    )
    return text.startswith(memory_markers) and not any(marker in text for marker in build_markers)


@dataclass(frozen=True)
class FlowEditIntent:
    """Parsed follow-up edit intent for an existing campaign flow."""

    action: str
    activity_type: str | None = None
    anchor_activity_type: str | None = None


def _extract_position_anchor(text: str) -> str | None:
    """Extracts a positional anchor like "после транзакции" from normalized text."""
    anchor_patterns = (
        (
            r"\b(?:после|after)\s+"
            r"(?:бизнес[-\s]?транзакц\w*|транзакц\w*|business\s+transaction|transaction)\b",
            "BusinessTransactionActivity",
        ),
        (
            r"\b(?:после|after)\s+(?:e-?mail|email|им[еэ]йл\w*|письм\w*)\b",
            "PushCommunicationActivity",
        ),
        (r"\b(?:после|after)\s+(?:событ\w*|event)\b", "EventActivity"),
    )
    for pattern, activity_type in anchor_patterns:
        if re.search(pattern, text):
            return activity_type
    return None


def _strip_position_anchors(text: str) -> str:
    """Removes positional anchor phrases before detecting the entity to add."""
    patterns = (
        r"\b(?:после|after)\s+(?:бизнес[-\s]?транзакц\w*|транзакц\w*|business\s+transaction|transaction)\b",
        r"\b(?:после|after)\s+(?:e-?mail|email|им[еэ]йл\w*|письм\w*)\b",
        r"\b(?:после|after)\s+(?:событ\w*|event)\b",
    )
    result = text
    for pattern in patterns:
        result = re.sub(pattern, " ", result)
    return result


def _parse_flow_edit_intent(goal: str) -> FlowEditIntent | None:
    """Parse follow-up intent into target activity and optional positional anchor.

    Priority matters: explicit real-time-check wording wins over nearby
    transaction words because "транзакция" can be an anchor, not the entity
    being added.
    """
    text = _normalize_text(goal)
    if not text:
        return None

    anchor_activity_type = _extract_position_anchor(text)

    realtime_markers = (
        "real-time",
        "real time",
        "реал-тайм",
        "реал тайм",
        "rt check",
        "проверк",
        "realtimecheckactivity",
    )
    if any(marker in text for marker in realtime_markers):
        return FlowEditIntent(
            action="add_activity",
            activity_type="RealTimeCheckActivity",
            anchor_activity_type=anchor_activity_type,
        )

    add_pattern = r"(?:\badd\b|\bappend\b|добав\w*|в конец|конце)"
    target_text = _strip_position_anchors(text)
    business_transaction_pattern = (
        r"(?:бизнес[-\s]?транзакц\w*|транзакц\w*|business\s+transaction|"
        r"\btransaction\b|активац\w*\s+оффер\w*|offer\s+activation)"
    )
    if re.search(add_pattern, target_text) and re.search(
        business_transaction_pattern,
        target_text,
    ):
        return FlowEditIntent(
            action="add_activity",
            activity_type="BusinessTransactionActivity",
            anchor_activity_type=anchor_activity_type,
        )

    return None


def _parse_flow_json(flow_json: str | None) -> dict | None:
    if not flow_json:
        return None
    try:
        data = json.loads(flow_json)
    except (json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("activities"), list) else None


def _extract_json_object(text: str) -> dict | None:
    """Tolerantly extracts a JSON object from an LLM response."""
    raw = (text or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = raw.strip("`").strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        data = json.loads(raw[start:end + 1])
    except (json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _normalize_special_turn_plan(plan: dict) -> dict:
    """Normalize planner output and legacy actions into the current add_activity schema."""
    normalized = dict(plan)
    if normalized.get("action") == "add_business_transaction":
        activity_params = normalized.get("activity_params")
        if not isinstance(activity_params, dict):
            activity_params = {}
        if normalized.get("offer_template_id") is not None:
            activity_params.setdefault("offer_template_id", normalized.get("offer_template_id"))
        if normalized.get("operation_id") is not None:
            activity_params.setdefault("operation_id", normalized.get("operation_id"))
        normalized.update({
            "action": "add_activity",
            "activity_type": "BusinessTransactionActivity",
            "activity_params": activity_params,
        })

    normalized.setdefault("activity_type", None)
    normalized.setdefault("anchor_type", None)
    normalized.setdefault("anchor_id", None)
    normalized.setdefault("anchor_position", None)
    normalized.setdefault("activity_params", {})
    normalized.setdefault("assistant_message", "")
    if not isinstance(normalized.get("activity_params"), dict):
        normalized["activity_params"] = {}
    return normalized


def _unsupported_activity_message(activity_type: str | None) -> str:
    requested = activity_type or "не указан"
    return (
        f"Не могу добавить активность типа {requested}: этот тип не поддержан Campaign Builder. "
        f"Поддержанные типы: {SUPPORTED_ACTIVITY_TYPES_TEXT}."
    )


def _is_supported_activity_type(activity_type: str | None) -> bool:
    return bool(activity_type) and activity_type in SUPPORTED_ACTIVITY_TYPES


def _looks_like_flow_activity_edit(goal: str) -> bool:
    text = _normalize_text(goal)
    if not text:
        return False
    edit_markers = ("добав", "встав", "add", "append", "insert")
    activity_markers = ("activity", "активност", "ноду", "узел", "check", "проверк", "транзакц")
    return any(marker in text for marker in edit_markers) and any(marker in text for marker in activity_markers)


async def _llm_plan_special_turn(
    goal: str,
    *,
    preferences: dict | None,
    existing_flow: dict | None,
    ref: dict | None = None,
) -> tuple[dict | None, str | None]:
    """Uses the LLM as a planner for iterative non-standard Builder turns.

    The backend still validates and applies the returned plan deterministically,
    so malformed tool-call JSON cannot break the prototype with HTTP 500.
    """
    try:
        llm = get_llm(for_tools=False)
        offers = []
        if ref:
            offers = [
                {"id": offer.get("id"), "name": offer.get("name"), "operationId": offer.get("operationId")}
                for offer in ref.get("offers", [])[:40]
            ]

        response = await llm.ainvoke([
            SystemMessage(content=(
                "Ты планировщик Campaign Builder. Верни только JSON без markdown. "
                "Схема: {\"action\": \"remember_context|add_activity|continue_agent\", "
                "\"activity_type\": string|null, \"anchor_type\": string|null, "
                "\"anchor_id\": string|null, \"anchor_position\": \"before|after|null\", "
                "\"activity_params\": object, \"assistant_message\": string}. "
                f"Поддержанные activity_type: {SUPPORTED_ACTIVITY_TYPES_TEXT}. "
                "Если пользователь только просит запомнить вводные — action=remember_context. "
                "Если просит добавить активность в текущий flow — action=add_activity; "
                "activity_type заполни точным типом активности, даже если тип не входит в поддержанный список. "
                "Для BusinessTransactionActivity выбери лучший оффер из available_offers и положи "
                "offer_template_id и operation_id в activity_params. "
                "anchor_id — id существующей активности-якоря, если пользователь указал конкретный узел; "
                "иначе anchor_type — тип существующей активности-якоря, anchor_position — before/after/null. "
                "Если это не запоминание и не правка flow, action=continue_agent. "
                "assistant_message всегда по-русски."
            )),
            HumanMessage(content=json.dumps({
                "user_goal": goal,
                "builder_preferences": preferences or {},
                "has_existing_flow": bool(existing_flow),
                "existing_activity_types": [
                    activity.get("type")
                    for activity in (existing_flow or {}).get("activities", [])
                    if isinstance(activity, dict)
                ],
                "available_offers": offers,
            }, ensure_ascii=False)),
        ])
        content = response.content if hasattr(response, "content") else str(response)
        plan = _extract_json_object(content if isinstance(content, str) else str(content))
        if not plan:
            return None, "LLM-планировщик вернул не-JSON ответ."
        return _normalize_special_turn_plan(plan), None
    except Exception as e:
        return None, f"LLM-планировщик недоступен: {type(e).__name__}: {e}"


def _resolve_business_transaction_activity_params(
    activity_params: dict[str, Any] | None,
    ref: dict,
    goal: str,
    preferences: dict | None = None,
) -> tuple[dict[str, Any] | None, dict | None, str | None]:
    """Resolve BusinessTransactionActivity params from planner output or reference fallback."""
    params = dict(activity_params or {})
    offer = None
    warning = None

    if params.get("offer_template_id") is not None:
        try:
            plan_offer_id = int(params["offer_template_id"])
            offer = next((item for item in ref.get("offers", []) if item.get("id") == plan_offer_id), None)
        except (TypeError, ValueError):
            offer = None

    if not offer:
        offer = _select_offer_template(ref, goal, preferences)
        if offer:
            warning = "LLM-планировщик не выбрал валидный offer_template_id; использован ближайший шаблон из справочника."

    if not offer:
        return None, None, None

    params["offer_template_id"] = int(offer["id"])
    params["operation_id"] = str(params.get("operation_id") or offer["operationId"])
    params.setdefault("operation_params", [])
    return params, offer, warning


def _select_offer_template(ref: dict, goal: str, preferences: dict | None = None) -> dict | None:
    """Выбирает шаблон оффера для deterministic follow-up без LLM.

    Сначала пытаемся найти совпадение по рекомендациям/тексту, иначе берём
    первый доступный шаблон из справочника, чтобы запрос "добавь транзакцию"
    не падал из-за отсутствия явного id.
    """
    offers = ref.get("offers", [])
    if not offers:
        return None

    preference_text = ""
    if isinstance(preferences, dict):
        preference_text = " ".join(str(v) for v in preferences.values() if v)
    haystack = _normalize_text(f"{goal} {preference_text}")

    for offer in offers:
        name = _normalize_text(str(offer.get("name", "")))
        operation_id = _normalize_text(str(offer.get("operationId", "")))
        if (name and name in haystack) or (operation_id and operation_id in haystack):
            return offer

    return offers[0]


def _find_activity_anchor_index(
    activities: list[dict],
    *,
    anchor_type: str | None = None,
    anchor_id: str | None = None,
) -> int | None:
    """Find an anchor by id first, otherwise the last activity with matching type."""
    if anchor_id:
        return next(
            (
                index
                for index, activity in enumerate(activities)
                if isinstance(activity, dict) and activity.get("id") == anchor_id
            ),
            None,
        )

    if anchor_type:
        for index in range(len(activities) - 1, -1, -1):
            activity = activities[index]
            if isinstance(activity, dict) and activity.get("type") == anchor_type:
                return index

    return None


def _find_anchor_insert_index(
    activities: list[dict],
    *,
    anchor_type: str | None = None,
    anchor_id: str | None = None,
    position: str = "after",
) -> int:
    """Return insertion index around an anchor, or append when no anchor is found."""
    if position not in {"after", "before", "end"}:
        raise ValueError('position должен быть "after", "before" или "end"')
    if position == "end":
        return len(activities)

    anchor_index = _find_activity_anchor_index(
        activities,
        anchor_type=anchor_type,
        anchor_id=anchor_id,
    )
    if anchor_index is None:
        return len(activities)
    return anchor_index + 1 if position == "after" else anchor_index


def _insert_activity_into_flow(
    flow: dict,
    new_activity: dict,
    anchor_type: str | None = None,
    anchor_id: str | None = None,
    position: str = "after",
) -> dict:
    """Insert activity before/after an anchor and rebuild links and positions."""
    activities = list(flow.get("activities") or [])
    if not activities:
        raise ValueError("flow должен содержать activities[]")

    insert_index = _find_anchor_insert_index(
        activities,
        anchor_type=anchor_type,
        anchor_id=anchor_id,
        position=position,
    )
    updated_activities = list(activities)
    updated_activities.insert(insert_index, new_activity)
    return assemble_flow(updated_activities)


def _last_communication_activity_id(activities: list[dict]) -> str | None:
    for activity in reversed(activities):
        if (
            isinstance(activity, dict)
            and activity.get("type") in {"PushCommunicationActivity", "PullCommunicationActivity"}
        ):
            activity_id = activity.get("id")
            return str(activity_id) if activity_id else None
    return None


def _add_business_transaction_to_flow(
    flow: dict,
    offer_template_id: int,
    operation_id: str,
    *,
    anchor_activity_type: str | None = None,
) -> dict:
    """Add BusinessTransactionActivity after an anchor or the last communication node."""
    activities = list(flow.get("activities") or [])
    if not activities:
        raise ValueError("flow должен содержать activities[]")

    anchor_id = None if anchor_activity_type else _last_communication_activity_id(activities)
    insert_index = _find_anchor_insert_index(
        activities,
        anchor_type=anchor_activity_type,
        anchor_id=anchor_id,
        position="after" if (anchor_activity_type or anchor_id) else "end",
    )

    next_activity = activities[insert_index] if insert_index < len(activities) else None
    if (
        isinstance(next_activity, dict)
        and next_activity.get("type") == "BusinessTransactionActivity"
        and next_activity.get("offerTemplateId") == offer_template_id
        and (next_activity.get("businessOperation") or {}).get("id") == operation_id
    ):
        return assemble_flow(activities)

    return _insert_activity_into_flow(
        flow,
        make_business_transaction_activity(offer_template_id, operation_id, []),
        anchor_type=anchor_activity_type,
        anchor_id=anchor_id,
        position="after" if (anchor_activity_type or anchor_id) else "end",
    )


def _add_activity_to_flow(
    flow: dict,
    activity_type: str,
    *,
    activity_params: dict[str, Any] | None = None,
    anchor_activity_type: str | None = None,
    anchor_id: str | None = None,
    anchor_position: str | None = "after",
) -> dict:
    """Add a supported activity to an existing flow relative to an optional positional anchor."""
    new_activity = _make_activity_from_params(activity_type, activity_params or {})
    position = anchor_position or ("after" if (anchor_activity_type or anchor_id) else "end")
    return _insert_activity_into_flow(
        flow,
        new_activity,
        anchor_type=anchor_activity_type,
        anchor_id=anchor_id,
        position=position,
    )


def _parse_activity_params(activity_params: dict[str, Any] | str | None) -> dict[str, Any]:
    if activity_params is None:
        return {}
    if isinstance(activity_params, str):
        if not activity_params.strip():
            return {}
        parsed = json.loads(activity_params)
        if not isinstance(parsed, dict):
            raise ValueError("activity_params должен быть JSON-объектом")
        return parsed
    if isinstance(activity_params, dict):
        return activity_params
    raise ValueError("activity_params должен быть dict или JSON-строкой")


def _make_activity_from_params(activity_type: str, activity_params: dict[str, Any] | None = None) -> dict[str, Any]:
    if activity_type not in SUPPORTED_ACTIVITY_TYPES:
        raise ValueError(f"Неподдерживаемый тип активности: {activity_type}")
    params = activity_params or {}
    if activity_type == "RealTimeCheckActivity":
        return make_real_time_check_activity(filters=params.get("filters"))
    if activity_type == "ResponseActivity":
        return make_response_activity(
            params.get("response_code") or params.get("responseCode"),
            relevance_minutes=int(params.get("relevance_minutes") or params.get("responseRelevanceInMinutes") or 15),
            filters=params.get("filters"),
        )
    if activity_type == "InteractiveResponseActivity":
        return make_interactive_response_activity(
            params.get("response_code") or params.get("responseCode"),
            relevance_minutes=int(params.get("relevance_minutes") or params.get("responseRelevanceInMinutes") or 15),
            filters=params.get("filters"),
        )
    if activity_type == "PullCommunicationActivity":
        return make_pull_communication_activity(
            int(params["channel_id"]),
            str(params.get("content_type") or params.get("contentType") or "SmsContent"),
            str(params.get("message_text") or params.get("text") or ""),
            sender=params.get("sender"),
        )
    if activity_type == "PushCommunicationActivity":
        return make_push_communication_activity(
            int(params["channel_id"]),
            str(params.get("content_type") or params.get("contentType") or "SmsContent"),
            str(params.get("message_text") or params.get("text") or ""),
            sender=params.get("sender"),
        )
    if activity_type == "EventActivity":
        return make_event_activity(
            str(params["event_code"]),
            relevance_minutes=int(params.get("relevance_minutes") or 15),
            filters=params.get("filters"),
        )
    if activity_type == "WaitActivity":
        return make_wait_activity(int(params.get("wait_days") or params.get("days") or 1))
    if activity_type == "BusinessTransactionActivity":
        return make_business_transaction_activity(
            int(params["offer_template_id"]),
            str(params["operation_id"]),
            list(params.get("operation_params") or []),
        )
    if activity_type == "OrJoinActivity":
        return make_or_join_activity()
    raise ValueError(f"Неподдерживаемый тип активности: {activity_type}")



# ── Reference data pre-fetch (injected into prompt, no tool round-trips) ─────

async def _fetch_reference_data() -> dict:
    """Fetches all four reference datasets in parallel. Returns compact dicts."""
    import asyncio

    results = await asyncio.gather(
        adtarget.list_target_groups(),
        adtarget.list_channels(),
        adtarget.list_events(),
        adtarget.list_offer_templates(),
        return_exceptions=True,
    )
    tg_result, channels, events, offers = results

    ref: dict = {}

    if isinstance(tg_result, dict) and "items" in tg_result:
        ref["target_groups"] = [
            {"id": tg["id"], "name": tg["name"]}
            for tg in tg_result["items"]
        ]
    else:
        ref["target_groups"] = []

    if isinstance(channels, list):
        ref["channels"] = [
            {"id": ch["id"], "name": ch["name"], "contentType": ch["contentType"]}
            for ch in channels
        ]
    else:
        ref["channels"] = []

    if isinstance(events, list):
        ref["events"] = [{"code": ev["code"], "name": ev["name"]} for ev in events]
    else:
        ref["events"] = []

    if isinstance(offers, list):
        ref["offers"] = [
            {
                "id": t["id"],
                "name": t["name"],
                "operationId": t["businessOperation"]["id"],
            }
            for t in offers
        ]
    else:
        ref["offers"] = []

    return ref


def _cap_builder_reference(ref: dict) -> tuple[dict, list[str]]:
    """Уменьшает справочники для системного промпта (Groq tier / лимит токенов на запрос)."""
    max_tg = int(os.getenv("BUILDER_MAX_TARGET_GROUPS", "150"))
    max_ch = int(os.getenv("BUILDER_MAX_CHANNELS", "200"))
    max_ev = int(os.getenv("BUILDER_MAX_EVENTS", "400"))
    max_off = int(os.getenv("BUILDER_MAX_OFFERS", "300"))

    tgs = ref.get("target_groups", [])
    chs = ref.get("channels", [])
    evs = ref.get("events", [])
    offs = ref.get("offers", [])

    notes: list[str] = []
    if len(tgs) > max_tg:
        notes.append(f"Целевые группы: в промпте первые {max_tg} из {len(tgs)}.")
    if len(chs) > max_ch:
        notes.append(f"Каналы: в промпте первые {max_ch} из {len(chs)}.")
    if len(evs) > max_ev:
        notes.append(f"События: в промпте первые {max_ev} из {len(evs)}.")
    if len(offs) > max_off:
        notes.append(f"Шаблоны офферов: в промпте первые {max_off} из {len(offs)}.")

    return {
        "target_groups": tgs[:max_tg],
        "channels": chs[:max_ch],
        "events": evs[:max_ev],
        "offers": offs[:max_off],
    }, notes


def _build_system_prompt(ref: dict, truncation_notes: list[str] | None = None) -> str:
    """Generate system prompt with reference data inlined (no lookup tools needed)."""
    tg_lines  = "\n".join(f"  {tg['id']} = {tg['name']}" for tg in ref.get("target_groups", []))
    ch_lines  = "\n".join(f"  {ch['id']} = {ch['name']} ({ch['contentType']})" for ch in ref.get("channels", []))
    ev_lines  = "\n".join(f"  {ev['code']} = {ev['name']}" for ev in ref.get("events", []))
    off_lines = "\n".join(f"  id={t['id']} \"{t['name']}\" operationId={t['operationId']}" for t in ref.get("offers", []))

    ev_codes = ", ".join(ev["code"] for ev in ref.get("events", []))

    trunc_block = ""
    if truncation_notes:
        trunc_block = "\nСправочники усечены (лимит контекста LLM):\n" + "\n".join(
            f"- {line}" for line in truncation_notes
        ) + "\n"

    return f"""AdTarget Campaign Builder.
{trunc_block}
Target groups (target_group_id):
{tg_lines}

Channels (use id, NOT clientsCount):
{ch_lines}

Event codes: {ev_codes}

Offer templates (offer_template_id / operation_id):
{off_lines}

Rules:
- Work step-by-step: if the user only describes product, content, audience, goal, or offer preferences, acknowledge and keep these details in conversation; ask for missing inputs instead of forcing campaign creation.
- Build/create only when the user asks to build/create/assemble, or when enough concrete inputs are available and the intent is clearly campaign creation.
- For a new campaign: pick the right build_*_flow tool, then validate_flow_tool, then create_campaign_tool. Use start_campaign_tool only if user says to launch.
- For follow-up edits to an existing flow, use session_flow_json and update_existing_flow_with_activity instead of starting from scratch for all supported activity types: PushCommunicationActivity, PullCommunicationActivity, EventActivity, WaitActivity, BusinessTransactionActivity, RealTimeCheckActivity, ResponseActivity, InteractiveResponseActivity, OrJoinActivity.
- You may still use update_existing_flow_with_business_transaction for the legacy business-transaction-only edit path when only offer_template_id and operation_id are needed.
- For update_existing_flow_with_activity, pass anchor_type/anchor_id and position=after|before|end when the user asks to place an activity relative to any existing node.
- Prefer explicit Builder UI preferences when present (desired channels, target groups, offer recommendations, product/content/goal).
- Reply in Russian. SMS text must be concrete and in Russian.
"""


# ── Flow Builder инструменты ──────────────────────────────────────────────────

@tool
async def build_sms_campaign_flow(
    campaign_name: str,
    target_group_id: int,
    sms_channel_id: int,
    message_text: str,
) -> str:
    """Common → TargetGroup → SMS."""
    resolved_ch = await _resolve_channel_id(sms_channel_id, "SmsContent")
    flow = assemble_flow([
        make_common_activity(campaign_name),
        make_target_group_activity(target_group_id),
        make_push_communication_activity(resolved_ch, "SmsContent", message_text),
    ])
    return json.dumps(flow, ensure_ascii=False)


@tool
async def build_email_campaign_flow(
    campaign_name: str,
    target_group_id: int,
    email_channel_id: int,
    message_text: str,
) -> str:
    """Common → TargetGroup → Email."""
    resolved_ch = await _resolve_channel_id(email_channel_id, "EmailContent")
    flow = assemble_flow([
        make_common_activity(campaign_name),
        make_target_group_activity(target_group_id),
        make_push_communication_activity(resolved_ch, "EmailContent", message_text),
    ])
    return json.dumps(flow, ensure_ascii=False)


@tool
async def build_push_campaign_flow(
    campaign_name: str,
    target_group_id: int,
    push_channel_id: int,
    message_text: str,
) -> str:
    """Common → TargetGroup → Push (мобильный)."""
    resolved_ch = await _resolve_channel_id(push_channel_id, "CustomContent")
    flow = assemble_flow([
        make_common_activity(campaign_name),
        make_target_group_activity(target_group_id),
        make_push_communication_activity(resolved_ch, "CustomContent", message_text),
    ])
    return json.dumps(flow, ensure_ascii=False)


@tool
async def build_event_sms_campaign_flow(
    campaign_name: str,
    target_group_id: int,
    event_code: str,
    sms_channel_id: int,
    message_text: str,
) -> str:
    """Common → TargetGroup → Event → SMS."""
    resolved_ch = await _resolve_channel_id(sms_channel_id, "SmsContent")
    flow = assemble_flow([
        make_common_activity(campaign_name),
        make_target_group_activity(target_group_id),
        make_event_activity(event_code),
        make_push_communication_activity(resolved_ch, "SmsContent", message_text),
    ])
    return json.dumps(flow, ensure_ascii=False)


@tool
async def build_business_transaction_flow(
    campaign_name: str,
    target_group_id: int,
    sms_channel_id: int,
    message_text: str,
    offer_template_id: int,
    operation_id: str,
) -> str:
    """Common → TargetGroup → SMS → BusinessTransaction. Для промо и активации продукта."""
    resolved_ch = await _resolve_channel_id(sms_channel_id, "SmsContent")
    flow = assemble_flow([
        make_common_activity(campaign_name),
        make_target_group_activity(target_group_id),
        make_push_communication_activity(resolved_ch, "SmsContent", message_text),
        make_business_transaction_activity(offer_template_id, operation_id, []),
    ])
    return json.dumps(flow, ensure_ascii=False)


@tool
async def build_event_sms_with_bt_flow(
    campaign_name: str,
    target_group_id: int,
    event_code: str,
    sms_channel_id: int,
    message_text: str,
    offer_template_id: int,
    operation_id: str,
) -> str:
    """Common → TargetGroup → Event → SMS → BusinessTransaction. Для реактивных кампаний с активацией."""
    resolved_ch = await _resolve_channel_id(sms_channel_id, "SmsContent")
    flow = assemble_flow([
        make_common_activity(campaign_name),
        make_target_group_activity(target_group_id),
        make_event_activity(event_code),
        make_push_communication_activity(resolved_ch, "SmsContent", message_text),
        make_business_transaction_activity(offer_template_id, operation_id, []),
    ])
    return json.dumps(flow, ensure_ascii=False)


@tool
async def build_sms_with_wait_flow(
    campaign_name: str,
    target_group_id: int,
    sms_channel_id: int,
    message_text: str,
    wait_days: int = 3,
    offer_template_id: int = 0,
    operation_id: str = "",
) -> str:
    """Common → TargetGroup → Wait(N дней) → SMS [→ BusinessTransaction если offer_template_id>0]."""
    resolved_ch = await _resolve_channel_id(sms_channel_id, "SmsContent")
    activities = [
        make_common_activity(campaign_name),
        make_target_group_activity(target_group_id),
        make_wait_activity(wait_days),
        make_push_communication_activity(resolved_ch, "SmsContent", message_text),
    ]
    if offer_template_id and operation_id:
        activities.append(make_business_transaction_activity(offer_template_id, operation_id, []))
    flow = assemble_flow(activities)
    return json.dumps(flow, ensure_ascii=False)


@tool
async def update_existing_flow_with_business_transaction(
    flow_json: str,
    offer_template_id: int,
    operation_id: str,
) -> str:
    """Добавить BusinessTransactionActivity после последней communication-ноды существующего flow."""
    flow = json.loads(flow_json)
    updated_flow = _add_business_transaction_to_flow(flow, offer_template_id, operation_id)
    return json.dumps(updated_flow, ensure_ascii=False)


@tool
async def update_existing_flow_with_activity(
    flow_json: str,
    activity_type: str,
    activity_params: dict[str, Any] | str | None = None,
    anchor_type: str | None = None,
    anchor_id: str | None = None,
    position: str = "after",
) -> str:
    """Добавить поддержанную activity в существующий flow рядом с anchor_type/anchor_id.

    activity_params можно передать dict или JSON-строкой. Поддержаны:
    PushCommunicationActivity, PullCommunicationActivity, EventActivity, WaitActivity,
    BusinessTransactionActivity, RealTimeCheckActivity, ResponseActivity,
    InteractiveResponseActivity и OrJoinActivity. position: after|before|end.
    """
    flow = json.loads(flow_json) if isinstance(flow_json, str) else flow_json
    activities = flow.get("activities") if isinstance(flow, dict) else None
    if not isinstance(activities, list) or not activities:
        raise ValueError("flow_json должен содержать activities[]")

    params = _parse_activity_params(activity_params)
    updated_flow = _add_activity_to_flow(
        flow,
        activity_type,
        activity_params=params,
        anchor_activity_type=anchor_type,
        anchor_id=anchor_id,
        anchor_position=position,
    )
    return json.dumps(updated_flow, ensure_ascii=False)


# ── API инструменты ───────────────────────────────────────────────────────────

@tool
async def validate_flow_tool(flow_json: str) -> str:
    """Валидировать flow через AdTarget API. Возвращает errors[] и warnings[]."""
    try:
        result = await adtarget.validate_campaign(json.loads(flow_json))
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return _api_error("validate_flow", e)


@tool
async def create_campaign_tool(flow_json: str) -> str:
    """Создать кампанию в AdTarget (POST /Campaigns). Возвращает campaignId."""
    try:
        result = await adtarget.create_campaign(json.loads(flow_json))
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return _api_error("create_campaign", e)


@tool
async def start_campaign_tool(campaign_id: int) -> str:
    """Запустить кампанию (PUT /Campaigns/start). Только по явному запросу пользователя."""
    try:
        result = await adtarget.start_campaign(campaign_id)
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return _api_error("start_campaign", e)



# ── Pseudo tool-call recovery ────────────────────────────────────────────────

def _extract_pseudo_tool_calls(text: str) -> list[tuple[str, dict]]:
    """Extracts tool calls printed by an LLM as text instead of real tool_calls.

    Some providers occasionally return markup like
    ``<build_sms_campaign_flow>{...}</function>`` in ``content``. LangGraph
    treats that as a final assistant answer, so tools are never executed and the
    UI receives no ``draft_flow``. This parser is deliberately tolerant: it
    accepts both ``</function>`` and ``</tool_name>`` closing tags and ignores
    malformed JSON blocks.
    """
    import re

    calls: list[tuple[str, dict]] = []
    pattern = re.compile(
        r"<(?P<name>[A-Za-z_][\w]*)>\s*(?P<args>.*?)\s*</(?:function|(?P=name))>",
        re.DOTALL,
    )
    for match in pattern.finditer(text or ""):
        raw_args = match.group("args").strip()
        try:
            args = json.loads(raw_args)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(args, dict):
            calls.append((match.group("name"), args))
    return calls


def _flow_from_legacy_steps(data: dict) -> dict | None:
    """Converts old prototype ``{name, steps[]}`` flow JSON to activities[]."""
    steps = data.get("steps")
    if not isinstance(steps, list):
        return None

    campaign_name = data.get("name") or "Новая кампания"
    target_group_id: int | None = None
    sms_channel_id: int | None = None
    message_text: str | None = None

    for step in steps:
        if not isinstance(step, dict):
            continue
        step_type = step.get("type")
        params = step.get("params") if isinstance(step.get("params"), dict) else {}
        if step_type in {"Common", "TargetGroup", "TargetGroupActivity"} and params.get("target_group_id"):
            target_group_id = int(params["target_group_id"])
        elif step_type in {"SMS", "Sms", "PushCommunicationActivity"}:
            if params.get("sms_channel_id"):
                sms_channel_id = int(params["sms_channel_id"])
            if params.get("message_text"):
                message_text = str(params["message_text"])

    if target_group_id and sms_channel_id and message_text:
        return assemble_flow([
            make_common_activity(campaign_name),
            make_target_group_activity(target_group_id),
            make_push_communication_activity(sms_channel_id, "SmsContent", message_text),
        ])
    return None


async def _flow_from_tool_args(tool_name: str, args: dict) -> dict | None:
    """Builds a flow from parsed textual tool-call arguments."""
    try:
        if tool_name == "build_sms_campaign_flow":
            resolved_ch = await _resolve_channel_id(int(args["sms_channel_id"]), "SmsContent")
            return assemble_flow([
                make_common_activity(str(args["campaign_name"])),
                make_target_group_activity(int(args["target_group_id"])),
                make_push_communication_activity(resolved_ch, "SmsContent", str(args["message_text"])),
            ])
        if tool_name == "build_email_campaign_flow":
            resolved_ch = await _resolve_channel_id(int(args["email_channel_id"]), "EmailContent")
            return assemble_flow([
                make_common_activity(str(args["campaign_name"])),
                make_target_group_activity(int(args["target_group_id"])),
                make_push_communication_activity(resolved_ch, "EmailContent", str(args["message_text"])),
            ])
        if tool_name == "build_push_campaign_flow":
            resolved_ch = await _resolve_channel_id(int(args["push_channel_id"]), "CustomContent")
            return assemble_flow([
                make_common_activity(str(args["campaign_name"])),
                make_target_group_activity(int(args["target_group_id"])),
                make_push_communication_activity(resolved_ch, "CustomContent", str(args["message_text"])),
            ])
        if tool_name == "build_event_sms_campaign_flow":
            resolved_ch = await _resolve_channel_id(int(args["sms_channel_id"]), "SmsContent")
            return assemble_flow([
                make_common_activity(str(args["campaign_name"])),
                make_target_group_activity(int(args["target_group_id"])),
                make_event_activity(str(args["event_code"])),
                make_push_communication_activity(resolved_ch, "SmsContent", str(args["message_text"])),
            ])
        if tool_name == "build_business_transaction_flow":
            resolved_ch = await _resolve_channel_id(int(args["sms_channel_id"]), "SmsContent")
            return assemble_flow([
                make_common_activity(str(args["campaign_name"])),
                make_target_group_activity(int(args["target_group_id"])),
                make_push_communication_activity(resolved_ch, "SmsContent", str(args["message_text"])),
                make_business_transaction_activity(
                    int(args["offer_template_id"]),
                    str(args["operation_id"]),
                    [],
                ),
            ])
        if tool_name == "build_event_sms_with_bt_flow":
            resolved_ch = await _resolve_channel_id(int(args["sms_channel_id"]), "SmsContent")
            return assemble_flow([
                make_common_activity(str(args["campaign_name"])),
                make_target_group_activity(int(args["target_group_id"])),
                make_event_activity(str(args["event_code"])),
                make_push_communication_activity(resolved_ch, "SmsContent", str(args["message_text"])),
                make_business_transaction_activity(
                    int(args["offer_template_id"]),
                    str(args["operation_id"]),
                    [],
                ),
            ])
        if tool_name == "build_sms_with_wait_flow":
            resolved_ch = await _resolve_channel_id(int(args["sms_channel_id"]), "SmsContent")
            activities = [
                make_common_activity(str(args["campaign_name"])),
                make_target_group_activity(int(args["target_group_id"])),
                make_wait_activity(int(args.get("wait_days") or 3)),
                make_push_communication_activity(resolved_ch, "SmsContent", str(args["message_text"])),
            ]
            offer_template_id = int(args.get("offer_template_id") or 0)
            operation_id = str(args.get("operation_id") or "")
            if offer_template_id and operation_id:
                activities.append(make_business_transaction_activity(offer_template_id, operation_id, []))
            return assemble_flow(activities)
        if tool_name == "update_existing_flow_with_business_transaction" and args.get("flow_json"):
            flow_data = json.loads(args["flow_json"]) if isinstance(args["flow_json"], str) else args["flow_json"]
            if isinstance(flow_data, dict) and isinstance(flow_data.get("activities"), list):
                activities = list(flow_data["activities"])
                communication_indexes = [
                    i
                    for i, activity in enumerate(activities)
                    if isinstance(activity, dict)
                    and activity.get("type") in {"PushCommunicationActivity", "PullCommunicationActivity"}
                ]
                insert_index = (communication_indexes[-1] + 1) if communication_indexes else len(activities)
                activities.insert(
                    insert_index,
                    make_business_transaction_activity(
                        int(args["offer_template_id"]),
                        str(args["operation_id"]),
                        [],
                    ),
                )
                return assemble_flow(activities)
        if tool_name == "update_existing_flow_with_activity" and args.get("flow_json"):
            flow_data = json.loads(args["flow_json"]) if isinstance(args["flow_json"], str) else args["flow_json"]
            if isinstance(flow_data, dict) and isinstance(flow_data.get("activities"), list):
                activities = list(flow_data["activities"])
                params = _parse_activity_params(args.get("activity_params"))
                activities.insert(
                    _find_anchor_insert_index(
                        activities,
                        anchor_type=args.get("anchor_type"),
                        anchor_id=args.get("anchor_id"),
                        position=args.get("position") or "after",
                    ),
                    _make_activity_from_params(str(args["activity_type"]), params),
                )
                return assemble_flow(activities)
        if tool_name in {"validate_flow_tool", "create_campaign_tool"} and args.get("flow_json"):
            flow_data = json.loads(args["flow_json"]) if isinstance(args["flow_json"], str) else args["flow_json"]
            if isinstance(flow_data, dict):
                if isinstance(flow_data.get("activities"), list):
                    return flow_data
                return _flow_from_legacy_steps(flow_data)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return None


async def _recover_flow_from_textual_tool_calls(text: str) -> dict | None:
    """Returns the richest flow represented by pseudo tool calls in text."""
    best_flow: dict | None = None
    best_len = -1
    for tool_name, args in _extract_pseudo_tool_calls(text):
        flow = await _flow_from_tool_args(tool_name, args)
        if not flow:
            continue
        activities_len = len(flow.get("activities", []))
        # Prefer richer builders (e.g. SMS → BusinessTransaction) over later
        # hallucinated simple SMS validation payloads.
        if activities_len > best_len:
            best_flow = flow
            best_len = activities_len
    return best_flow

# ── LangGraph State ───────────────────────────────────────────────────────────

class BuilderState(TypedDict):
    messages: Annotated[list, add_messages]
    campaign_id: int | None
    last_flow_json: str | None
    system_prompt: str          # injected per-run with live reference data


# ── Tool list (lookup tools removed — data injected into prompt instead) ─────

TOOLS = [
    # Flow builders
    build_sms_campaign_flow,
    build_email_campaign_flow,
    build_push_campaign_flow,
    build_event_sms_campaign_flow,
    build_business_transaction_flow,
    build_event_sms_with_bt_flow,
    build_sms_with_wait_flow,
    update_existing_flow_with_business_transaction,
    update_existing_flow_with_activity,
    # API
    validate_flow_tool,
    create_campaign_tool,
    start_campaign_tool,
]


def _extract_state_from_messages(messages: list) -> tuple[int | None, str | None]:
    """Сканирует tool results и извлекает campaign_id и last_flow_json."""
    campaign_id = None
    last_flow_json = None

    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue
        content = msg.content if isinstance(msg.content, str) else ""
        if not content:
            continue
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            continue

        # campaignId из create_campaign_tool
        if isinstance(data, dict) and "campaignId" in data:
            campaign_id = data["campaignId"]

        # flow JSON из build_*_flow tools (has "activities" key)
        if isinstance(data, dict) and "activities" in data:
            last_flow_json = content

        # start_campaign result
        if isinstance(data, list) and data and "isSuccess" in data[0]:
            pass  # just for status detection in run()

    return campaign_id, last_flow_json


def _build_graph() -> StateGraph:
    llm = get_llm(for_tools=True).bind_tools(TOOLS)
    tool_node = ToolNode(TOOLS)

    async def call_model(state: BuilderState) -> dict:
        from langchain_core.messages import SystemMessage
        sys_prompt = state.get("system_prompt") or ""
        budget = int(os.getenv("BUILDER_MESSAGE_TOKEN_BUDGET", "26000"))
        chat_tail = trim_messages(
            state["messages"],
            max_tokens=budget,
            strategy="last",
            token_counter="approximate",
            start_on="human",
        )
        messages = [SystemMessage(content=sys_prompt)] + chat_tail
        response = await llm.ainvoke(messages)

        campaign_id, last_flow_json = _extract_state_from_messages(state["messages"])

        # Если в state уже был campaign_id — не затираем
        if state.get("campaign_id") and not campaign_id:
            campaign_id = state["campaign_id"]
        if state.get("last_flow_json") and not last_flow_json:
            last_flow_json = state["last_flow_json"]

        return {
            "messages": [response],
            "campaign_id": campaign_id,
            "last_flow_json": last_flow_json,
            "system_prompt": state.get("system_prompt", ""),
        }

    graph = StateGraph(BuilderState)
    graph.add_node("agent", call_model)
    graph.add_node("tools", tool_node)
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", tools_condition)
    graph.add_edge("tools", "agent")

    return graph.compile()


_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = _build_graph()
    return _graph


# ── Основная функция ──────────────────────────────────────────────────────────

async def run(request: BuilderRequest) -> BuilderResponse:
    """Запускает один шаг агента и возвращает ответ."""
    existing_flow = _parse_flow_json(request.session_flow_json)
    flow_edit_intent = _parse_flow_edit_intent(request.goal)

    # Context-only turns go through an LLM planner, but never create a campaign.
    if _is_memory_only_request(request.goal):
        plan, plan_warning = await _llm_plan_special_turn(
            request.goal,
            preferences=request.builder_preferences,
            existing_flow=existing_flow,
        )
        message = (
            plan.get("assistant_message")
            if plan and plan.get("action") == "remember_context" and plan.get("assistant_message")
            else "Запомнил вводные для этой сборки. Когда будете готовы, напишите «собери кампанию» или уточните недостающие параметры."
        )
        if plan_warning:
            message += f"\n\n⚠️ LLM-планировщик не смог подтвердить шаг ({plan_warning}); контекст сохранён без сборки кампании."
        return BuilderResponse(
            message=message,
            campaign_id=request.session_campaign_id,
            draft_flow=existing_flow,
            status="created" if request.session_campaign_id else "in_progress",
        )

    # ── Pre-fetch reference data in parallel → inject into system prompt ──────
    # This eliminates 4 lookup tool calls per run (~40% fewer LLM calls, ~50% fewer tokens)
    ref_full = await _fetch_reference_data()
    ref, trunc_notes = _cap_builder_reference(ref_full)
    system_prompt = _build_system_prompt(ref, trunc_notes)
    if request.builder_preferences:
        preferences_json = json.dumps(request.builder_preferences, ensure_ascii=False)
        system_prompt += f"\nBuilder UI preferences (use as user-provided constraints): {preferences_json}\n"
    print(f"[campaign_builder] Ref data (API total → prompt): "
          f"{len(ref_full.get('target_groups', []))}→{len(ref['target_groups'])} TGs, "
          f"{len(ref_full.get('channels', []))}→{len(ref['channels'])} channels, "
          f"{len(ref_full.get('events', []))}→{len(ref['events'])} events, "
          f"{len(ref_full.get('offers', []))}→{len(ref['offers'])} offers")

    planned_flow_edit: dict | None = None
    planned_flow_edit_warning: str | None = None

    if flow_edit_intent and flow_edit_intent.action == "add_activity":
        planned_flow_edit = {
            "action": "add_activity",
            "activity_type": flow_edit_intent.activity_type,
            "anchor_type": flow_edit_intent.anchor_activity_type,
            "anchor_position": "after" if flow_edit_intent.anchor_activity_type else None,
            "activity_params": {},
            "assistant_message": "",
        }
        if existing_flow and flow_edit_intent.activity_type == "BusinessTransactionActivity":
            planner_plan, planned_flow_edit_warning = await _llm_plan_special_turn(
                request.goal,
                preferences=request.builder_preferences,
                existing_flow=existing_flow,
                ref=ref_full,
            )
            if planner_plan and planner_plan.get("action") == "add_activity":
                planner_plan.setdefault("activity_type", "BusinessTransactionActivity")
                if not planner_plan.get("anchor_type"):
                    planner_plan["anchor_type"] = flow_edit_intent.anchor_activity_type
                if not planner_plan.get("anchor_position") and flow_edit_intent.anchor_activity_type:
                    planner_plan["anchor_position"] = "after"
                planned_flow_edit = planner_plan
    elif existing_flow and _looks_like_flow_activity_edit(request.goal):
        planned_flow_edit, planned_flow_edit_warning = await _llm_plan_special_turn(
            request.goal,
            preferences=request.builder_preferences,
            existing_flow=existing_flow,
            ref=ref_full,
        )
        if planned_flow_edit and planned_flow_edit.get("action") != "add_activity":
            planned_flow_edit = None

    if planned_flow_edit and planned_flow_edit.get("action") == "add_activity":
        activity_type = planned_flow_edit.get("activity_type")
        if not _is_supported_activity_type(activity_type):
            return BuilderResponse(
                message=_unsupported_activity_message(activity_type),
                campaign_id=request.session_campaign_id,
                draft_flow=existing_flow,
                status="error",
            )

        if not existing_flow:
            return BuilderResponse(
                message=(
                    "Не нашёл текущий flow для доработки. Сначала соберите кампанию, "
                    "затем повторите запрос на добавление активности."
                ),
                campaign_id=request.session_campaign_id,
                draft_flow=None,
                status="error",
            )

        activity_params = dict(planned_flow_edit.get("activity_params") or {})
        offer = None
        if activity_type == "BusinessTransactionActivity":
            resolved_params, offer, fallback_warning = _resolve_business_transaction_activity_params(
                activity_params,
                ref_full,
                request.goal,
                request.builder_preferences,
            )
            if fallback_warning and not planned_flow_edit_warning:
                planned_flow_edit_warning = fallback_warning
            if not resolved_params:
                return BuilderResponse(
                    message=(
                        "Не удалось выбрать шаблон оффера для бизнес-транзакции: справочник офферов пуст "
                        "или недоступен. Укажите offer_template_id и operation_id или проверьте доступ к AdTarget API."
                    ),
                    campaign_id=request.session_campaign_id,
                    draft_flow=existing_flow,
                    status="error",
                )
            activity_params = resolved_params

        try:
            updated_flow = _add_activity_to_flow(
                existing_flow,
                str(activity_type),
                activity_params=activity_params,
                anchor_activity_type=planned_flow_edit.get("anchor_type"),
                anchor_id=planned_flow_edit.get("anchor_id"),
                anchor_position=planned_flow_edit.get("anchor_position"),
            )
        except Exception as e:
            return BuilderResponse(
                message=f"Не удалось добавить активность в текущий flow: {type(e).__name__}: {e}",
                campaign_id=request.session_campaign_id,
                draft_flow=existing_flow,
                status="error",
            )

        anchor_type = planned_flow_edit.get("anchor_type")
        anchor_position = planned_flow_edit.get("anchor_position") or "after"
        if anchor_type:
            anchor_text = f" {anchor_position} {anchor_type}"
        else:
            anchor_text = " в конец flow"
        message = planned_flow_edit.get("assistant_message") or f"Добавил {activity_type}{anchor_text}."
        if offer:
            message += (
                f"\n\nШаблон оффера: **{offer.get('name', offer['id'])}** "
                f"(id={offer['id']}, operationId={activity_params['operation_id']})."
            )
        if planned_flow_edit_warning:
            message += f"\n\n⚠️ {planned_flow_edit_warning}"

        return BuilderResponse(
            message=message,
            campaign_id=request.session_campaign_id,
            draft_flow=updated_flow,
            status="created" if request.session_campaign_id else "in_progress",
        )

    messages = []
    for msg in request.history:
        if msg["role"] == "user":
            messages.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            messages.append(AIMessage(content=msg["content"]))

    # Если есть контекст сессии (campaignId из предыдущего хода) — подсказываем агенту
    if request.session_campaign_id:
        ctx_hint = f"[Контекст сессии] Уже создана кампания ID: {request.session_campaign_id}."
        if request.session_flow_json:
            ctx_hint += " Flow кампании доступен в session_flow_json."
        messages.append(AIMessage(content=ctx_hint))

    messages.append(HumanMessage(content=request.goal))

    initial_campaign_id = request.session_campaign_id
    initial_flow_json = request.session_flow_json

    graph = get_graph()
    try:
        result = await graph.ainvoke({
            "messages": messages,
            "campaign_id": initial_campaign_id,
            "last_flow_json": initial_flow_json,
            "system_prompt": system_prompt,
        })
    except Exception as e:
        print(f"[campaign_builder] LLM/tool graph failed: {type(e).__name__}: {e}")
        return BuilderResponse(
            message=(
                "Не удалось завершить шаг через LLM: "
                f"{type(e).__name__}: {e}. "
                "Риск: текущая модель или tool-call могли вернуть некорректный JSON/пустой ответ; "
                "попробуйте повторить запрос или уточнить параметры."
            ),
            campaign_id=initial_campaign_id,
            draft_flow=existing_flow,
            status="error",
        )

    last_message = result["messages"][-1]
    answer_text = last_message.content if hasattr(last_message, "content") else str(last_message)
    campaign_id = result.get("campaign_id")
    last_flow_json = result.get("last_flow_json")

    # ── Debug: log tool call sequence ────────────────────────────────────────────
    tool_calls_made = []
    for msg in result["messages"]:
        if isinstance(msg, ToolMessage):
            tool_calls_made.append(msg.name if hasattr(msg, "name") else "?")
    if tool_calls_made:
        print(f"[campaign_builder] Tools called: {tool_calls_made}")
    else:
        print("[campaign_builder] WARNING: No tool calls detected in result messages!")

    # ── Recovery: some LLMs print pseudo tool calls instead of real tool_calls ──
    # Without this fallback the chat displays raw <tool>{...}</function> text and
    # the prototype has no draft_flow to render. Recover the richest flow and let
    # the existing auto-create path persist it in AdTarget.
    recovered_from_text = False
    if not last_flow_json and isinstance(answer_text, str) and "</function>" in answer_text:
        recovered_flow = await _recover_flow_from_textual_tool_calls(answer_text)
        if recovered_flow:
            last_flow_json = json.dumps(recovered_flow, ensure_ascii=False)
            recovered_from_text = True
            print("[campaign_builder] Recovered flow from textual tool calls")

    # ── Авто-создание: если агент построил flow но не вызвал create_campaign ──
    auto_created = False
    if last_flow_json and not campaign_id:
        try:
            flow_data = json.loads(last_flow_json)
            create_result = await adtarget.create_campaign(flow_data)
            campaign_id = create_result.get("campaignId")
            auto_created = bool(campaign_id)
            print(f"[campaign_builder] Auto-created campaign: campaignId={campaign_id}")
        except Exception as e:
            print(f"[campaign_builder] Auto-create failed: {e}")

    # Парсим draft_flow для передачи в UI
    draft_flow = None
    if last_flow_json:
        try:
            draft_flow = json.loads(last_flow_json)
        except (json.JSONDecodeError, TypeError):
            pass

    # Определяем статус
    started = False
    for msg in reversed(result["messages"]):
        if not isinstance(msg, ToolMessage):
            continue
        content = msg.content if isinstance(msg.content, str) else ""
        try:
            data = json.loads(content)
            if isinstance(data, list) and data and "isSuccess" in data[0]:
                started = data[0]["isSuccess"]
                break
        except (json.JSONDecodeError, TypeError):
            pass

    if started:
        status = "started"
    elif campaign_id:
        status = "created"
    else:
        status = "in_progress"

    # ── Финализируем сообщение ────────────────────────────────────────────────
    # Если auto_created/recovered_from_text — заменяем служебный вывод агента на
    # чёткое подтверждение. Это скрывает raw <tool>{...}</function> из чата.
    if (auto_created and campaign_id) or recovered_from_text:
        flow_name = ""
        if draft_flow and draft_flow.get("activities"):
            for act in draft_flow["activities"]:
                if act.get("type") == "CommonActivity" and act.get("name"):
                    flow_name = f' «{act["name"]}»'
                    break
        if campaign_id:
            answer_text = f"Кампания{flow_name} создана. ID: **{campaign_id}**"
            if started:
                answer_text += " — запущена ✅"
            else:
                answer_text += "\n\nFlow собран и сохранён в AdTarget. Хотите запустить кампанию?"
        else:
            answer_text = (
                f"Flow кампании{flow_name} собран и готов к отображению на прототипе. "
                "Создание в AdTarget пока не выполнено — проверьте доступность API и повторите запрос."
            )

    return BuilderResponse(
        message=answer_text,
        campaign_id=campaign_id,
        draft_flow=draft_flow,
        status=status,
    )
