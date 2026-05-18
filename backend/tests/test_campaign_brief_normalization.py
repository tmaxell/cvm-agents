import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from schemas import BuilderRequest, CampaignBrief


def test_legacy_builder_preferences_are_normalized_to_campaign_brief():
    request = BuilderRequest(
        goal="Собери кампанию",
        builder_preferences={
            "product": "тариф Family Max",
            "goal": "апсейл семейного тарифа",
            "channels": "SMS, Push",
            "targetGroups": "семейные клиенты, data users",
            "content": "премиальный тон, указать выгоду",
            "offerRecommendations": "скидка 20% на первый месяц",
        },
    )

    assert request.campaign_brief is not None
    assert request.campaign_brief.product == "тариф Family Max"
    assert request.campaign_brief.goal == "апсейл семейного тарифа"
    assert [channel.name for channel in request.campaign_brief.channels] == ["SMS", "Push"]
    assert request.campaign_brief.audience.target_groups == ["семейные клиенты", "data users"]
    assert request.campaign_brief.audience.description == "семейные клиенты, data users"
    assert request.campaign_brief.constraints.content == "премиальный тон, указать выгоду"
    assert request.campaign_brief.constraints.offer_recommendations == "скидка 20% на первый месяц"


def test_campaign_brief_backfills_legacy_builder_preferences():
    request = BuilderRequest(
        goal="Собери кампанию",
        campaign_brief=CampaignBrief.from_builder_preferences({
            "product": "Data Pack",
            "goal": "activation",
            "channels": "Email; Push",
            "targetGroups": "inactive users",
            "content": "friendly copy",
            "offerRecommendations": "bonus bundle",
        }),
    )

    assert request.builder_preferences == {
        "product": "Data Pack",
        "goal": "activation",
        "targetGroups": "inactive users",
        "channels": "Email, Push",
        "content": "friendly copy",
        "offerRecommendations": "bonus bundle",
    }
