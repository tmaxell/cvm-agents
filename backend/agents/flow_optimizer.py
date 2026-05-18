"""Deterministic pre-launch optimizer for Campaign Builder draft flows."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from schemas import CampaignBrief, FlowPatch, ReviewChecklist
from agents.safety_review import build_review_checklist
from agents.campaign_builder import (
    _apply_flow_patch,
    _fetch_reference_data,
    _resolve_business_transaction_activity_params,
)


def _brief_preferences(brief: CampaignBrief | None) -> dict[str, Any]:
    return brief.to_builder_preferences() if brief is not None else {}


def _activities(flow: dict[str, Any] | None) -> list[dict[str, Any]]:
    activities = flow.get("activities") if isinstance(flow, dict) else None
    return [item for item in activities if isinstance(item, dict)] if isinstance(activities, list) else []


def _has_activity(flow: dict[str, Any], activity_type: str) -> bool:
    return any(activity.get("type") == activity_type for activity in _activities(flow))


def _item_status(checklist: ReviewChecklist, category: str) -> str | None:
    for item in checklist.items:
        if item.category == category:
            return item.status
    return None


def _selected_target_group_id(brief: CampaignBrief | None) -> int | None:
    selected = getattr(getattr(brief, "audience", None), "selected_segment", None)
    if not selected or getattr(selected, "recommendationOnly", False):
        return None
    matched = getattr(selected, "matched_target_group", None)
    if not matched:
        return None
    raw_id = getattr(matched, "target_group_id", None) or getattr(matched, "id", None)
    try:
        return int(raw_id) if raw_id is not None and str(raw_id).strip() else None
    except (TypeError, ValueError):
        return None


def _target_group_id_from_brief_or_ref(brief: CampaignBrief | None, ref: dict[str, Any]) -> int | None:
    selected_id = _selected_target_group_id(brief)
    if selected_id is not None:
        return selected_id

    audience = getattr(brief, "audience", None)
    candidates = list(getattr(audience, "target_groups", None) or [])
    if getattr(audience, "description", None):
        candidates.append(str(audience.description))

    for candidate in candidates:
        text = str(candidate).strip()
        if not text:
            continue
        if text.isdigit():
            return int(text)
        for target_group in ref.get("target_groups", []):
            name = str(target_group.get("name") or "").strip().lower()
            if name and (text.lower() == name or text.lower() in name or name in text.lower()):
                try:
                    return int(target_group["id"])
                except (KeyError, TypeError, ValueError):
                    continue
    return None


def _content_type_for_channel_name(name: str) -> str:
    normalized = name.strip().lower()
    if "email" in normalized or "mail" in normalized:
        return "EmailContent"
    if "ussd" in normalized:
        return "UssdContent"
    return "SmsContent"


def _channel_params_from_brief_or_ref(brief: CampaignBrief | None, ref: dict[str, Any]) -> dict[str, Any] | None:
    channels = getattr(brief, "channels", None) or []
    for channel in channels:
        channel_id = getattr(channel, "channel_id", None)
        content_type = getattr(channel, "content_type", None) or _content_type_for_channel_name(getattr(channel, "name", ""))
        if channel_id is not None:
            return {"channel_id": int(channel_id), "content_type": content_type}
        name = str(getattr(channel, "name", "") or "").lower()
        for ref_channel in ref.get("channels", []):
            ref_name = str(ref_channel.get("name") or "").lower()
            ref_type = str(ref_channel.get("contentType") or "")
            if (name and name in ref_name) or (content_type and ref_type == content_type):
                return {"channel_id": int(ref_channel["id"]), "content_type": ref_type or content_type}

    for preferred_type in ("SmsContent", "CustomContent", "EmailContent"):
        for ref_channel in ref.get("channels", []):
            if ref_channel.get("contentType") == preferred_type:
                return {"channel_id": int(ref_channel["id"]), "content_type": preferred_type}
    return None


def _brief_content(brief: CampaignBrief | None) -> str | None:
    content = getattr(getattr(brief, "constraints", None), "content", None)
    text = str(content or "").strip()
    return text or None


def _has_offer_context(brief: CampaignBrief | None) -> bool:
    constraints = getattr(brief, "constraints", None)
    return bool(
        str(getattr(brief, "product", None) or "").strip()
        or str(getattr(constraints, "offer_recommendations", None) or "").strip()
    )


def _flow_has_message_content(flow: dict[str, Any]) -> bool:
    for activity in _activities(flow):
        if activity.get("type") not in {"PushCommunicationActivity", "PullCommunicationActivity"}:
            continue
        content = activity.get("content")
        parameters = content.get("parameters") if isinstance(content, dict) else None
        if any(str(parameter.get("value") or "").strip() for parameter in parameters or [] if isinstance(parameter, dict)):
            return True
    return False


def _set_first_message_content(flow: dict[str, Any], message_text: str) -> bool:
    for activity in _activities(flow):
        if activity.get("type") not in {"PushCommunicationActivity", "PullCommunicationActivity"}:
            continue
        content = activity.setdefault("content", {"type": activity.get("contentType") or "SmsContent", "parameters": []})
        parameters = content.setdefault("parameters", [])
        if not isinstance(parameters, list):
            content["parameters"] = parameters = []
        for parameter in parameters:
            if isinstance(parameter, dict) and parameter.get("name") == "Text":
                if not str(parameter.get("value") or "").strip():
                    parameter["value"] = message_text
                    return True
                return False
        parameters.append({
            "type": "StringContentParameterValue",
            "name": "Text",
            "value": message_text,
            "valueExpression": None,
            "isPriority": False,
            "targetType": "String",
        })
        return True
    return False


def _mark_contact_policy(flow: dict[str, Any]) -> bool:
    activities = _activities(flow)
    if not activities:
        return False
    common = activities[0]
    if common.get("type") != "CommonActivity":
        return False
    settings = common.setdefault("settings", {})
    changed = False
    if settings.get("useContactPolicies") is not True:
        settings["useContactPolicies"] = True
        changed = True
    if settings.get("communicationLimit") is None:
        settings["communicationLimit"] = {"periodInDays": 7, "limit": 1, "source": "builder_optimizer"}
        changed = True
    return changed


def _apply_add_activity(
    flow: dict[str, Any],
    *,
    current_version: int,
    activity_type: str,
    params: dict[str, Any],
    anchor_type: str | None = None,
    position: str = "after",
) -> dict[str, Any]:
    patch = FlowPatch(
        base_version=current_version,
        operations=["add_activity"],
        anchor_activity_type=anchor_type,
        insert_position=position,
        activity={"type": activity_type, "params": params},
    )
    return _apply_flow_patch(flow, patch, current_version=current_version)


async def optimize_draft_flow(
    *,
    draft_flow: dict[str, Any],
    campaign_brief: CampaignBrief | None,
    draft_flow_version: int,
    validation_errors: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], int, ReviewChecklist, list[str], list[str]]:
    """Return optimized flow, next version, checklist, additions and remaining recommendations."""
    flow = deepcopy(draft_flow)
    ref = await _fetch_reference_data()
    version = draft_flow_version
    additions: list[str] = []
    skipped: list[str] = []

    checklist = build_review_checklist(campaign_brief, flow, validation_errors or [])

    if _item_status(checklist, "audience") != "green" and not _has_activity(flow, "TargetGroupActivity"):
        target_group_id = _target_group_id_from_brief_or_ref(campaign_brief, ref)
        if target_group_id is not None:
            flow = _apply_add_activity(
                flow,
                current_version=version,
                activity_type="TargetGroupActivity",
                params={"target_group_id": target_group_id},
                anchor_type="CommonActivity",
            )
            version += 1
            additions.append(f"TargetGroupActivity для Target Group #{target_group_id}")
        else:
            skipped.append("TargetGroupActivity: не удалось определить существующую Target Group из brief/ref data")

    checklist = build_review_checklist(campaign_brief, flow, validation_errors or [])
    if _item_status(checklist, "consent") != "green":
        flow = _apply_add_activity(
            flow,
            current_version=version,
            activity_type="RealTimeCheckActivity",
            params={"filters": [{"type": "ConsentOptIn", "source": "builder_optimizer"}]},
            anchor_type="TargetGroupActivity" if _has_activity(flow, "TargetGroupActivity") else "CommonActivity",
        )
        # The review heuristic recognizes consent/opt-in by activity name/type/id.
        for activity in _activities(flow):
            if (
                activity.get("type") == "RealTimeCheckActivity"
                and any(isinstance(item, dict) and item.get("type") == "ConsentOptIn" for item in activity.get("filters") or [])
            ):
                activity["name"] = "Consent opt-in gate"
                break
        version += 1
        additions.append("consent/opt-in gate")

    checklist = build_review_checklist(campaign_brief, flow, validation_errors or [])
    if not _has_activity(flow, "BusinessTransactionActivity") and (
        _item_status(checklist, "offer") != "green" or _has_offer_context(campaign_brief)
    ):
        goal = campaign_brief.goal if campaign_brief and campaign_brief.goal else ""
        params, offer, warning = _resolve_business_transaction_activity_params(
            {},
            ref,
            goal,
            _brief_preferences(campaign_brief),
        )
        if params and offer:
            flow = _apply_add_activity(
                flow,
                current_version=version,
                activity_type="BusinessTransactionActivity",
                params=params,
                anchor_type="PushCommunicationActivity" if _has_activity(flow, "PushCommunicationActivity") else None,
                position="after" if _has_activity(flow, "PushCommunicationActivity") else "end",
            )
            version += 1
            additions.append(f"BusinessTransactionActivity для оффера #{offer.get('id')}")
            if warning:
                skipped.append(f"Оффер подобран эвристически: {warning}")
        else:
            skipped.append("BusinessTransactionActivity: не найден релевантный offer template в ref data")

    checklist = build_review_checklist(campaign_brief, flow, validation_errors or [])
    if _item_status(checklist, "content") != "green" or not _flow_has_message_content(flow):
        content = _brief_content(campaign_brief)
        if content and _set_first_message_content(flow, content):
            version += 1
            additions.append("базовый message content из brief")
        elif content:
            channel_params = _channel_params_from_brief_or_ref(campaign_brief, ref)
            if channel_params:
                flow = _apply_add_activity(
                    flow,
                    current_version=version,
                    activity_type="PushCommunicationActivity",
                    params={**channel_params, "message_text": content},
                    anchor_type="RealTimeCheckActivity" if _has_activity(flow, "RealTimeCheckActivity") else "TargetGroupActivity",
                )
                version += 1
                additions.append("PushCommunicationActivity с базовым message content")
            else:
                skipped.append("message content: не найден канал коммуникации в brief/ref data")
        elif _mark_contact_policy(flow):
            version += 1
            additions.append("contact policy/frequency marker")
        else:
            skipped.append("message content: в brief нет текста сообщения")

    checklist = build_review_checklist(campaign_brief, flow, validation_errors or [])
    if _item_status(checklist, "contact_policy") != "green" and _mark_contact_policy(flow):
        version += 1
        additions.append("contact policy/frequency marker")
        checklist = build_review_checklist(campaign_brief, flow, validation_errors or [])

    remaining = [item.message for item in checklist.items if item.status != "green"] + skipped
    return flow, version, checklist, additions, list(dict.fromkeys(remaining))
