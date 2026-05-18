import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from agents import campaign_builder
from agents.safety_review import is_review_allowed_for_runtime
from schemas import BuilderRequest, CampaignBrief
from tools.flow_builder import (
    assemble_flow,
    make_common_activity,
    make_push_communication_activity,
    make_target_group_activity,
)


def _brief() -> CampaignBrief:
    return CampaignBrief.from_builder_preferences({
        "goal": "Удержание",
        "product": "Тариф Max",
        "targetGroups": "Target Group #42",
        "channels": "SMS",
        "content": "Проверьте персональное предложение.",
    })


def _flow_without_consent() -> dict:
    return assemble_flow([
        make_common_activity("Retention"),
        make_target_group_activity(42),
        make_push_communication_activity(1, "SmsContent", "Проверьте персональное предложение."),
    ])


def test_builder_blocks_create_without_green_or_acknowledged_checklist():
    flow = _flow_without_consent()

    response = asyncio.run(campaign_builder.run(BuilderRequest(
        goal="Создай кампанию в AdTarget",
        session_flow_json=__import__("json").dumps(flow, ensure_ascii=False),
        campaign_brief=_brief(),
    )))

    assert response.status == "error"
    assert response.campaign_id is None
    assert response.review_status == "blocked"
    assert any(item.category == "consent" and item.status == "blocker" for item in response.review_checklist.items)
    assert "Create/launch заблокирован" in response.message


def test_builder_allows_acknowledged_warnings_but_not_blockers():
    flow = _flow_without_consent()

    response = asyncio.run(campaign_builder.run(BuilderRequest(
        goal="Создай кампанию в AdTarget",
        session_flow_json=__import__("json").dumps(flow, ensure_ascii=False),
        campaign_brief=_brief(),
        review_checklist_acknowledged=True,
    )))

    assert response.status == "error"
    assert response.review_status == "blocked"
    assert response.campaign_id is None



def test_launch_policy_requires_green_or_acknowledged_review_status():
    assert not is_review_allowed_for_runtime("warnings", acknowledged_warnings=False)
    assert is_review_allowed_for_runtime("warnings", acknowledged_warnings=True)
    assert is_review_allowed_for_runtime("green", acknowledged_warnings=False)
    assert not is_review_allowed_for_runtime("blocked", acknowledged_warnings=True)
