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


def test_offer_selector_prefers_exact_product_name_even_when_not_first():
    from agents.campaign_builder import _select_offer_template

    ref = {
        "offers": [
            {"id": 1, "name": "Пакет данных 5 ГБ", "operationId": "ActivateData5Gb"},
            {"id": 2, "name": "Тариф Max", "operationId": "ActivateMaxTariff"},
        ]
    }

    selected = _select_offer_template(ref, "добавь транзакцию", {"product": "тариф Max"})

    assert selected is not None
    assert selected["id"] == 2


def test_offer_selector_uses_family_max_token_match():
    from agents.campaign_builder import _select_offer_template

    ref = {
        "offers": [
            {"id": 1, "name": "Пакет данных 5 ГБ", "operationId": "ActivateData5Gb"},
            {"id": 2, "name": "Family Max", "operationId": "ActivateFamilyMax"},
        ]
    }

    selected = _select_offer_template(ref, "подключить family max", {"product": "семейный max"})

    assert selected is not None
    assert selected["id"] == 2


def test_offer_selector_does_not_fallback_to_first_offer_without_match():
    from agents.campaign_builder import _resolve_business_transaction_activity_params, _select_offer_template

    ref = {
        "offers": [
            {"id": 1, "name": "Пакет данных 5 ГБ", "operationId": "ActivateData5Gb"},
            {"id": 2, "name": "Безлимитные минуты", "operationId": "ActivateVoiceUnlimited"},
        ]
    }

    selected = _select_offer_template(ref, "добавь транзакцию", {"product": "тариф Max"})
    params, offer, warning = _resolve_business_transaction_activity_params(
        {},
        ref,
        "добавь транзакцию",
        {"product": "тариф Max"},
    )

    assert selected is None
    assert params is None
    assert offer is None
    assert warning is not None
    assert "первый шаблон" in warning


def test_follow_up_realtime_check_after_sms_applies_current_version_and_increments(monkeypatch):
    import asyncio
    import json

    from agents import campaign_builder
    from schemas import BuilderRequest
    from tools.flow_builder import (
        assemble_flow,
        make_common_activity,
        make_push_communication_activity,
        make_target_group_activity,
    )

    async def fake_fetch_reference_data():
        return {"target_groups": [], "channels": [], "events": [], "offers": []}

    monkeypatch.setattr(campaign_builder, "_fetch_reference_data", fake_fetch_reference_data)

    flow = assemble_flow([
        make_common_activity("Retention draft"),
        make_target_group_activity(42),
        make_push_communication_activity(201, "SmsContent", "Текст SMS"),
    ])
    current_version = 3

    response = asyncio.run(campaign_builder.run(BuilderRequest(
        goal="добавь RealTimeCheck после SMS",
        session_flow_json=json.dumps(flow, ensure_ascii=False),
        draft_flow_version=current_version,
    )))

    assert response.draft_flow_version == current_version + 1
    assert response.draft_flow is not None
    activity_types = [activity["type"] for activity in response.draft_flow["activities"]]
    assert activity_types == [
        "CommonActivity",
        "TargetGroupActivity",
        "PushCommunicationActivity",
        "RealTimeCheckActivity",
    ]
    sms_activity = response.draft_flow["activities"][2]
    realtime_activity = response.draft_flow["activities"][3]
    assert sms_activity["nextActivityId"] == realtime_activity["id"]


def test_flow_patch_add_activity_uses_helper_and_increments_topology():
    from agents.campaign_builder import _apply_flow_patch
    from schemas import FlowPatch
    from tools.flow_builder import (
        assemble_flow,
        make_common_activity,
        make_push_communication_activity,
    )

    flow = assemble_flow([
        make_common_activity("Patch add"),
        make_push_communication_activity(201, "SmsContent", "Текст SMS"),
    ])

    result = _apply_flow_patch(
        flow,
        FlowPatch(
            base_version=2,
            operations=["add_activity"],
            anchor_activity_type="PushCommunicationActivity",
            insert_position="after",
            activity={"type": "RealTimeCheckActivity", "params": {}},
        ),
        current_version=2,
    )

    assert [activity["type"] for activity in result["activities"]] == [
        "CommonActivity",
        "PushCommunicationActivity",
        "RealTimeCheckActivity",
    ]
    assert result["activities"][1]["nextActivityId"] == result["activities"][2]["id"]


def test_flow_patch_remove_activity_uses_helper_and_relinks():
    from agents.campaign_builder import _apply_flow_patch
    from schemas import FlowPatch
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

    result = _apply_flow_patch(
        flow,
        FlowPatch(
            base_version=4,
            operations=["remove_activity"],
            activity={"type": "BusinessTransactionActivity", "occurrence": "last"},
        ),
        current_version=4,
    )

    assert [activity["type"] for activity in result["activities"]] == [
        "PushCommunicationActivity",
        "RealTimeCheckActivity",
    ]
    assert result["activities"][0]["nextActivityId"] == result["activities"][1]["id"]


def test_flow_patch_version_conflict_does_not_mutate_draft():
    import pytest

    from agents.campaign_builder import FlowPatchConflictError, _apply_flow_patch
    from schemas import FlowPatch
    from tools.flow_builder import assemble_flow, make_common_activity, make_push_communication_activity

    flow = assemble_flow([
        make_common_activity("Patch conflict"),
        make_push_communication_activity(201, "SmsContent", "Текст SMS"),
    ])
    original_activities = [dict(activity) for activity in flow["activities"]]

    with pytest.raises(FlowPatchConflictError):
        _apply_flow_patch(
            flow,
            FlowPatch(
                base_version=1,
                operations=["add_activity"],
                activity={"type": "RealTimeCheckActivity", "params": {}},
            ),
            current_version=2,
        )

    assert flow["activities"] == original_activities
