import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from agents.chat_orchestrator import IntentClassifier


def test_intent_router_prefers_rule_match_for_attention_report():
    classifier = IntentClassifier()
    decision = asyncio.run(classifier.classify("Сделай отчет по вниманию и метрикам кампаний"))
    assert decision.intent == "campaign_attention_report"
    assert decision.confidence >= 0.9


def test_intent_router_returns_clarify_for_ambiguous_prompt_without_llm(monkeypatch):
    classifier = IntentClassifier(confidence_threshold=0.95)

    async def fake_llm(_message: str):
        from agents.chat_orchestrator import IntentDecision

        return IntentDecision("build_campaign", 0.4, reason="low confidence", clarify_question="Уточните, собрать кампанию или сегменты?")

    monkeypatch.setattr(classifier, "_llm_classify", fake_llm)
    decision = asyncio.run(classifier.classify("Сделай что-нибудь"))
    assert decision.intent == "clarify"
    assert "Уточните" in (decision.clarify_question or "")
