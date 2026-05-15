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


def test_add_realtime_check_after_transaction_keeps_existing_transaction_position():
    from agents.campaign_builder import _add_activity_to_flow, _parse_flow_edit_intent
    from tools.flow_builder import (
        assemble_flow,
        make_business_transaction_activity,
        make_push_communication_activity,
    )

    flow = assemble_flow([
        make_push_communication_activity(201, "EmailContent", "Письмо с предложением."),
        make_business_transaction_activity(301, "ActivateOffer", []),
    ])
    intent = _parse_flow_edit_intent("добавь после транзакции real-time check")
    assert intent is not None

    result = _add_activity_to_flow(
        flow,
        intent.activity_type,
        anchor_activity_type=intent.anchor_activity_type,
        anchor_position="after",
    )

    assert [activity["type"] for activity in result["activities"]] == [
        "PushCommunicationActivity",
        "BusinessTransactionActivity",
        "RealTimeCheckActivity",
    ]
    assert result["activities"][1]["nextActivityId"] == result["activities"][2]["id"]
    assert result["activities"][2]["nextActivityId"] is None


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


def test_remove_last_transaction_intent_targets_business_transaction():
    intent = _parse_flow_edit_intent("убери последнюю транзакцию")

    assert intent is not None
    assert intent.action == "remove_activity"
    assert intent.activity_type == "BusinessTransactionActivity"
    assert intent.occurrence == "last"


def test_remove_last_transaction_relinks_sms_to_realtime_check():
    from agents.campaign_builder import _remove_activity_from_flow
    from tools.flow_builder import (
        assemble_flow,
        make_business_transaction_activity,
        make_push_communication_activity,
        make_real_time_check_activity,
    )

    flow = assemble_flow([
        make_push_communication_activity(201, "SmsContent", "Текст SMS"),
        make_business_transaction_activity(301, "ActivateOffer", []),
        make_real_time_check_activity(),
    ])

    result = _remove_activity_from_flow(
        flow,
        activity_type="BusinessTransactionActivity",
        occurrence="last",
    )

    assert [activity["type"] for activity in result["activities"]] == [
        "PushCommunicationActivity",
        "RealTimeCheckActivity",
    ]


def test_remove_last_transaction_rebuilds_next_activity_id():
    from agents.campaign_builder import _remove_activity_from_flow
    from tools.flow_builder import (
        assemble_flow,
        make_business_transaction_activity,
        make_push_communication_activity,
        make_real_time_check_activity,
    )

    flow = assemble_flow([
        make_push_communication_activity(201, "SmsContent", "Текст SMS"),
        make_business_transaction_activity(301, "ActivateOffer", []),
        make_real_time_check_activity(),
    ])

    result = _remove_activity_from_flow(
        flow,
        activity_type="BusinessTransactionActivity",
        occurrence="last",
    )

    assert result["activities"][0]["nextActivityId"] == result["activities"][1]["id"]
    assert result["activities"][1]["nextActivityId"] is None
