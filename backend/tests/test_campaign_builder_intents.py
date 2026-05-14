import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from agents.campaign_builder import _normalize_special_turn_plan, _parse_flow_edit_intent


def test_add_transaction_intent_targets_business_transaction():
    intent = _parse_flow_edit_intent("Добавь транзакцию")

    assert intent is not None
    assert intent.action == "add_activity"
    assert intent.activity_type == "BusinessTransactionActivity"
    assert intent.anchor_activity_type is None


def test_realtime_check_after_transaction_uses_transaction_as_anchor():
    intent = _parse_flow_edit_intent("Добавь после транзакции реал-тайм проверку")

    assert intent is not None
    assert intent.action == "add_activity"
    assert intent.activity_type == "RealTimeCheckActivity"
    assert intent.anchor_activity_type == "BusinessTransactionActivity"


def test_realtime_check_without_add_marker_still_targets_realtime_activity():
    intent = _parse_flow_edit_intent("нет, real-time check должно добавится")

    assert intent is not None
    assert intent.action == "add_activity"
    assert intent.activity_type == "RealTimeCheckActivity"
    assert intent.anchor_activity_type is None


def test_update_existing_flow_with_activity_inserts_response_after_anchor():
    import asyncio
    import json

    from agents.campaign_builder import update_existing_flow_with_activity
    from tools.flow_builder import (
        assemble_flow,
        make_common_activity,
        make_push_communication_activity,
        make_target_group_activity,
    )

    flow = assemble_flow([
        make_common_activity("Generic edit"),
        make_target_group_activity(101),
        make_push_communication_activity(201, "EmailContent", "Ответьте на письмо."),
    ])

    result_json = asyncio.run(update_existing_flow_with_activity.ainvoke({
        "flow_json": json.dumps(flow, ensure_ascii=False),
        "activity_type": "ResponseActivity",
        "activity_params": {"response_code": "EmailReply"},
        "anchor_type": "PushCommunicationActivity",
        "position": "after",
    }))
    result = json.loads(result_json)

    assert [activity["type"] for activity in result["activities"]] == [
        "CommonActivity",
        "TargetGroupActivity",
        "PushCommunicationActivity",
        "ResponseActivity",
    ]
    assert result["activities"][3]["responseCode"] == "EmailReply"
    assert result["activities"][2]["nextActivityId"] == result["activities"][3]["id"]


def test_legacy_planner_action_maps_to_add_activity_schema():
    plan = _normalize_special_turn_plan({
        "action": "add_business_transaction",
        "offer_template_id": 123,
        "operation_id": "ActivateOffer",
        "assistant_message": "Добавлю транзакцию.",
    })

    assert plan["action"] == "add_activity"
    assert plan["activity_type"] == "BusinessTransactionActivity"
    assert plan["activity_params"] == {
        "offer_template_id": 123,
        "operation_id": "ActivateOffer",
    }


def test_update_existing_flow_with_activity_rejects_unsupported_activity_type():
    import asyncio
    import json
    import pytest

    from agents.campaign_builder import update_existing_flow_with_activity
    from tools.flow_builder import assemble_flow, make_common_activity, make_target_group_activity

    flow = assemble_flow([
        make_common_activity("Unsupported edit"),
        make_target_group_activity(101),
    ])

    with pytest.raises(ValueError, match="Неподдерживаемый тип активности"):
        asyncio.run(update_existing_flow_with_activity.ainvoke({
            "flow_json": json.dumps(flow, ensure_ascii=False),
            "activity_type": "UnsupportedActivity",
        }))
