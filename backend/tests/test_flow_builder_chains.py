import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from tools.flow_builder import (
    assemble_flow,
    make_business_transaction_activity,
    make_common_activity,
    make_event_activity,
    make_push_communication_activity,
    make_real_time_check_activity,
    make_response_activity,
    make_target_group_activity,
)


def _types(flow: dict) -> list[str]:
    return [activity["type"] for activity in flow["activities"]]


def _assert_linear_links(flow: dict) -> None:
    activities = flow["activities"]
    for index, activity in enumerate(activities):
        expected_next = activities[index + 1]["id"] if index + 1 < len(activities) else None
        assert activity["nextActivityId"] == expected_next


def test_common_target_group_email_chain():
    flow = assemble_flow([
        make_common_activity("Email campaign"),
        make_target_group_activity(101),
        make_push_communication_activity(201, "EmailContent", "Здравствуйте! Новое предложение для вас."),
    ])

    assert _types(flow) == [
        "CommonActivity",
        "TargetGroupActivity",
        "PushCommunicationActivity",
    ]
    assert flow["activities"][2]["contentType"] == "EmailContent"
    _assert_linear_links(flow)


def test_common_target_group_email_business_transaction_chain():
    flow = assemble_flow([
        make_common_activity("Email + BT campaign"),
        make_target_group_activity(101),
        make_push_communication_activity(201, "EmailContent", "Активируйте пакет в один клик."),
        make_business_transaction_activity(301, "ActivateOffer", []),
    ])

    assert _types(flow) == [
        "CommonActivity",
        "TargetGroupActivity",
        "PushCommunicationActivity",
        "BusinessTransactionActivity",
    ]
    assert flow["activities"][3]["businessOperation"]["id"] == "ActivateOffer"
    _assert_linear_links(flow)


def test_common_target_group_email_business_transaction_realtime_check_chain():
    flow = assemble_flow([
        make_common_activity("Email + BT + RT campaign"),
        make_target_group_activity(101),
        make_push_communication_activity(201, "EmailContent", "Проверьте персональный оффер."),
        make_business_transaction_activity(301, "ActivateOffer", []),
        make_real_time_check_activity(filters=[]),
    ])

    assert _types(flow) == [
        "CommonActivity",
        "TargetGroupActivity",
        "PushCommunicationActivity",
        "BusinessTransactionActivity",
        "RealTimeCheckActivity",
    ]
    assert "filters" in flow["activities"][4]
    _assert_linear_links(flow)


def test_common_target_group_event_email_response_chain():
    flow = assemble_flow([
        make_common_activity("Event email response campaign"),
        make_target_group_activity(101),
        make_event_activity("TopUp"),
        make_push_communication_activity(201, "EmailContent", "Спасибо за событие — ответьте на письмо."),
        make_response_activity("EmailReply"),
    ])

    assert _types(flow) == [
        "CommonActivity",
        "TargetGroupActivity",
        "EventActivity",
        "PushCommunicationActivity",
        "ResponseActivity",
    ]
    assert flow["activities"][4]["responseCode"] == "EmailReply"
    _assert_linear_links(flow)
