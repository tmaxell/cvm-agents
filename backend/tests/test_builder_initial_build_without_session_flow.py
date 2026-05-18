import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from langchain_core.messages import AIMessage

from schemas import BuilderRequest


class FakeGraph:
    def __init__(self):
        self.payload = None

    async def ainvoke(self, payload):
        self.payload = payload
        return {
            "messages": [AIMessage(content="Flow собран как draft.")],
            "campaign_id": None,
            "last_flow_json": None,
        }


def test_initial_build_with_realtime_check_without_session_flow_uses_draft_build_path(monkeypatch):
    from agents import campaign_builder

    async def fake_fetch_reference_data():
        return {"target_groups": [], "channels": [], "events": [], "offers": []}

    fake_graph = FakeGraph()
    monkeypatch.setattr(campaign_builder, "_fetch_reference_data", fake_fetch_reference_data)
    monkeypatch.setattr(campaign_builder, "get_graph", lambda: fake_graph)

    response = asyncio.run(campaign_builder.run(BuilderRequest(
        goal="Собери flow с RealTimeCheck для удержания абонентов",
        session_flow_json=None,
        builder_preferences={
            "goal": "Удержание абонентов",
            "product": "тариф Max",
            "targetGroups": "Target Group: #42 · Абоненты с риском оттока\nСегмент: churn risk",
            "channels": "SMS, Push",
        },
    )))

    assert fake_graph.payload is not None
    assert response.status != "error"
    assert "Не нашёл текущий flow" not in response.message
    assert "Builder UI preferences" in fake_graph.payload["system_prompt"]
    assert fake_graph.payload["last_flow_json"] is not None


def test_draft_build_with_last_flow_does_not_auto_create_campaign(monkeypatch):
    import json
    from unittest.mock import AsyncMock

    from agents import campaign_builder
    from tools.flow_builder import (
        assemble_flow,
        make_common_activity,
        make_push_communication_activity,
        make_target_group_activity,
    )

    flow = assemble_flow([
        make_common_activity("Draft retention"),
        make_target_group_activity(42),
        make_push_communication_activity(201, "SmsContent", "Проверьте персональное предложение."),
    ])

    class DraftGraph:
        async def ainvoke(self, payload):
            return {
                "messages": [AIMessage(content="Flow собран как draft.")],
                "campaign_id": None,
                "last_flow_json": json.dumps(flow, ensure_ascii=False),
            }

    async def fake_fetch_reference_data():
        return {"target_groups": [], "channels": [], "events": [], "offers": []}

    create_campaign_mock = AsyncMock(return_value={"campaignId": 999})
    monkeypatch.setattr(campaign_builder, "_fetch_reference_data", fake_fetch_reference_data)
    monkeypatch.setattr(campaign_builder, "get_graph", lambda: DraftGraph())
    monkeypatch.setattr(campaign_builder.adtarget, "create_campaign", create_campaign_mock)

    response = asyncio.run(campaign_builder.run(BuilderRequest(
        goal="Собери draft кампании для удержания абонентов",
        builder_preferences={"goal": "Удержание абонентов"},
    )))

    create_campaign_mock.assert_not_awaited()
    assert response.campaign_id is None
    assert response.draft_flow == flow
    assert response.status == "draft_ready"
    assert "Draft" in response.message
    assert "готов к review" in response.message
    assert "отдельного подтверждения" in response.message


def test_explicit_builder_create_endpoint_calls_adtarget_create(monkeypatch):
    from unittest.mock import AsyncMock
    import types

    class PlaceholderSessionStore:
        pass

    async def fake_init_db():
        return None

    monkeypatch.setitem(sys.modules, "db", types.SimpleNamespace(
        DatabaseSessionStore=PlaceholderSessionStore,
        init_db=fake_init_db,
    ))

    from app import builder_create
    import app as app_module
    from schemas import BuilderCreateRequest, CampaignBrief, CampaignAudienceRef, CampaignConstraints, SessionDetail

    flow = {
        "activities": [
            {"id": "common", "type": "CommonActivity", "name": "Retention"},
            {"id": "tg", "type": "TargetGroupActivity", "name": "Target Group", "targetGroupId": 42},
            {"id": "consent", "type": "ConsentCheckActivity", "name": "Consent opt-in"},
            {
                "id": "push",
                "type": "PushCommunicationActivity",
                "name": "Push",
                "content": {"parameters": [{"name": "text", "value": "Проверьте персональное предложение."}]},
            },
        ],
    }
    brief = CampaignBrief(
        product="тариф Max",
        goal="удержание",
        audience=CampaignAudienceRef(target_groups=["42"], description="Target Group #42"),
        constraints=CampaignConstraints(content="Проверьте персональное предложение."),
    )

    class FakeSessionStore:
        async def get_session(self, session_id):
            return SessionDetail(
                id=session_id,
                campaign_id=None,
                title="Test session",
                created_at="2026-05-18T00:00:00Z",
                updated_at="2026-05-18T00:00:00Z",
                status="draft_ready",
                draft_flow_version=3,
                messages=[],
            )

        async def add_message(self, **kwargs):
            return None

        async def upsert_campaign_state(self, **kwargs):
            return None

    create_campaign_mock = AsyncMock(return_value={"campaignId": 777001})
    monkeypatch.setattr(app_module, "session_store", FakeSessionStore())
    monkeypatch.setattr(app_module.adtarget, "create_campaign", create_campaign_mock)

    response = asyncio.run(builder_create(BuilderCreateRequest(
        session_id="session-1",
        draft_flow=flow,
        draft_flow_version=3,
        campaign_brief=brief,
        review_checklist_acknowledged=True,
    )))

    create_campaign_mock.assert_awaited_once_with(flow)
    assert response.campaign_id == 777001
    assert response.status == "created_in_adtarget"
    assert response.draft_flow == flow
