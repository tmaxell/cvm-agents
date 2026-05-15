import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from schemas import SegmentSuggestRequest


class FakeLLM:
    async def ainvoke(self, messages):
        return SimpleNamespace(content=json.dumps({
            "hypotheses": [
                {
                    "name": "Утилизаторы интернет-пакета",
                    "audience_description": "Клиенты с высокой утилизацией пакета данных.",
                    "relevance_reason": "Высокая вероятность покупки дополнительного пакета.",
                    "selection_criteria": {"usage": ">=80%"},
                    "risk_or_limitation": "Нужно исключить клиентов с недавним контактом.",
                    "matched_target_group": {"id": 105, "name": "Утилизаторы пакета данных (≥80%)"},
                    "is_existing_target_group": True,
                    "confidence": 0.87,
                },
                {
                    "name": "Новый look-alike сегмент",
                    "audience_description": "Похожие клиенты без готовой ЦГ.",
                    "relevance_reason": "Можно протестировать дополнительный спрос.",
                    "selection_criteria": {"look_alike": True},
                    "risk_or_limitation": "Требуется создать и валидировать сегмент.",
                    "matched_target_group": {"id": 999, "name": "Несуществующая ЦГ"},
                    "is_existing_target_group": True,
                    "confidence": 0.7,
                },
            ]
        }, ensure_ascii=False))


class CreatedTargetGroupFakeLLM:
    async def ainvoke(self, messages):
        return SimpleNamespace(content=json.dumps({
            "status": "created",
            "hypotheses": [
                {
                    "name": "Новая Target Group создана для апсейла",
                    "audience_description": "Target Group заведена и уже доступна для 12000 клиентов.",
                    "relevance_reason": "ЦГ сформирована под продукт и готова к запуску.",
                    "selection_criteria": {"usage": ">=80%"},
                    "risk_or_limitation": "Согласия проверены.",
                    "matched_target_group": None,
                    "is_existing_target_group": False,
                    "confidence": 0.8,
                },
                {
                    "name": "ЦГ сформирована для похожих клиентов",
                    "audience_description": "Похожая аудитория для тестового запуска.",
                    "relevance_reason": "Создана целевая группа для теста.",
                    "selection_criteria": {"look_alike": True},
                    "risk_or_limitation": "Frequency cap учтен.",
                    "matched_target_group": None,
                    "is_existing_target_group": False,
                    "confidence": 0.7,
                },
            ]
        }, ensure_ascii=False))


def test_segment_agent_returns_two_to_three_hypotheses_for_product_and_goal(monkeypatch):
    from agents import segment_agent

    async def fake_target_groups():
        return {"items": []}

    monkeypatch.setattr(segment_agent.adtarget, "list_target_groups", fake_target_groups)
    monkeypatch.setattr(segment_agent, "get_llm", lambda for_tools=False: FakeLLM())

    response = asyncio.run(segment_agent.suggest_segments(SegmentSuggestRequest(
        product="Пакет данных 5 ГБ",
        campaign_goal="увеличить продажи интернет-пакетов",
    )))

    assert 2 <= len(response.hypotheses) <= 3
    assert all(hypothesis.name for hypothesis in response.hypotheses)
    assert all(hypothesis.relevance_reason for hypothesis in response.hypotheses)


def test_segment_agent_validates_existing_target_group_matches(monkeypatch):
    from agents import segment_agent

    async def fake_target_groups():
        return {
            "items": [
                {"id": 105, "name": "Утилизаторы пакета данных (≥80%)", "clientsCount": 67890, "status": "Active"},
            ]
        }

    monkeypatch.setattr(segment_agent.adtarget, "list_target_groups", fake_target_groups)
    monkeypatch.setattr(segment_agent, "get_llm", lambda for_tools=False: FakeLLM())

    response = asyncio.run(segment_agent.suggest_segments(SegmentSuggestRequest(
        product="Пакет данных 5 ГБ",
        campaign_goal="увеличить продажи интернет-пакетов",
        audience_constraints={"exclude_recent_contacts_days": 14},
    )))

    assert len(response.hypotheses) == 2
    assert response.hypotheses[0].is_existing_target_group is True
    assert response.hypotheses[0].matched_target_group.target_group_id == 105
    assert response.hypotheses[0].matched_target_group.name == "Утилизаторы пакета данных (≥80%)"
    assert response.hypotheses[0].matched_target_group.clients_count == 67890
    assert response.hypotheses[1].is_existing_target_group is False
    assert response.hypotheses[1].matched_target_group is None
    assert "только рекомендация" in response.hypotheses[1].risk_or_limitation
    assert response.recommendation_only is True


def test_segment_agent_marks_hypothesis_recommendation_only_when_no_target_group_fits(monkeypatch):
    from agents import segment_agent

    async def fake_target_groups():
        return {
            "items": [
                {"id": 105, "name": "Утилизаторы пакета данных (≥80%)", "clientsCount": 67890, "status": "Active"},
            ]
        }

    monkeypatch.setattr(segment_agent.adtarget, "list_target_groups", fake_target_groups)
    monkeypatch.setattr(segment_agent, "get_llm", lambda for_tools=False: FakeLLM())

    response = asyncio.run(segment_agent.suggest_segments(SegmentSuggestRequest(
        product="Пакет данных 5 ГБ",
        campaign_goal="увеличить продажи интернет-пакетов",
    )))

    recommendation_only = response.hypotheses[1]
    assert recommendation_only.is_existing_target_group is False
    assert recommendation_only.matched_target_group is None
    assert recommendation_only.matched_target_groups == []
    assert "только рекомендация" in recommendation_only.risk_or_limitation


def test_segment_agent_does_not_claim_new_target_group_was_created(monkeypatch):
    from agents import segment_agent

    async def fake_target_groups():
        return {"items": []}

    monkeypatch.setattr(segment_agent.adtarget, "list_target_groups", fake_target_groups)
    monkeypatch.setattr(segment_agent, "get_llm", lambda for_tools=False: CreatedTargetGroupFakeLLM())

    response = asyncio.run(segment_agent.suggest_segments(SegmentSuggestRequest(
        product="Пакет данных 5 ГБ",
        campaign_goal="увеличить продажи интернет-пакетов",
    )))

    payload = response.model_dump()
    response_text = json.dumps(payload, ensure_ascii=False).lower()
    forbidden_claims = [
        "target group создана",
        "target group заведена",
        "целевая группа создана",
        "цг сформирована",
        "уже доступна",
        "готова к запуску",
        '"status": "created"',
    ]

    assert "status" not in payload
    for forbidden_claim in forbidden_claims:
        assert forbidden_claim not in response_text
    assert all(hypothesis.is_existing_target_group is False for hypothesis in response.hypotheses)
    assert all(hypothesis.matched_target_group is None for hypothesis in response.hypotheses)


def test_match_existing_target_group_rejects_unconfirmed_name():
    from agents import segment_agent
    from schemas import MatchedTargetGroup, SegmentHypothesis

    hypothesis = SegmentHypothesis(
        name="Утилизаторы интернет-пакета",
        audience_description="Клиенты с высокой утилизацией пакета данных.",
        relevance_reason="Высокая вероятность покупки дополнительного пакета.",
        matched_target_group=MatchedTargetGroup(
            target_group_id=105,
            name="Другое название",
            match_score=0,
        ),
        confidence=0.8,
        priority=1,
    )

    matched = segment_agent._match_existing_target_group(
        hypothesis,
        [{"id": 105, "name": "Утилизаторы пакета данных (≥80%)", "clientsCount": 67890}],
    )

    assert matched is None


def test_match_existing_target_group_uses_mvp_threshold():
    from agents import segment_agent
    from schemas import MatchedTargetGroup, SegmentHypothesis

    weak_hypothesis = SegmentHypothesis(
        name="Сегмент для теста",
        audience_description="Общее описание без совпадений.",
        relevance_reason="Общая причина.",
        matched_target_group=MatchedTargetGroup(
            name="Пакет данных",
            match_score=0,
        ),
        confidence=0.8,
        priority=1,
    )

    assert segment_agent._score_target_group_match(
        weak_hypothesis,
        {"id": 105, "name": "Пакет данных архивный"},
    ) < 0.55
    assert segment_agent._match_existing_target_group(
        weak_hypothesis,
        [{"id": 105, "name": "Пакет данных архивный"}],
    ) is None


def test_parse_raw_response_normalises_required_fields_and_safety_rules():
    from agents import segment_agent

    request = SegmentSuggestRequest(
        product="Пакет данных 10 ГБ",
        campaign_goal="увеличить продажи интернет-пакетов",
    )
    raw = json.dumps({
        "status": "created",
        "hypotheses": [
            {
                "name": "Новая ЦГ создана для апсейла",
                "audience_description": "Сегмент составляет 12345 клиентов, согласия проверены.",
                "selection_criteria": ["usage>=80%"],
                "matched_target_group": {"id": 999, "name": "Несуществующая ЦГ"},
                "is_existing_target_group": True,
                "confidence": 2,
            }
        ]
    }, ensure_ascii=False)

    response = segment_agent._parse_raw_response(
        raw,
        request,
        [{"id": 105, "name": "Утилизаторы пакета данных", "clients_count": 1000}],
    )

    assert len(response.hypotheses) == 2
    assert response.hypotheses[0].matched_target_group is None
    assert response.hypotheses[0].is_existing_target_group is False
    assert response.hypotheses[0].confidence == 1.0
    assert "только рекомендация" in response.hypotheses[0].risk_or_limitation
    assert "Target Group не создана автоматически" in response.hypotheses[0].name
    assert "размер сегмента требует отдельного расчёта" in response.hypotheses[0].audience_description
    assert response.hypotheses[0].relevance_reason
    assert "status" not in response.model_dump()
