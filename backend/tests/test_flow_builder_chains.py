import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from tools.flow_builder import (
    assemble_flow,
    build_upsell_with_reminder_flow,
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


def test_upsell_with_reminder_flow_matches_template():
    """Структура должна совпадать с examples/upsell_exp.json: 9 активностей,
    SMS offer → Response#1 (timeout → reminder), Response#1/#2 → OrJoin → BT → Exclude."""
    flow = build_upsell_with_reminder_flow(
        campaign_name="Апсейл Семейный",
        product="Тариф Семейный",
        target_group_id=478,
        target_group_name="ультра vad4",
        offer_text="Уважаемый абонент, с {{[c.BeginDate]}} по {{[c.EndDate]}} перейдите на «Семейный».",
        switch_tariff_plan_id=301,
    )

    types = _types(flow)
    assert types == [
        "CommonActivity",
        "TargetGroupActivity",
        "PushCommunicationActivity",
        "ResponseActivity",
        "PushCommunicationActivity",
        "ResponseActivity",
        "OrJoinActivity",
        "BusinessTransactionActivity",
        "ExcludeFromCampaignActivity",
    ]

    by_id = {a["id"]: a for a in flow["activities"]}
    common, tg, sms_offer, resp1, sms_reminder, resp2, orjoin, bt, exclude = flow["activities"]

    # Линейные участки
    assert common["nextActivityId"] == tg["id"]
    assert tg["nextActivityId"] == sms_offer["id"]

    # SMS offer → Response #1 через defaultSuccessActivityId (а не nextActivityId)
    assert sms_offer["nextActivityId"] is None
    assert sms_offer["defaultSuccessActivityId"] == resp1["id"]
    assert sms_offer["contentType"] == "SmsContent"

    # Response #1: case "1" → orjoin, timeout → reminder
    assert resp1["cases"]["1"] == orjoin["id"]
    assert resp1["timeOutNextActivityId"] == sms_reminder["id"]
    assert resp1["timeoutParameters"]["interval"] == 259_200

    # Reminder → Response #2
    assert sms_reminder["defaultSuccessActivityId"] == resp2["id"]
    assert sms_reminder["isNotification"] is True

    # Response #2: case "1" → orjoin (без timeout-ветки)
    assert resp2["cases"]["1"] == orjoin["id"]

    # OrJoin → BT → Exclude
    assert orjoin["nextActivityId"] == bt["id"]
    assert bt["defaultSuccessActivityId"] == exclude["id"]
    assert bt["businessOperation"]["id"] == "switchTariffPlan"
    params = {p["name"]: p["value"] for p in bt["businessOperation"]["parameters"]}
    assert params["newPlanId"] == "301"
    assert "Comment" in params and "FromNextPeriod" in params

    # ExcludeFromCampaign: removeFromCurrentCampaign=True
    assert exclude["removeFromCurrentCampaign"] is True

    # subNodes должны содержать фильтры для обоих Response
    sub_ids = {sn["id"] for sn in flow["subNodes"]}
    assert f"{resp1['id']}__1" in sub_ids
    assert f"{resp2['id']}__1" in sub_ids

    # linkedCommunicationActivities должны ссылаться на свои SMS
    assert sms_offer["id"] in resp1.get("linkedCommunicationActivities", [])
    assert sms_reminder["id"] in resp2.get("linkedCommunicationActivities", [])

    # Все id уникальны
    assert len(by_id) == 9
