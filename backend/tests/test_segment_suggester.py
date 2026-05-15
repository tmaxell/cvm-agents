import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import asyncio

from schemas import SegmentSuggestRequest


def test_segment_suggester_returns_structured_hypotheses(monkeypatch):
    from agents import segment_suggester

    async def fake_target_groups():
        return {
            "items": [
                {"id": 105, "name": "Утилизаторы пакета данных (≥80%)", "clientsCount": 67890, "status": "Active"},
                {"id": 107, "name": "Высокий churn-риск", "clientsCount": 31200, "status": "Active"},
                {"id": 102, "name": "Низкий ARPU (<300₽)", "clientsCount": 112540, "status": "Active"},
            ]
        }

    monkeypatch.setattr(segment_suggester.adtarget, "list_target_groups", fake_target_groups)

    response = asyncio.run(segment_suggester.run(SegmentSuggestRequest(
        product="Пакет данных 5 ГБ",
        campaign_goal="увеличить продажи интернет-пакетов и снизить отток",
        audience_constraints={"exclude_recent_contacts_days": 14, "region": "Москва"},
        current_campaign_context={"campaign_id": 123, "channel": "SMS"},
    )))

    assert 2 <= len(response.hypotheses) <= 3
    assert response.hypotheses[0].matched_target_groups[0].target_group_id == 105
    assert response.hypotheses[0].audience_filters["request_constraints"]["region"] == "Москва"
    assert all(0 <= hypothesis.confidence <= 1 for hypothesis in response.hypotheses)
