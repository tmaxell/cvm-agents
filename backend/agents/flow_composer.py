"""Deterministic Campaign Flow Composer.

Builds the canonical first draft of a campaign flow from a typed CampaignBrief
without LLM, network calls, or AdTarget reference lookups. The LLM may enrich the
result later with template variants and final message copy, but the base route is
owned by this module.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from typing import Any

from schemas import CampaignBrief, CampaignChannel
from tools.flow_builder import (
    assemble_flow,
    make_common_activity,
    make_push_communication_activity,
    make_real_time_check_activity,
    make_response_activity,
    make_target_group_activity,
    make_wait_activity,
)

_CONTENT_TYPE_BY_HINT = {
    "sms": "SmsContent",
    "смс": "SmsContent",
    "push": "CustomContent",
    "пуш": "CustomContent",
    "email": "EmailContent",
    "e-mail": "EmailContent",
    "mail": "EmailContent",
    "почт": "EmailContent",
}

_DEFAULT_CHANNEL_IDS = {
    "SmsContent": 1,
    "CustomContent": 2,
    "EmailContent": 3,
}

_DETERMINISTIC_BEGIN_DATE = "2026-05-18T00:00:00+05:00"
_DETERMINISTIC_END_DATE = "2026-06-17T00:00:00+05:00"


@dataclass(frozen=True)
class FlowCompositionResult:
    """Result of deterministic flow composition."""

    flow: dict[str, Any]
    validation_metadata: dict[str, Any]


def compose_campaign_flow(brief: CampaignBrief) -> dict[str, Any]:
    """Return canonical deterministic flow JSON for ``CampaignBrief``.

    The flow always contains the baseline route:
    Common/Start → AudienceFilter → ConsentCheck → channel communication(s)
    → Wait → Response or ActivationCheck.
    """
    return compose_campaign_flow_result(brief).flow


def compose_campaign_flow_result(brief: CampaignBrief) -> FlowCompositionResult:
    """Compose flow plus validation metadata without LLM/network calls."""
    fingerprint = _brief_fingerprint(brief)
    warnings: list[dict[str, Any]] = []
    assumptions: list[str] = []

    campaign_name = _campaign_name(brief)
    target_group_id = _target_group_id(brief)
    if target_group_id is None:
        target_group_id = 0
        warnings.append({
            "code": "missing_target_group_id",
            "message": "AudienceFilter uses clientSourceId=0 until an existing Target Group is selected.",
            "activity": "AudienceFilter",
        })

    channels = _selected_channels(brief)
    if not channels:
        assumptions.append("channels: SMS + Push")
        channels = [
            CampaignChannel(name="SMS", channel_id=_DEFAULT_CHANNEL_IDS["SmsContent"], content_type="SmsContent"),
            CampaignChannel(name="Push", channel_id=_DEFAULT_CHANNEL_IDS["CustomContent"], content_type="CustomContent"),
        ]

    message_text = _message_text(brief)
    activities: list[dict[str, Any]] = [
        make_common_activity(
            campaign_name,
            begin_date=_DETERMINISTIC_BEGIN_DATE,
            end_date=_DETERMINISTIC_END_DATE,
        ),
        make_target_group_activity(target_group_id),
        _make_consent_check_activity(),
    ]

    for channel in channels:
        content_type = _content_type(channel)
        channel_id = channel.channel_id or _DEFAULT_CHANNEL_IDS.get(content_type, 0)
        if channel.channel_id is None:
            assumptions.append(f"channel_id for {content_type}: {channel_id}")
        activities.append(
            make_push_communication_activity(
                channel_id,
                content_type,
                message_text,
            )
        )

    activities.append(make_wait_activity(_wait_days(brief)))
    if _needs_activation_check(brief):
        activities.append(_make_activation_check_activity())
    else:
        activities.append(make_response_activity(_response_code(brief)))

    flow = assemble_flow(activities)
    _canonicalize_activities(flow["activities"], fingerprint)

    validation_metadata = {
        "composer": "FlowComposer",
        "version": 1,
        "deterministic": True,
        "briefFingerprint": fingerprint,
        "warnings": warnings,
        "assumptions": _dedupe(assumptions),
        "checks": [
            "CommonActivity is first and has a schedule.",
            "AudienceFilter precedes consent and communications.",
            "ConsentCheck precedes all outbound channel activities.",
            "Wait precedes terminal Response/ActivationCheck.",
        ],
    }
    flow["validation"] = validation_metadata
    flow["metadata"] = {
        "source": "deterministic_flow_composer",
        "llmResponsibilities": ["template_variant", "message_text"],
    }
    # Rebuild UI offers after canonical IDs are assigned.
    flow["offers"] = _generated_offers(flow["activities"])
    return FlowCompositionResult(flow=flow, validation_metadata=validation_metadata)


def _brief_fingerprint(brief: CampaignBrief) -> str:
    payload = brief.model_dump(mode="json", exclude_none=True)
    encoded = repr(_sort_json_like(payload)).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _sort_json_like(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _sort_json_like(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_sort_json_like(item) for item in value]
    return value


def _campaign_name(brief: CampaignBrief) -> str:
    parts = [brief.goal, brief.product]
    name = " — ".join(part.strip() for part in parts if part and part.strip())
    return name or "Deterministic campaign draft"


def _target_group_id(brief: CampaignBrief) -> int | None:
    selected = getattr(brief.audience, "selected_segment", None)
    if selected and selected.is_existing_target_group and not selected.recommendationOnly:
        match = selected.matched_target_group
        if match:
            raw_id = match.target_group_id or match.id
            parsed = _parse_int(raw_id)
            if parsed is not None:
                return parsed
    text = " ".join([
        brief.audience.description or "",
        " ".join(brief.audience.target_groups or []),
    ])
    return _parse_target_group_id(text)


def _parse_target_group_id(text: str) -> int | None:
    patterns = [r"(?:target\s*group|tg|цг|#)\D{0,8}(\d+)", r"\b(\d{2,})\b"]
    for pattern in patterns:
        match = re.search(pattern, text or "", flags=re.IGNORECASE)
        if match:
            return _parse_int(match.group(1))
    return None


def _parse_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _selected_channels(brief: CampaignBrief) -> list[CampaignChannel]:
    channels: list[CampaignChannel] = []
    seen: set[str] = set()
    for channel in brief.channels:
        content_type = _content_type(channel)
        if content_type in seen:
            continue
        seen.add(content_type)
        channels.append(channel)
    return channels


def _content_type(channel: CampaignChannel) -> str:
    if channel.content_type:
        return channel.content_type
    name = (channel.name or "").strip().lower()
    for hint, content_type in _CONTENT_TYPE_BY_HINT.items():
        if hint in name:
            return content_type
    return "SmsContent"


def _message_text(brief: CampaignBrief) -> str:
    if brief.constraints.content:
        return brief.constraints.content.strip()
    product = (brief.product or "предложение").strip()
    return f"Для вас доступно персональное предложение: {product}. Подробности в личном кабинете."


def _wait_days(brief: CampaignBrief) -> int:
    source = " ".join([
        brief.goal or "",
        brief.constraints.content or "",
        brief.constraints.offer_recommendations or "",
    ])
    match = re.search(r"(\d+)\s*(?:дн|day)", source, flags=re.IGNORECASE)
    if match:
        return max(1, min(30, int(match.group(1))))
    return 3


def _needs_activation_check(brief: CampaignBrief) -> bool:
    text = " ".join([
        brief.goal or "",
        brief.product or "",
        brief.constraints.offer_recommendations or "",
    ]).lower()
    markers = ("активац", "activate", "activation", "подключ", "offer", "оффер", "промо", "скид")
    return any(marker in text for marker in markers)


def _response_code(brief: CampaignBrief) -> str:
    channel_types = {_content_type(channel) for channel in brief.channels}
    if "EmailContent" in channel_types:
        return "EmailReply"
    if "SmsContent" in channel_types:
        return "SmsReply"
    if "CustomContent" in channel_types:
        return "PushOpen"
    return "Response"


def _make_consent_check_activity() -> dict[str, Any]:
    activity = make_real_time_check_activity(filters=[{
        "type": "ConsentCheck",
        "description": "Client has opt-in for selected outbound channel and is not in opt-out list.",
    }])
    activity["name"] = "Consent check"
    activity["composerRole"] = "ConsentCheck"
    return activity


def _make_activation_check_activity() -> dict[str, Any]:
    activity = make_real_time_check_activity(filters=[{
        "type": "ActivationCheck",
        "description": "Client activated or became eligible for the promoted offer/product.",
    }])
    activity["name"] = "Activation check"
    activity["composerRole"] = "ActivationCheck"
    return activity


def _canonicalize_activities(activities: list[dict[str, Any]], fingerprint: str) -> None:
    for index, activity in enumerate(activities):
        role = activity.get("composerRole") or activity.get("type") or "Activity"
        new_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"cvm-flow:{fingerprint}:{index}:{role}"))
        activity["id"] = new_id
        if activity.get("type") == "TargetGroupActivity":
            activity["name"] = "Audience filter"
            activity["composerRole"] = "AudienceFilter"
        elif activity.get("type") == "CommonActivity":
            activity["composerRole"] = "Start/Common"
        elif activity.get("type") == "PushCommunicationActivity":
            activity["composerRole"] = _channel_role(activity.get("contentType"))
        elif activity.get("type") == "WaitActivity":
            activity["composerRole"] = "Wait"
        elif activity.get("type") == "ResponseActivity":
            activity["composerRole"] = "Response"

    for index, activity in enumerate(activities):
        activity["nextActivityId"] = activities[index + 1]["id"] if index + 1 < len(activities) else None
        activity["position"] = {"left": 120, "top": 38 + index * 112}


def _channel_role(content_type: str | None) -> str:
    if content_type == "EmailContent":
        return "Email"
    if content_type == "CustomContent":
        return "Push"
    return "SMS"


def _generated_offers(activities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    offers: list[dict[str, Any]] = []
    for activity in activities:
        if activity.get("type") != "PushCommunicationActivity":
            continue
        text = None
        for parameter in activity.get("content", {}).get("parameters", []):
            if parameter.get("name") == "Text":
                text = parameter.get("value")
                break
        offers.append({
            "id": f"offer-{activity['id']}",
            "activityId": activity["id"],
            "channelId": activity.get("channelId"),
            "contentType": activity.get("contentType"),
            "text": text,
            "templateVariant": None,
            "source": "deterministic_flow_composer",
        })
    return offers


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result
