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
    assert fake_graph.payload["last_flow_json"] is None
