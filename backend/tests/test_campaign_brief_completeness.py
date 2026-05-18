import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from langchain_core.messages import AIMessage

from agents.campaign_builder import check_campaign_brief_completeness
from schemas import BuilderRequest


class FakeGraph:
    def __init__(self):
        self.payload = None

    async def ainvoke(self, payload):
        self.payload = payload
        return {
            "messages": [AIMessage(content="Draft ready")],
            "campaign_id": None,
            "last_flow_json": None,
        }


def test_full_brief_has_no_missing_fields_or_assumptions():
    request = BuilderRequest(
        goal="Собери кампанию",
        builder_preferences={
            "goal": "retention",
            "product": "Family Max",
            "targetGroups": "churn risk subscribers",
            "channels": "SMS, Push",
        },
    )

    completeness = check_campaign_brief_completeness(request)

    assert completeness.missing_fields == []
    assert completeness.assumptions == []
    assert completeness.safety_checks


def test_incomplete_brief_reports_missing_required_fields():
    request = BuilderRequest(
        goal="Собери кампанию",
        builder_preferences={"product": "Family Max"},
    )

    completeness = check_campaign_brief_completeness(request)

    assert completeness.missing_fields == ["goal", "audience", "channels"]
    assert completeness.assumptions == ["channels: SMS + Push"]


def test_default_channels_assumption_is_returned_and_added_to_prompt(monkeypatch):
    from agents import campaign_builder

    async def fake_fetch_reference_data():
        return {"target_groups": [], "channels": [], "events": [], "offers": []}

    fake_graph = FakeGraph()
    monkeypatch.setattr(
        campaign_builder, "_fetch_reference_data", fake_fetch_reference_data
    )
    monkeypatch.setattr(campaign_builder, "get_graph", lambda: fake_graph)

    response = asyncio.run(
        campaign_builder.run(
            BuilderRequest(
                goal="Собери draft flow",
                builder_preferences={
                    "goal": "activation",
                    "product": "Data Pack",
                    "targetGroups": "data users",
                },
            )
        )
    )

    assert response.brief_completeness is not None
    assert response.brief_completeness.missing_fields == ["channels"]
    assert response.brief_completeness.assumptions == ["channels: SMS + Push"]
    assert fake_graph.payload is not None
    assert (
        '"assumptions": ["channels: SMS + Push"]' in fake_graph.payload["system_prompt"]
    )
    assert "Apply explicit assumptions" in fake_graph.payload["system_prompt"]
