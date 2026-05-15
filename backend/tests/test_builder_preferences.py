import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import asyncio
from schemas import BuilderRequest


def test_memory_only_run_returns_product_preference_patch(monkeypatch):
    from agents import campaign_builder

    async def fake_plan(*args, **kwargs):
        return {
            "action": "remember_context",
            "assistant_message": "Запомнил продукт.",
            "preference_patch": {},
        }, None

    monkeypatch.setattr(campaign_builder, "_llm_plan_special_turn", fake_plan)

    response = asyncio.run(campaign_builder.run(BuilderRequest(goal="Запомни: продукт — тариф Max")))

    assert response.preference_patch == {"product": "тариф Max"}
    assert response.builder_preferences == {"product": "тариф Max"}


def test_next_builder_run_receives_returned_preferences_in_payload(monkeypatch):
    from langchain_core.messages import AIMessage
    from agents import campaign_builder

    async def fake_plan(*args, **kwargs):
        return {
            "action": "remember_context",
            "assistant_message": "Запомнил продукт.",
            "preference_patch": {},
        }, None

    class FakeGraph:
        def __init__(self):
            self.payload = None

        async def ainvoke(self, payload):
            self.payload = payload
            return {
                "messages": [AIMessage(content="Собираю кампанию по вашим параметрам.")],
                "campaign_id": None,
                "last_flow_json": None,
            }

    async def fake_fetch_reference_data():
        return {"target_groups": [], "channels": [], "events": [], "offers": []}

    fake_graph = FakeGraph()
    monkeypatch.setattr(campaign_builder, "_llm_plan_special_turn", fake_plan)
    monkeypatch.setattr(campaign_builder, "_fetch_reference_data", fake_fetch_reference_data)
    monkeypatch.setattr(campaign_builder, "get_graph", lambda: fake_graph)

    first_response = asyncio.run(campaign_builder.run(BuilderRequest(goal="Запомни: продукт — тариф Max")))
    assert first_response.builder_preferences == {"product": "тариф Max"}

    asyncio.run(campaign_builder.run(BuilderRequest(
        goal="Составь кампанию по моим параметрам",
        builder_preferences=first_response.builder_preferences or {},
    )))

    assert fake_graph.payload is not None
    assert "Builder UI preferences" in fake_graph.payload["system_prompt"]
    assert '"product": "тариф Max"' in fake_graph.payload["system_prompt"]
