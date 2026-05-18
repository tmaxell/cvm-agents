import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi.testclient import TestClient

import types

_fake_db = types.ModuleType("db")

class _FakeDatabaseSessionStore:
    pass

async def _fake_init_db():
    return None

_fake_db.DatabaseSessionStore = _FakeDatabaseSessionStore
_fake_db.init_db = _fake_init_db
sys.modules.setdefault("db", _fake_db)

import app as app_module
from schemas import SegmentHypothesis, SegmentSuggestResponse


def _hypothesis(priority: int, name: str) -> SegmentHypothesis:
    return SegmentHypothesis(
        name=name,
        audience_description=f"Описание аудитории {priority}",
        relevance_reason="Релевантно цели кампании",
        selection_criteria={"priority": priority},
        risk_or_limitation="Требуется проверка согласий и контактной политики",
        demo_insight="Демо-гипотеза для ручной проверки аналитиком",
        estimated_reach_label="Средний",
        confidence=0.7,
        title=name,
        description=f"Описание аудитории {priority}",
        rationale="Релевантно цели кампании",
        product_fit="Подходит продукту",
        expected_effect="Поддерживает апсейл",
        audience_filters={"priority": priority},
        priority=priority,
    )


def test_segments_suggest_accepts_family_max_payload(monkeypatch):
    captured_payload = {}

    async def fake_segment_suggest_run(request):
        captured_payload.update(request.model_dump())
        return SegmentSuggestResponse(
            summary="Подготовлено 2 гипотезы сегментов",
            hypotheses=[
                _hypothesis(1, "Семейные пользователи с потенциалом апсейла"),
                _hypothesis(2, "Мульти-SIM домохозяйства"),
            ],
            warnings=[],
            recommendation_only=True,
        )

    monkeypatch.setattr(app_module, "segment_suggest_run", fake_segment_suggest_run)
    client = TestClient(app_module.app)

    response = client.post(
        "/api/segments/suggest",
        json={
            "product": "Тариф Family Max",
            "campaign_goal": "Апсейл семейной аудитории",
            "audience_constraints": {
                "note": "Исключить opt-out и клиентов с контактом за последние 7 дней"
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == "Подготовлено 2 гипотезы сегментов"
    assert len(body["hypotheses"]) == 2
    assert captured_payload["product"] == "Тариф Family Max"
    assert captured_payload["campaign_goal"] == "Апсейл семейной аудитории"
    assert captured_payload["audience_constraints"] == {
        "note": "Исключить opt-out и клиентов с контактом за последние 7 дней"
    }


def test_segments_suggest_validation_failure_returns_diagnostic_detail(monkeypatch):
    async def fake_segment_suggest_run(_request):
        SegmentSuggestResponse.model_validate({"summary": "bad", "hypotheses": []})

    monkeypatch.setattr(app_module, "segment_suggest_run", fake_segment_suggest_run)
    client = TestClient(app_module.app)

    response = client.post(
        "/api/segments/suggest",
        json={
            "product": "Тариф Family Max",
            "campaign_goal": "Апсейл семейной аудитории",
            "audience_constraints": {
                "note": "Исключить opt-out и клиентов с контактом за последние 7 дней"
            },
        },
    )

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["message"] == "Segment suggestion validation failed"
    assert "hypotheses" in detail["error"]
    assert "Ошибка LLM" not in str(detail)
