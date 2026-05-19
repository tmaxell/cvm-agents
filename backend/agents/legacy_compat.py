"""Legacy compatibility helpers.

Temporary module to isolate deprecated payload converters for one-shot removal.
"""

from __future__ import annotations

from tools.flow_builder import (
    assemble_flow,
    make_common_activity,
    make_push_communication_activity,
    make_target_group_activity,
)


def flow_from_legacy_steps(data: dict) -> dict | None:
    """Convert old prototype ``{name, steps[]}`` flow JSON to activities[]."""
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
