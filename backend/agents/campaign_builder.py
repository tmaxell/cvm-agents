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
from schemas import BuilderRequest, BuilderResponse, CampaignBriefCompleteness, FlowPatch
from agents.safety_review import build_review_checklist, is_review_allowed_for_runtime
from agents.flow_composer import compose_campaign_flow_result
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
    text = (text or "").replace("ё", "е")
    # Make camelCase/PascalCase operationId values searchable as separate words.
    text = re.sub(r"(?<=[a-zа-я0-9])(?=[A-ZА-Я])", " ", text)
    return text.strip().lower()


def _text_tokens(text: str | None) -> set[str]:
    """Normalize text and split it into searchable tokens."""
    return set(re.findall(r"[a-zа-я0-9]+", _normalize_text(text)))


def _stringify_preference_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_stringify_preference_value(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_stringify_preference_value(item) for item in value)
    return str(value)


def _model_to_plain(value: Any) -> Any:
    """Convert Pydantic models/lists into JSON-serializable primitives."""
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    if isinstance(value, dict):
        return {key: _model_to_plain(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_model_to_plain(item) for item in value]
    return value


def _campaign_brief_context(request: BuilderRequest) -> dict[str, Any]:
    """Return typed campaign brief context for planner/composer prompts."""
    brief = request.campaign_brief
    if not brief:
        return {}
    payload = _model_to_plain(brief)
    if not isinstance(payload, dict):
        return {}
    return payload


def _has_audience_context(audience: Any) -> bool:
    if not audience:
        return False
    target_groups = getattr(audience, "target_groups", None) or []
    description = (getattr(audience, "description", None) or "").strip()
    selected_segment = getattr(audience, "selected_segment", None)
    return bool(target_groups or description or selected_segment)


def check_campaign_brief_completeness(request: BuilderRequest) -> CampaignBriefCompleteness:
    """Validate required Campaign Builder brief fields and expose UI-safe metadata."""
    brief = request.campaign_brief
    preferences = request.builder_preferences or {}

    def text_present(value: Any) -> bool:
        return bool(str(value or "").strip())

    missing_fields: list[str] = []
    assumptions: list[str] = []
    safety_checks = [
        "Проверьте доступность аудитории для контакта, согласия на канал и возможность отписки.",
        "Проверьте право клиента на оффер, соответствие продукта и юридический текст.",
    ]

    if not text_present(getattr(brief, "goal", None)) and not text_present(preferences.get("goal")):
        missing_fields.append("goal")
    brief_offer = getattr(getattr(brief, "constraints", None), "offer_recommendations", None)
    if (
        not text_present(getattr(brief, "product", None))
        and not text_present(brief_offer)
        and not text_present(preferences.get("product"))
        and not text_present(preferences.get("offerRecommendations"))
    ):
        missing_fields.append("product/offer")
    if (
        not _has_audience_context(getattr(brief, "audience", None))
        and not text_present(preferences.get("targetGroups"))
    ):
        missing_fields.append("audience")

    channels = getattr(brief, "channels", None) or []
    has_channels = any(
        text_present(getattr(channel, "name", None)) for channel in channels
    ) or text_present(preferences.get("channels"))
    if not has_channels:
        missing_fields.append("channels")
        assumptions.append("channels: SMS + Push")

    return CampaignBriefCompleteness(
        missing_fields=missing_fields,
        assumptions=assumptions,
        safety_checks=safety_checks,
    )


def _current_draft_flow_version(request: BuilderRequest) -> int:
    value = request.draft_flow_version
    return value if isinstance(value, int) and value > 0 else 0


def _next_draft_flow_version(request: BuilderRequest) -> int:
    return _current_draft_flow_version(request) + 1


def _builder_response(request: BuilderRequest, **kwargs: Any) -> BuilderResponse:
    """Create BuilderResponse with brief completeness, review checklist, and draft version."""
    kwargs.setdefault("brief_completeness", check_campaign_brief_completeness(request))
    if kwargs.get("draft_flow") is not None and kwargs.get("draft_flow_version") is None:
        current_version = _current_draft_flow_version(request)
        kwargs["draft_flow_version"] = current_version or 1
    checklist = kwargs.get("review_checklist") or build_review_checklist(
        request.campaign_brief,
        kwargs.get("draft_flow"),
        kwargs.get("validation_errors") or [],
    )
    kwargs["review_checklist"] = checklist
    kwargs.setdefault("review_status", checklist.status)
    kwargs.setdefault("review_checklist_acknowledged", request.review_checklist_acknowledged)
    return BuilderResponse(**kwargs)


def _status_for_flow_context(campaign_id: int | None, draft_flow: dict[str, Any] | None) -> str:
    if campaign_id:
        return "created_in_adtarget"
    if draft_flow is not None:
        return "draft_ready"
    return "collect_brief"

def _looks_like_create_or_launch_request(goal: str) -> bool:
    text = _normalize_text(goal)
    return bool(re.search(r"\b(create|launch|start)\b|созда|запус|старт", text))


def _blocked_runtime_review_message(checklist_status: str) -> str:
    if checklist_status == "warnings":
        return (
            "Действие заблокировано: чеклист готовности содержит предупреждения. "
            "Попросите пользователя явно подтвердить допустимые предупреждения и повторите действие."
        )
    return (
        "Действие заблокировано: чеклист готовности содержит критичные замечания. "
        "Исправьте чеклист до статуса «Готово» перед созданием или запуском кампании."
    )


def _structured_audience_context(request: BuilderRequest) -> dict[str, Any]:
    """Return selected Audience Builder segment without relying on human text parsing."""
    brief_context = _campaign_brief_context(request)
    audience = brief_context.get("audience") if isinstance(brief_context, dict) else None
    if not isinstance(audience, dict):
        return {}
    selected = audience.get("selected_segment")
    if isinstance(selected, dict) and selected:
        return selected
    return {}


def _selected_target_group_id(audience: dict[str, Any]) -> int | None:
    """Extract an existing Target Group id from structured selected-segment context."""
    if not audience or audience.get("recommendationOnly"):
        return None
    if not audience.get("is_existing_target_group"):
        return None
    match = audience.get("matched_target_group")
    if not isinstance(match, dict):
        return None
    raw_id = match.get("id") if match.get("id") not in (None, "") else match.get("target_group_id")
    try:
        return int(raw_id)
    except (TypeError, ValueError):
        return None


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


PREFERENCE_KEYS = {
    "product",
    "goal",
    "channels",
    "targetGroups",
    "content",
    "offerRecommendations",
}

PREFERENCE_LABELS = {
    "product": (
        "продукт", "product", "тариф", "tariff", "услуга", "service",
    ),
    "goal": (
        "цель", "goal", "задача", "objective",
    ),
    "channels": (
        "каналы", "канал", "channels", "channel",
    ),
    "targetGroups": (
        "цг", "целевая группа", "целевые группы", "target group", "target groups", "audience", "аудитория",
    ),
    "content": (
        "контент", "content", "сообщение", "copy", "креатив", "текст",
    ),
    "offerRecommendations": (
        "оффер", "офферы", "рекомендации оффера", "offer", "offers", "offer recommendations",
    ),
}


def _coerce_preference_patch(patch: Any) -> dict[str, Any]:
    """Keep only supported non-empty preference fields from planner output."""
    if not isinstance(patch, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for key in PREFERENCE_KEYS:
        value = patch.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue
        cleaned[key] = value
    return cleaned


def _merge_builder_preferences(
    current: dict[str, Any] | None,
    patch: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge a preference patch over existing Builder UI preferences."""
    merged = dict(current or {})
    merged.update(_coerce_preference_patch(patch))
    return merged


def _extract_preference_patch_from_text(goal: str) -> dict[str, Any]:
    """Best-effort deterministic fallback for common 'remember X: Y' turns."""
    text = re.sub(r"^\s*(запомни|учти|remember|note that)\s*[:：,-]?\s*", "", goal.strip(), flags=re.IGNORECASE)
    patch: dict[str, Any] = {}

    # Split multiple preferences, while preserving values such as "тариф Max".
    parts = [part.strip(" .;\n\t") for part in re.split(r"\s*(?:;|\n|,\s*(?=(?:цель|канал|контент|оффер|цг|target|goal|channels?|content|offers?)\b))\s*", text) if part.strip()]
    for part in parts:
        for key, labels in PREFERENCE_LABELS.items():
            label_pattern = "|".join(re.escape(label) for label in sorted(labels, key=len, reverse=True))
            match = re.match(rf"^(?:{label_pattern})\s*(?:—|-|:|=|это|is|are)?\s*(.+)$", part, flags=re.IGNORECASE)
            if not match:
                continue
            value = match.group(1).strip(" .;\n\t")
            if value:
                patch[key] = value
            break
    return patch


@dataclass(frozen=True)
class FlowEditIntent:
    """Parsed follow-up edit intent for an existing campaign flow."""

    action: str
    activity_type: str | None = None
    anchor_activity_type: str | None = None
    activity_id: str | None = None
    anchor_id: str | None = None
    anchor_position: str | None = None
    occurrence: str = "last"


def _extract_position_anchor(text: str) -> str | None:
    """Extracts a positional anchor like "после транзакции" from normalized text."""
    anchor_patterns = (
        (
            r"\b(?:после|after)\s+"
            r"(?:бизнес[-\s]?транзакц\w*|транзакц\w*|business\s+transaction|transaction)\b",
            "BusinessTransactionActivity",
        ),
        (
            r"\b(?:после|after)\s+(?:sms|смс|с\.?м\.?с\.?|сообщен\w*|e-?mail|email|им[еэ]йл\w*|письм\w*)\b",
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
        r"\b(?:после|after)\s+(?:sms|смс|с\.?м\.?с\.?|сообщен\w*|e-?mail|email|им[еэ]йл\w*|письм\w*)\b",
        r"\b(?:после|after)\s+(?:событ\w*|event)\b",
    )
    result = text
    for pattern in patterns:
        result = re.sub(pattern, " ", result)
    return result


def _extract_activity_id(text: str) -> str | None:
    """Extract an explicit activity/node id from natural-language edit text."""
    patterns = (
        r"\b(?:activity_id|activity id|node_id|node id|anchor_id|anchor id|id)\s*[:=]?\s*([0-9a-f-]{8,})\b",
        r"\b(?:активност(?:и|ь)|нод[уы]|узл[ао]?)\s+(?:с\s+)?id\s*[:=]?\s*([0-9a-f-]{8,})\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None


def _parse_flow_edit_intent(goal: str) -> FlowEditIntent | None:
    """Parse follow-up intent into target activity and optional positional anchor.

    Priority matters: explicit removal wording wins before add detection, and
    explicit real-time-check wording wins over nearby transaction words because
    "транзакция" can be an anchor, not the entity being added.
    """
    text = _normalize_text(goal)
    if not text:
        return None

    anchor_activity_type = _extract_position_anchor(text)
    activity_id = _extract_activity_id(text)

    remove_pattern = r"(?:\bremove\b|\bdelete\b|убер\w*|удал\w*)"
    node_pattern = r"(?:нод[уаы]?|уз[её]?л\w*|node)"
    business_transaction_pattern = (
        r"(?:бизнес[-\s]?транзакц\w*|транзакц\w*|business\s+transaction|"
        r"\btransaction\b|активац\w*\s+оффер\w*|offer\s+activation)"
    )
    last_marker_pattern = r"(?:последн\w*|\blast\b)"
    if re.search(remove_pattern, text):
        if re.search(business_transaction_pattern, text):
            occurrence = "last" if re.search(last_marker_pattern, text) else "last"
            return FlowEditIntent(
                action="remove_activity",
                activity_type="BusinessTransactionActivity",
                anchor_activity_type=anchor_activity_type,
                activity_id=activity_id,
                anchor_id=activity_id,
                occurrence=occurrence,
            )
        if re.search(node_pattern, text) or activity_id:
            return FlowEditIntent(
                action="remove_activity",
                activity_type=None,
                anchor_activity_type=anchor_activity_type,
                activity_id=activity_id,
                anchor_id=activity_id,
                occurrence="last",
            )

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
    normalized.setdefault("activity_id", None)
    normalized.setdefault("occurrence", "last")
    normalized.setdefault("activity_params", {})
    normalized["preference_patch"] = _coerce_preference_patch(normalized.get("preference_patch"))
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
    edit_markers = ("добав", "встав", "add", "append", "insert", "убер", "удал", "remove", "delete")
    activity_markers = ("activity", "активност", "ноду", "узел", "узл", "node", "check", "проверк", "транзакц", "transaction")
    return any(marker in text for marker in edit_markers) and any(marker in text for marker in activity_markers)


def _has_filled_builder_preferences(preferences: dict | None) -> bool:
    """Return True when Builder UI preferences contain usable build context."""
    if not isinstance(preferences, dict):
        return False
    return any(bool(_stringify_preference_value(value).strip()) for value in preferences.values())


def _looks_like_initial_flow_build(goal: str, preferences: dict | None = None) -> bool:
    """Distinguish an initial campaign/flow build request from a follow-up edit.

    Some initial prompts mention activity names (for example RealTimeCheck) and
    would otherwise be parsed as an add-activity edit.  Without an existing
    session flow, explicit build wording should stay on the normal draft-build
    path so the agent can create a new flow from goal/preferences.
    """
    text = _normalize_text(goal)
    if not text:
        return False

    build_markers = (
        "собер",
        "состав",
        "созда",
        "постро",
        "сгенер",
        "build",
        "create",
        "assemble",
        "generate",
    )
    build_object_markers = (
        "flow",
        "флоу",
        "кампан",
        "campaign",
        "draft",
        "чернов",
    )
    explicit_edit_markers = (
        "добав",
        "встав",
        "add",
        "append",
        "insert",
        "убер",
        "удал",
        "remove",
        "delete",
    )

    has_build_wording = any(marker in text for marker in build_markers)
    if not has_build_wording:
        return False

    # Pure edit commands such as "добавь RealTimeCheck после SMS" must still
    # fail with missing flow when there is no draft to edit.  If the prompt also
    # says to build/create a flow, treat it as an initial build with constraints.
    has_explicit_edit_wording = any(marker in text for marker in explicit_edit_markers)
    has_build_object = any(marker in text for marker in build_object_markers)
    has_preferences = _has_filled_builder_preferences(preferences)
    if has_explicit_edit_wording and not has_build_object:
        return False

    return has_build_object or has_preferences


async def _llm_plan_special_turn(
    goal: str,
    *,
    preferences: dict | None,
    existing_flow: dict | None,
    ref: dict | None = None,
    campaign_brief: dict[str, Any] | None = None,
    audience: dict[str, Any] | None = None,
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
                for offer in _shortlist_offer_templates(ref, goal, preferences, limit=40)
            ]

        response = await llm.ainvoke([
            SystemMessage(content=(
                "Ты планировщик Campaign Builder. Верни только JSON без markdown. "
                "Схема: {\"action\": \"remember_context|add_activity|remove_activity|continue_agent\", "
                "\"activity_type\": string|null, \"anchor_type\": string|null, "
                "\"anchor_id\": string|null, \"anchor_position\": \"before|after|null\", "
                "\"activity_id\": string|null, \"occurrence\": \"first|last|null\", "
                "\"activity_params\": object, \"preference_patch\": object|null, "
                "\"assistant_message\": string}. "
                "preference_patch поддерживает только поля product, goal, channels, "
                "targetGroups, content, offerRecommendations. "
                f"Поддержанные activity_type: {SUPPORTED_ACTIVITY_TYPES_TEXT}. "
                "Если пользователь только просит запомнить вводные — action=remember_context. "
                "Для remember_context извлеки новые вводные в preference_patch. "
                "Если передан structured_audience, используй его как основной источник аудитории; не извлекай аудиторию из длинного текста. "
                "Если selected_target_group_id не null, используй его как существующую Target Group. "
                "Если structured_audience.recommendationOnly=true, не выдумывай target_group_id и попроси подтвердить/создать Target Group. "
                "Если просит добавить активность в текущий flow — action=add_activity; "
                "activity_type заполни точным типом активности, даже если тип не входит в поддержанный список. "
                "Для BusinessTransactionActivity выбери лучший оффер из available_offers и положи "
                "offer_template_id и operation_id в activity_params. "
                "Если пользователь просит удалить активность из текущего flow — action=remove_activity; "
                "верни activity_type для удаления по типу (например BusinessTransactionActivity для транзакции) "
                "или activity_id/anchor_id, если пользователь указал конкретный узел; для 'последней' ставь occurrence=last. "
                "anchor_id — id существующей активности-якоря, если пользователь указал конкретный узел; "
                "иначе anchor_type — тип существующей активности-якоря, anchor_position — before/after/null. "
                "Если это не запоминание и не правка flow, action=continue_agent. "
                "assistant_message всегда по-русски."
            )),
            HumanMessage(content=json.dumps({
                "user_goal": goal,
                "builder_preferences": preferences or {},
                "campaign_brief": campaign_brief or {},
                "structured_audience": audience or {},
                "selected_target_group_id": _selected_target_group_id(audience or {}),
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

    llm_selected_id = params.get("offer_template_id") is not None
    if llm_selected_id:
        try:
            plan_offer_id = int(params["offer_template_id"])
            offer = next((item for item in ref.get("offers", []) if item.get("id") == plan_offer_id), None)
        except (TypeError, ValueError):
            offer = None
        if not offer:
            warning = (
                "LLM-планировщик выбрал невалидный offer_template_id; "
                "пробую подобрать шаблон детерминированно по цели и preferences."
            )

    if not offer:
        offer = _select_offer_template(ref, goal, preferences)
        if offer:
            fallback_note = "использован ближайший шаблон из справочника по score."
            warning = f"{warning} {fallback_note}" if warning else fallback_note

    if not offer:
        no_match_warning = (
            "Не нашёл релевантный шаблон оффера по goal/builder_preferences с достаточным score; "
            "первый шаблон из справочника не подставлен. Укажите offer_template_id и operation_id."
        )
        warning = f"{warning} {no_match_warning}" if warning else no_match_warning
        return None, None, warning

    params["offer_template_id"] = int(offer["id"])
    params["operation_id"] = str(params.get("operation_id") or offer["operationId"])
    params.setdefault("operation_params", [])
    return params, offer, warning


OFFER_SELECTION_MIN_SCORE = 30


def _offer_search_fields(offer: dict) -> tuple[str, str, set[str]]:
    name = _normalize_text(str(offer.get("name", "")))
    operation_id = _normalize_text(str(offer.get("operationId", "")))
    return name, operation_id, _text_tokens(f"{name} {operation_id}")


def _score_offer_text(offer: dict, query: str | None, *, weight: int, exact_product: bool = False) -> int:
    query_norm = _normalize_text(query)
    if not query_norm:
        return 0

    name, operation_id, offer_tokens = _offer_search_fields(offer)
    query_tokens = _text_tokens(query_norm)
    if not query_tokens:
        return 0

    score = 0
    if exact_product and name and query_norm == name:
        score += 120 * weight
    if exact_product and name and (query_norm in name or name in query_norm):
        score += 70 * weight
    if operation_id and query_norm == operation_id:
        score += 55 * weight
    elif operation_id and (query_norm in operation_id or operation_id in query_norm):
        score += 25 * weight

    overlap = query_tokens & offer_tokens
    score += len(overlap) * 12 * weight

    # Give useful product tokens such as Max/Family/тариф a chance to win even
    # when the user phrase and offer name are not exact string matches.
    for token in query_tokens:
        if len(token) < 3:
            continue
        if any(token in offer_token or offer_token in token for offer_token in offer_tokens):
            score += 5 * weight

    return score


def _score_offer_template(offer: dict, goal: str, preferences: dict | None = None) -> int:
    """Score one offer against product, recommendations and goal independently."""
    product = ""
    recommendations = ""
    if isinstance(preferences, dict):
        product = _stringify_preference_value(preferences.get("product"))
        recommendations = _stringify_preference_value(preferences.get("offerRecommendations"))

    return (
        _score_offer_text(offer, product, weight=4, exact_product=True)
        + _score_offer_text(offer, recommendations, weight=2)
        + _score_offer_text(offer, goal, weight=1)
    )


def _rank_offer_templates(ref: dict, goal: str, preferences: dict | None = None) -> list[tuple[int, dict]]:
    ranked = [
        (_score_offer_template(offer, goal, preferences), offer)
        for offer in ref.get("offers", [])
    ]
    return sorted(ranked, key=lambda item: item[0], reverse=True)


def _shortlist_offer_templates(
    ref: dict,
    goal: str,
    preferences: dict | None = None,
    *,
    limit: int = 40,
) -> list[dict]:
    """Return a score-prioritized offer shortlist for LLM planning."""
    ranked = _rank_offer_templates(ref, goal, preferences)
    scored = [offer for score, offer in ranked if score >= OFFER_SELECTION_MIN_SCORE]
    if len(scored) >= limit:
        return scored[:limit]

    seen = {offer.get("id") for offer in scored}
    fallback = [offer for _, offer in ranked if offer.get("id") not in seen]
    return (scored + fallback)[:limit]


def _select_offer_template(ref: dict, goal: str, preferences: dict | None = None) -> dict | None:
    """Select an offer template only when deterministic scoring is confident."""
    ranked = _rank_offer_templates(ref, goal, preferences)
    if not ranked:
        return None

    best_score, best_offer = ranked[0]
    if best_score < OFFER_SELECTION_MIN_SCORE:
        return None

    return best_offer


GENERIC_PRODUCT_TOKENS = {"тариф", "пакет", "данных", "гб", "gb", "offer", "оффер"}


def _offer_matches_product(offer: dict, product: str | None) -> bool:
    product_tokens = _text_tokens(product)
    if not product_tokens:
        return True
    name, operation_id, offer_tokens = _offer_search_fields(offer)
    product_norm = _normalize_text(product)
    if product_norm and (product_norm in name or product_norm in operation_id):
        return True
    important_tokens = {token for token in product_tokens if token not in GENERIC_PRODUCT_TOKENS}
    if important_tokens:
        return bool(important_tokens & offer_tokens)
    return bool(product_tokens & offer_tokens)


def _validate_flow_offer_product_match(
    flow: dict | None,
    ref: dict,
    preferences: dict | None,
) -> str | None:
    """Warn if a built flow uses an offer unrelated to builder_preferences.product."""
    product = ""
    if isinstance(preferences, dict):
        product = _stringify_preference_value(preferences.get("product"))
    if not product or not isinstance(flow, dict):
        return None

    offers_by_id = {offer.get("id"): offer for offer in ref.get("offers", [])}
    for activity in flow.get("activities", []):
        if not isinstance(activity, dict) or activity.get("type") != "BusinessTransactionActivity":
            continue
        offer_id = activity.get("offerTemplateId")
        offer = offers_by_id.get(offer_id)
        if not offer:
            operation_id = (activity.get("businessOperation") or {}).get("id")
            offer = {"id": offer_id, "name": "", "operationId": operation_id or ""}
        if not _offer_matches_product(offer, product):
            return (
                f"Выбранный шаблон оффера id={offer_id} "
                f"(name='{offer.get('name') or 'неизвестно'}', operationId='{offer.get('operationId') or ''}') "
                f"не похож на продукт '{product}'. Уточните offer_template_id или product перед созданием кампании."
            )
    return None


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



def _remove_activity_from_flow(
    flow: dict,
    activity_type: str | None = None,
    activity_id: str | None = None,
    occurrence: str = "last",
) -> dict:
    """Remove an activity from a flow and rebuild links, positions, and generated offers."""
    activities = list(flow.get("activities") or []) if isinstance(flow, dict) else []
    if not activities:
        raise ValueError("flow должен содержать activities[]")
    if occurrence not in {"first", "last"}:
        raise ValueError('occurrence должен быть "first" или "last"')

    remove_index: int | None = None
    if activity_id:
        remove_index = next(
            (
                index
                for index, activity in enumerate(activities)
                if isinstance(activity, dict) and activity.get("id") == activity_id
            ),
            None,
        )
    elif activity_type:
        indexes = [
            index
            for index, activity in enumerate(activities)
            if isinstance(activity, dict) and activity.get("type") == activity_type
        ]
        if indexes:
            remove_index = indexes[0] if occurrence == "first" else indexes[-1]
    else:
        raise ValueError("Нужно указать activity_type или activity_id для удаления")

    if remove_index is None:
        requested = f"id={activity_id}" if activity_id else activity_type
        raise ValueError(f"Не найдена активность для удаления: {requested}")

    activity_to_remove = activities[remove_index]
    if activity_to_remove.get("type") == "CommonActivity" and remove_index == 0:
        raise ValueError("Нельзя удалить корневую обязательную CommonActivity")

    updated_activities = activities[:remove_index] + activities[remove_index + 1:]
    if not updated_activities:
        raise ValueError("Нельзя удалить последнюю активность flow")
    return assemble_flow(updated_activities)

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


class FlowPatchConflictError(ValueError):
    """Raised when a typed flow patch targets a stale draft version."""


def _flow_patch_from_plan(plan: dict[str, Any], base_version: int) -> FlowPatch:
    """Convert parser/LLM planner output into the typed FlowPatch contract."""
    return FlowPatch(
        base_version=base_version,
        operations=[plan.get("action")],
        anchor_activity_id=plan.get("anchor_id"),
        anchor_activity_type=plan.get("anchor_type"),
        insert_position=plan.get("anchor_position") or (
            "after" if plan.get("anchor_type") or plan.get("anchor_id") else "end"
        ),
        activity={
            "type": plan.get("activity_type"),
            "params": dict(plan.get("activity_params") or {}),
            "id": plan.get("activity_id"),
            "occurrence": plan.get("occurrence") or "last",
        },
    )


def _apply_flow_patch(
    flow: dict,
    patch: FlowPatch,
    *,
    current_version: int,
) -> dict:
    """Apply a typed FlowPatch through the existing flow edit helpers.

    The draft is never mutated when the patch was based on a stale version.
    """
    if patch.base_version != current_version:
        raise FlowPatchConflictError(
            f"Flow patch base_version={patch.base_version} does not match "
            f"current draft_flow_version={current_version}"
        )

    updated_flow = flow
    for operation in patch.operations:
        if operation == "add_activity":
            updated_flow = _add_activity_to_flow(
                updated_flow,
                str(patch.activity.type),
                activity_params=patch.activity.params,
                anchor_activity_type=patch.anchor_activity_type,
                anchor_id=patch.anchor_activity_id,
                anchor_position=patch.insert_position,
            )
        elif operation == "remove_activity":
            updated_flow = _remove_activity_from_flow(
                updated_flow,
                activity_type=patch.activity.type,
                activity_id=patch.activity.id or patch.anchor_activity_id,
                occurrence=patch.activity.occurrence,
            )
        else:
            raise ValueError(f"Неподдерживаемая patch operation: {operation}")
    return updated_flow


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
- For a new campaign: use last_flow_json supplied by the deterministic Flow Composer as the canonical base route; do not build the base route with LLM/tool calls. Use validate_flow_tool for draft checks only. Creating a campaign is handled outside this chat path by POST /api/builder/create; start_campaign_tool only if a campaign already exists and the user says to launch.
- For follow-up edits to an existing flow, use session_flow_json and update_existing_flow_with_activity instead of starting from scratch for all supported activity types: PushCommunicationActivity, PullCommunicationActivity, EventActivity, WaitActivity, BusinessTransactionActivity, RealTimeCheckActivity, ResponseActivity, InteractiveResponseActivity, OrJoinActivity.
- For removal follow-up edits, use remove_existing_flow_activity with activity_type or activity_id/anchor_id; for "last transaction" remove the last BusinessTransactionActivity.
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


@tool
async def remove_existing_flow_activity(
    flow_json: str,
    activity_type: str | None = None,
    activity_id: str | None = None,
    anchor_id: str | None = None,
    occurrence: str = "last",
) -> str:
    """Удалить activity из существующего flow по activity_type или activity_id/anchor_id.

    Для «последней транзакции» передайте activity_type=BusinessTransactionActivity
    и occurrence=last. CommonActivity нельзя удалить, если это корневая обязательная нода.
    """
    flow = json.loads(flow_json) if isinstance(flow_json, str) else flow_json
    target_id = activity_id or anchor_id
    updated_flow = _remove_activity_from_flow(
        flow,
        activity_type=activity_type,
        activity_id=target_id,
        occurrence=occurrence or "last",
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
        if tool_name == "remove_existing_flow_activity" and args.get("flow_json"):
            flow_data = json.loads(args["flow_json"]) if isinstance(args["flow_json"], str) else args["flow_json"]
            if isinstance(flow_data, dict) and isinstance(flow_data.get("activities"), list):
                return _remove_activity_from_flow(
                    flow_data,
                    activity_type=args.get("activity_type"),
                    activity_id=args.get("activity_id") or args.get("anchor_id"),
                    occurrence=args.get("occurrence") or "last",
                )
        if tool_name == "validate_flow_tool" and args.get("flow_json"):
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
    # Flow editing tools. Initial base-route generation is handled by
    # agents.flow_composer before the LLM is called.
    update_existing_flow_with_business_transaction,
    update_existing_flow_with_activity,
    remove_existing_flow_activity,
    # API
    validate_flow_tool,
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
    campaign_brief_context = _campaign_brief_context(request)
    brief_completeness = check_campaign_brief_completeness(request)
    structured_audience_context = _structured_audience_context(request)

    if existing_flow and _looks_like_create_or_launch_request(request.goal):
        checklist = build_review_checklist(request.campaign_brief, existing_flow, [])
        if not is_review_allowed_for_runtime(checklist.status, request.review_checklist_acknowledged):
            return _builder_response(
                request,
                message=_blocked_runtime_review_message(checklist.status),
                campaign_id=request.session_campaign_id,
                draft_flow=existing_flow,
                review_checklist=checklist,
                review_status=checklist.status,
                status="needs_review",
            )

    # Context-only turns go through an LLM planner, but never create a campaign.
    if _is_memory_only_request(request.goal):
        plan, plan_warning = await _llm_plan_special_turn(
            request.goal,
            preferences=request.builder_preferences,
            existing_flow=existing_flow,
            campaign_brief=campaign_brief_context,
            audience=structured_audience_context,
        )
        message = (
            plan.get("assistant_message")
            if plan and plan.get("action") == "remember_context" and plan.get("assistant_message")
            else "Запомнил вводные для этой сборки. Когда будете готовы, напишите «собери кампанию» или уточните недостающие параметры."
        )
        preference_patch = _extract_preference_patch_from_text(request.goal)
        if plan and plan.get("action") == "remember_context":
            preference_patch.update(_coerce_preference_patch(plan.get("preference_patch")))
        updated_preferences = _merge_builder_preferences(request.builder_preferences, preference_patch)
        if plan_warning:
            message += f"\n\n⚠️ LLM-планировщик не смог подтвердить шаг ({plan_warning}); контекст сохранён без сборки кампании."
        return _builder_response(
            request,
            message=message,
            builder_preferences=updated_preferences,
            preference_patch=preference_patch,
            campaign_id=request.session_campaign_id,
            draft_flow=existing_flow,
            status=_status_for_flow_context(request.session_campaign_id, existing_flow),
        )

    # ── Pre-fetch reference data in parallel → inject into system prompt ──────
    # This eliminates 4 lookup tool calls per run (~40% fewer LLM calls, ~50% fewer tokens)
    ref_full = await _fetch_reference_data()
    ref, trunc_notes = _cap_builder_reference(ref_full)
    system_prompt = _build_system_prompt(ref, trunc_notes)
    if request.builder_preferences:
        preferences_json = json.dumps(request.builder_preferences, ensure_ascii=False)
        system_prompt += f"\nBuilder UI preferences (use as user-provided constraints): {preferences_json}\n"
    if campaign_brief_context:
        brief_json = json.dumps(campaign_brief_context, ensure_ascii=False)
        system_prompt += f"\nTyped campaign_brief (authoritative structured request context): {brief_json}\n"
    completeness_json = json.dumps(brief_completeness.model_dump(), ensure_ascii=False)
    system_prompt += (
        "\nCampaign brief completeness (UI-safe server validation; do not expose debug output): "
        f"{completeness_json}\n"
    )
    if brief_completeness.assumptions:
        system_prompt += (
            "Apply explicit assumptions unless the user overrides them: "
            + "; ".join(brief_completeness.assumptions)
            + "\n"
        )
    if structured_audience_context:
        audience_json = json.dumps(structured_audience_context, ensure_ascii=False)
        target_group_id = _selected_target_group_id(structured_audience_context)
        system_prompt += (
            "\nStructured Audience Builder selection (primary audience source; do not parse audience from free-form text): "
            f"{audience_json}\n"
        )
        if target_group_id is not None:
            system_prompt += f"Use existing target_group_id={target_group_id} for TargetGroupActivity.\n"
        elif structured_audience_context.get("recommendationOnly"):
            system_prompt += "Audience is recommendation-only; do not invent target_group_id. Ask to confirm/create a Target Group before building with a TargetGroupActivity.\n"
    print(f"[campaign_builder] Ref data (API total → prompt): "
          f"{len(ref_full.get('target_groups', []))}→{len(ref['target_groups'])} TGs, "
          f"{len(ref_full.get('channels', []))}→{len(ref['channels'])} channels, "
          f"{len(ref_full.get('events', []))}→{len(ref['events'])} events, "
          f"{len(ref_full.get('offers', []))}→{len(ref['offers'])} offers")

    initial_build_without_existing_flow = (
        not existing_flow
        and _looks_like_initial_flow_build(request.goal, request.builder_preferences)
    )
    deterministic_initial_flow: dict[str, Any] | None = None
    if initial_build_without_existing_flow:
        # Keep initial build requests on the normal draft-flow path even when
        # they mention an activity type that the deterministic edit parser knows
        # about (for example RealTimeCheck). Missing-flow errors are reserved for
        # follow-up edit commands that require an existing draft.
        flow_edit_intent = None
        if request.campaign_brief is not None:
            composition = compose_campaign_flow_result(request.campaign_brief)
            deterministic_initial_flow = composition.flow
            system_prompt += (
                "\nDeterministic Flow Composer already supplied the canonical base route in "
                "last_flow_json. Do not call build_*_flow tools or change the base route. "
                "Use the LLM only to choose message/template variants and wording; keep "
                "Start/Common, AudienceFilter, ConsentCheck, channel, Wait and "
                "Response/ActivationCheck routing intact. "
                f"Composer validation metadata: {json.dumps(composition.validation_metadata, ensure_ascii=False)}\n"
            )

    planned_flow_patch: FlowPatch | None = None
    planned_flow_plan: dict[str, Any] | None = None
    planned_flow_patch_warning: str | None = None
    patch_base_version = _current_draft_flow_version(request)

    if flow_edit_intent and flow_edit_intent.action in {"add_activity", "remove_activity"}:
        planned_flow_plan = {
            "action": flow_edit_intent.action,
            "activity_type": flow_edit_intent.activity_type,
            "anchor_type": flow_edit_intent.anchor_activity_type,
            "anchor_id": flow_edit_intent.anchor_id,
            "anchor_position": flow_edit_intent.anchor_position or ("after" if flow_edit_intent.anchor_activity_type else None),
            "activity_id": flow_edit_intent.activity_id,
            "occurrence": flow_edit_intent.occurrence,
            "activity_params": {},
            "assistant_message": "",
        }
        if existing_flow and flow_edit_intent.action == "add_activity" and flow_edit_intent.activity_type == "BusinessTransactionActivity":
            planner_plan, planned_flow_patch_warning = await _llm_plan_special_turn(
                request.goal,
                preferences=request.builder_preferences,
                existing_flow=existing_flow,
                ref=ref_full,
                campaign_brief=campaign_brief_context,
                audience=structured_audience_context,
            )
            if planner_plan and planner_plan.get("action") == "add_activity":
                planner_plan.setdefault("activity_type", "BusinessTransactionActivity")
                if not planner_plan.get("anchor_type"):
                    planner_plan["anchor_type"] = flow_edit_intent.anchor_activity_type
                if not planner_plan.get("anchor_position") and flow_edit_intent.anchor_activity_type:
                    planner_plan["anchor_position"] = "after"
                planned_flow_plan = planner_plan
    elif existing_flow and _looks_like_flow_activity_edit(request.goal):
        planned_flow_plan, planned_flow_patch_warning = await _llm_plan_special_turn(
            request.goal,
            preferences=request.builder_preferences,
            existing_flow=existing_flow,
            ref=ref_full,
            campaign_brief=campaign_brief_context,
            audience=structured_audience_context,
        )
        if planned_flow_plan and planned_flow_plan.get("action") not in {"add_activity", "remove_activity"}:
            planned_flow_plan = None

    if planned_flow_plan:
        planned_flow_patch = _flow_patch_from_plan(planned_flow_plan, patch_base_version)

    if planned_flow_patch and "remove_activity" in planned_flow_patch.operations:
        if not existing_flow:
            return _builder_response(
                request,
                message=(
                    "Не нашёл текущий flow для доработки. Сначала соберите кампанию, "
                    "затем повторите запрос на удаление активности."
                ),
                campaign_id=request.session_campaign_id,
                draft_flow=None,
                status="error",
            )

        try:
            updated_flow = _apply_flow_patch(
                existing_flow,
                planned_flow_patch,
                current_version=_current_draft_flow_version(request),
            )
        except FlowPatchConflictError as e:
            return _builder_response(
                request,
                message=f"Конфликт версии flow: {e}. Обновите черновик и повторите действие.",
                campaign_id=request.session_campaign_id,
                draft_flow=existing_flow,
                draft_flow_version=_current_draft_flow_version(request) or None,
                status="error",
            )
        except Exception as e:
            return _builder_response(
                request,
                message=f"Не удалось удалить активность из текущего flow: {type(e).__name__}: {e}",
                campaign_id=request.session_campaign_id,
                draft_flow=existing_flow,
                status="error",
            )

        activity_id = planned_flow_patch.activity.id or planned_flow_patch.anchor_activity_id
        activity_type = planned_flow_patch.activity.type
        target_text = f" с id={activity_id}" if activity_id else f" {activity_type}" if activity_type else ""
        message = (planned_flow_plan or {}).get("assistant_message") or f"Удалил{target_text} из flow и пересобрал связи."
        if planned_flow_patch_warning:
            message += f"\n\n⚠️ {planned_flow_patch_warning}"
        return _builder_response(
            request,
            message=message,
            campaign_id=request.session_campaign_id,
            draft_flow=updated_flow,
            draft_flow_version=_next_draft_flow_version(request),
            status=_status_for_flow_context(request.session_campaign_id, updated_flow),
        )

    if planned_flow_patch and "add_activity" in planned_flow_patch.operations:
        activity_type = planned_flow_patch.activity.type
        if not _is_supported_activity_type(activity_type):
            return _builder_response(
                request,
                message=_unsupported_activity_message(activity_type),
                campaign_id=request.session_campaign_id,
                draft_flow=existing_flow,
                status="error",
            )

        if not existing_flow:
            return _builder_response(
                request,
                message=(
                    "Не нашёл текущий flow для доработки. Сначала соберите кампанию, "
                    "затем повторите запрос на добавление активности."
                ),
                campaign_id=request.session_campaign_id,
                draft_flow=None,
                status="error",
            )

        activity_params = dict(planned_flow_patch.activity.params or {})
        offer = None
        if activity_type == "BusinessTransactionActivity":
            resolved_params, offer, fallback_warning = _resolve_business_transaction_activity_params(
                activity_params,
                ref_full,
                request.goal,
                request.builder_preferences,
            )
            if fallback_warning and not planned_flow_patch_warning:
                planned_flow_patch_warning = fallback_warning
            if not resolved_params:
                details = fallback_warning or (
                    "справочник офферов пуст или недоступен. "
                    "Укажите offer_template_id и operation_id или проверьте доступ к AdTarget API."
                )
                return _builder_response(
                    request,
                    message=f"Не удалось выбрать шаблон оффера для бизнес-транзакции: {details}",
                    campaign_id=request.session_campaign_id,
                    draft_flow=existing_flow,
                    status="error",
                )
            activity_params = resolved_params
            planned_flow_patch = planned_flow_patch.model_copy(update={
                "activity": planned_flow_patch.activity.model_copy(update={"params": activity_params}),
            })

        try:
            updated_flow = _apply_flow_patch(
                existing_flow,
                planned_flow_patch,
                current_version=_current_draft_flow_version(request),
            )
        except FlowPatchConflictError as e:
            return _builder_response(
                request,
                message=f"Конфликт версии flow: {e}. Обновите черновик и повторите действие.",
                campaign_id=request.session_campaign_id,
                draft_flow=existing_flow,
                draft_flow_version=_current_draft_flow_version(request) or None,
                status="error",
            )
        except Exception as e:
            return _builder_response(
                request,
                message=f"Не удалось добавить активность в текущий flow: {type(e).__name__}: {e}",
                campaign_id=request.session_campaign_id,
                draft_flow=existing_flow,
                status="error",
            )

        anchor_type = planned_flow_patch.anchor_activity_type
        anchor_position = planned_flow_patch.insert_position or "after"
        if anchor_type:
            anchor_text = f" {anchor_position} {anchor_type}"
        else:
            anchor_text = " в конец flow"
        message = (planned_flow_plan or {}).get("assistant_message") or f"Добавил {activity_type}{anchor_text}."
        if offer:
            message += (
                f"\n\nШаблон оффера: **{offer.get('name', offer['id'])}** "
                f"(id={offer['id']}, operationId={activity_params['operation_id']})."
            )
        if planned_flow_patch_warning:
            message += f"\n\n⚠️ {planned_flow_patch_warning}"

        return _builder_response(
            request,
            message=message,
            campaign_id=request.session_campaign_id,
            draft_flow=updated_flow,
            draft_flow_version=_next_draft_flow_version(request),
            status=_status_for_flow_context(request.session_campaign_id, updated_flow),
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
    if deterministic_initial_flow is not None:
        initial_flow_json = json.dumps(deterministic_initial_flow, ensure_ascii=False)

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
        return _builder_response(
            request,
            message=(
                "Не удалось завершить шаг через LLM: "
                f"{type(e).__name__}: {e}. "
                "Риск: текущая модель или tool-call могли вернуть некорректный JSON/пустой ответ; "
                "попробуйте повторить запрос или уточнить параметры."
            ),
            campaign_id=initial_campaign_id,
            draft_flow=deterministic_initial_flow or existing_flow,
            status="error",
        )

    last_message = result["messages"][-1]
    answer_text = last_message.content if hasattr(last_message, "content") else str(last_message)
    campaign_id = result.get("campaign_id")
    last_flow_json = result.get("last_flow_json")
    if not last_flow_json and deterministic_initial_flow is not None:
        last_flow_json = json.dumps(deterministic_initial_flow, ensure_ascii=False)

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
    # the prototype has no draft_flow to render. Recover the richest flow and
    # return it as a draft for explicit user review/confirmation.
    recovered_from_text = False
    if not last_flow_json and isinstance(answer_text, str) and "</function>" in answer_text:
        recovered_flow = await _recover_flow_from_textual_tool_calls(answer_text)
        if recovered_flow:
            last_flow_json = json.dumps(recovered_flow, ensure_ascii=False)
            recovered_from_text = True
            print("[campaign_builder] Recovered flow from textual tool calls")

    flow_product_warning = None
    flow_data_for_validation = None
    if last_flow_json:
        try:
            flow_data_for_validation = json.loads(last_flow_json)
            flow_product_warning = _validate_flow_offer_product_match(
                flow_data_for_validation,
                ref_full,
                request.builder_preferences,
            )
        except (json.JSONDecodeError, TypeError):
            flow_data_for_validation = None

    if flow_product_warning and not campaign_id:
        return _builder_response(
            request,
            message=f"⚠️ {flow_product_warning}",
            campaign_id=None,
            draft_flow=flow_data_for_validation,
            status="error",
        )

    # ── Draft-only: flow собран, но create_campaign должен быть явным ─────────
    draft_only = bool(last_flow_json and not campaign_id)

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
        status = "running"
    elif campaign_id:
        status = "created_in_adtarget"
    elif draft_only or recovered_from_text or draft_flow is not None:
        status = "draft_ready"
    else:
        status = "collect_brief"

    # ── Финализируем сообщение ────────────────────────────────────────────────
    # Если draft_only/recovered_from_text — заменяем служебный вывод агента на
    # чёткое подтверждение. Это скрывает raw <tool>{...}</function> из чата.
    if flow_product_warning and campaign_id:
        answer_text = (
            f"⚠️ {flow_product_warning}\n\n"
            "Кампания уже могла быть создана tool-вызовом до серверной проверки; проверьте её перед запуском."
        )

    if draft_only or recovered_from_text:
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
                f"Draft кампании{flow_name} собран и готов к review. "
                "Создание в AdTarget требует отдельного подтверждения."
            )

    return _builder_response(
        request,
        message=answer_text,
        campaign_id=campaign_id,
        draft_flow=draft_flow,
        draft_flow_version=(
            _next_draft_flow_version(request)
            if draft_flow is not None and draft_flow != existing_flow
            else (_current_draft_flow_version(request) or (1 if draft_flow is not None else None))
        ),
        status=status,
    )
