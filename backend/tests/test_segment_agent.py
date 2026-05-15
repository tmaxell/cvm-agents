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
    assert response.hypotheses[1].is_existing_target_group is False
    assert response.hypotheses[1].matched_target_group is None
    assert "только рекомендация" in response.hypotheses[1].risk_or_limitation
    assert response.recommendation_only is True


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
