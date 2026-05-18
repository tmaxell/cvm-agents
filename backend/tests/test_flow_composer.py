import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from agents.flow_composer import compose_campaign_flow
from schemas import CampaignBrief, CampaignChannel, CampaignConstraints, CampaignAudienceRef


def _brief() -> CampaignBrief:
    return CampaignBrief(
        product="Тариф Max",
        goal="Удержание с активацией оффера",
        audience=CampaignAudienceRef(
            target_groups=["Target Group: #42 · Риск оттока"],
            description="Target Group: #42 · Риск оттока",
        ),
        channels=[
            CampaignChannel(name="SMS", channel_id=201),
            CampaignChannel(name="Push", channel_id=202),
        ],
        constraints=CampaignConstraints(
            content="Подключите персональное предложение по тарифу Max.",
            offer_recommendations="Проверить activation через 3 дня",
        ),
    )


def test_composer_builds_canonical_route_without_llm_or_network():
    flow = compose_campaign_flow(_brief())

    assert [activity["composerRole"] for activity in flow["activities"]] == [
        "Start/Common",
        "AudienceFilter",
        "ConsentCheck",
        "SMS",
        "Push",
        "Wait",
        "ActivationCheck",
    ]
    assert [activity["type"] for activity in flow["activities"]] == [
        "CommonActivity",
        "TargetGroupActivity",
        "RealTimeCheckActivity",
        "PushCommunicationActivity",
        "PushCommunicationActivity",
        "WaitActivity",
        "RealTimeCheckActivity",
    ]
    assert flow["activities"][1]["clientSourceId"] == 42
    assert flow["activities"][2]["name"] == "Consent check"
    assert flow["activities"][-1]["name"] == "Activation check"
    assert flow["validation"]["deterministic"] is True
    assert flow["validation"]["warnings"] == []


def test_composer_is_deterministic_for_same_brief():
    first = compose_campaign_flow(_brief())
    second = compose_campaign_flow(_brief())

    assert first == second


def test_composer_adds_validation_warning_when_target_group_id_missing():
    flow = compose_campaign_flow(CampaignBrief(
        product="Пакет данных",
        goal="Информирование",
        audience=CampaignAudienceRef(description="молодые пользователи мобильного интернета"),
        channels=[CampaignChannel(name="Email", channel_id=301)],
    ))

    assert flow["activities"][1]["clientSourceId"] == 0
    assert flow["activities"][-1]["composerRole"] == "Response"
    assert flow["validation"]["warnings"][0]["code"] == "missing_target_group_id"
    assert flow["offers"][0]["contentType"] == "EmailContent"


def test_composer_uses_default_sms_push_channels_when_brief_has_no_channels():
    flow = compose_campaign_flow(CampaignBrief(
        product="Пакет данных",
        goal="Активация",
        audience=CampaignAudienceRef(description="TG 77"),
    ))

    roles = [activity["composerRole"] for activity in flow["activities"]]
    assert roles == [
        "Start/Common",
        "AudienceFilter",
        "ConsentCheck",
        "SMS",
        "Push",
        "Wait",
        "ActivationCheck",
    ]
    assert "channels: SMS + Push" in flow["validation"]["assumptions"]
