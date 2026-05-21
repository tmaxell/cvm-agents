import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from agents import campaign_attention


class _FakeScalarsResult:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def scalars(self, _query):
        return _FakeScalarsResult(self._rows)


def test_campaign_attention_report_is_deterministic_with_seeded_campaigns(seeded_campaigns, monkeypatch):
    monkeypatch.setattr(campaign_attention, "AsyncSessionLocal", lambda: _FakeSession(seeded_campaigns))
    report = asyncio.run(campaign_attention.build_campaign_attention_report())

    assert report["status"] == "ok"
    # Кампания #201 — severity=critical, должна быть в топе priority_score
    assert report["campaigns"][0]["campaign_id"] == 201
    # Формула приоритета описана текстом — проверяем что есть упоминание priority + слагаемые
    assert "priority" in report["ranking_formula"]
    assert "severity_weight" in report["ranking_formula"]
    # Текст проблемы из health.issues_json должен оказаться в issues кампании
    assert any("CTR drop" in issue.get("message", "") for issue in report["campaigns"][0]["issues"])
