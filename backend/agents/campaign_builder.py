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
from typing import Annotated

from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, trim_messages
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
    assemble_flow,
)


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

    return f"""AdTarget Campaign Builder. Always end with create_campaign_tool.
{trunc_block}
Target groups (target_group_id):
{tg_lines}

Channels (use id, NOT clientsCount):
{ch_lines}

Event codes: {ev_codes}

Offer templates (offer_template_id / operation_id):
{off_lines}

Rules: pick the right build_*_flow tool, then validate_flow_tool, then create_campaign_tool. Use start_campaign_tool only if user says to launch. Reply in Russian. SMS text must be concrete and in Russian.
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
    graph = get_graph()

    # ── Pre-fetch reference data in parallel → inject into system prompt ──────
    # This eliminates 4 lookup tool calls per run (~40% fewer LLM calls, ~50% fewer tokens)
    ref_full = await _fetch_reference_data()
    ref, trunc_notes = _cap_builder_reference(ref_full)
    system_prompt = _build_system_prompt(ref, trunc_notes)
    print(f"[campaign_builder] Ref data (API total → prompt): "
          f"{len(ref_full.get('target_groups', []))}→{len(ref['target_groups'])} TGs, "
          f"{len(ref_full.get('channels', []))}→{len(ref['channels'])} channels, "
          f"{len(ref_full.get('events', []))}→{len(ref['events'])} events, "
          f"{len(ref_full.get('offers', []))}→{len(ref['offers'])} offers")

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

    result = await graph.ainvoke({
        "messages": messages,
        "campaign_id": initial_campaign_id,
        "last_flow_json": initial_flow_json,
        "system_prompt": system_prompt,
    })

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
