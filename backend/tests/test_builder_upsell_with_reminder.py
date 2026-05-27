"""Интеграционный тест на ветку upsell-with-reminder в BuilderAgent.

Проверяет полный путь: при `goal=апсейл`, `channel=SMS`, `product=тариф` и
наличии target_group в сессии BuilderAgent собирает кампанию по структуре
examples/upsell_exp.json (9 активностей, ветка timeout→reminder, OrJoin,
BusinessTransaction switchTariffPlan, ExcludeFromCampaign).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


class _FakeStore:
    """In-memory заглушка ChatStore — хватает для _finalize/_build_upsell_with_reminder."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.artifacts: dict[str, dict[str, Any]] = {}
        self._counter = 0

    async def add_event(self, *, run_id: str, event: str, status: str = "info",
                        detail: str | None = None, metadata: dict[str, Any] | None = None) -> None:
        self.events.append({"event": event, "status": status, "detail": detail, "metadata": metadata or {}})

    async def save_artifact(self, *, session_id: str, artifact_type: str,
                            content_json: dict[str, Any], metadata_json: dict[str, Any] | None = None,
                            source_run_id: str | None = None) -> str:
        self._counter += 1
        aid = f"art-{self._counter}"
        self.artifacts[aid] = {
            "id": aid,
            "type": artifact_type,
            "content": content_json,
            "metadata": metadata_json or {},
        }
        return aid

    async def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        return self.artifacts.get(artifact_id)


def test_build_upsell_with_reminder_assembles_9_activity_dag(monkeypatch):
    from agents import agent_builder
    from agents.base import AgentContext
    from agents.builder.brief import CampaignBriefAnalysis

    # Мокаем _resolve_switch_plan_id, чтобы не лезть в БД.
    async def _fake_plan_id(product: str | None) -> int:
        return 301
    monkeypatch.setattr(agent_builder, "_resolve_switch_plan_id", _fake_plan_id)

    store = _FakeStore()
    # В сессии уже есть target_group_draft — _resolve_target_group её найдёт.
    tg_artifact = {
        "id": "tg-pre",
        "type": "target_group_draft",
        "content": {"target_group_id": 478, "name": "Семьи, NBO=Семейный"},
        "metadata": {},
    }
    ctx = AgentContext(
        session_id="s-1",
        run_id="r-1",
        store=store,
        message="возьми эту ТГ и собери кампанию",
        artifacts=[tg_artifact],
        inputs={},
    )

    brief = CampaignBriefAnalysis(
        goal="апсейл тарифа",
        product="Тариф Семейный",
        channels=["sms"],
        audience={"description": "Семьи, NBO=Семейный"},
        scenario="upsell",
        missing_critical=[],
        confidence=0.9,
        notes=[],
    )

    # Условие срабатывания upsell-ветки должно выполниться.
    assert asyncio.run(agent_builder._matches_upsell_with_reminder(brief, ctx)) is True

    offer_text = (
        "Уважаемый абонент, с {{[c.BeginDate]}} по {{[c.EndDate]}} вы можете "
        "бесплатно перейти на новый ТП Семейный — отправьте «Ок» на 999."
    )
    result = asyncio.run(
        agent_builder._build_upsell_with_reminder(
            ctx, goal="апсейл", brief=brief, offer_text=offer_text,
        )
    )

    assert result.status == "ok"
    assert result.metadata["mode"] == "upsell_with_reminder"
    # _finalize сохраняет draft_flow артефакт.
    draft = next((a for a in result.artifacts if a["type"] == "draft_flow"), None)
    assert draft is not None
    flow = draft["content"]
    activities = flow["activities"]
    assert len(activities) == 8

    types = [a["type"] for a in activities]
    assert types == [
        "CommonActivity",
        "TargetGroupActivity",
        "PushCommunicationActivity",
        "ResponseActivity",
        "PushCommunicationActivity",
        "ResponseActivity",
        "OrJoinActivity",
        "BusinessTransactionActivity",
    ]
    # Exclude не должен попадать в флоу — BT терминальный.
    assert not any(a["type"] == "ExcludeFromCampaignActivity" for a in activities)

    sms_offer = activities[2]
    resp1 = activities[3]
    sms_reminder = activities[4]
    resp2 = activities[5]
    orjoin = activities[6]
    bt = activities[7]

    # Текст оффера попал в первый PushCommunication (в content.parameters Text).
    text_param = next(
        (p for p in sms_offer["content"]["parameters"] if p.get("name") == "Text"),
        None,
    )
    assert text_param is not None
    assert "{{[c.BeginDate]}}" in text_param["value"]
    assert sms_offer["contentType"] == "SmsContent"
    # Response#1 ждёт «Ок», иначе через 3 дня уходит на reminder.
    assert resp1["cases"]["1"] == orjoin["id"]
    assert resp1["timeOutNextActivityId"] == sms_reminder["id"]
    assert resp1["timeoutParameters"]["interval"] == 259_200
    # Reminder помечен как isNotification.
    assert sms_reminder["isNotification"] is True
    # Response#2 → OrJoin.
    assert resp2["cases"]["1"] == orjoin["id"]
    # OrJoin → BT (терминал).
    assert orjoin["nextActivityId"] == bt["id"]
    assert bt["defaultSuccessActivityId"] is None
    assert bt["businessOperation"]["id"] == "switchTariffPlan"
    params = {p["name"]: p["value"] for p in bt["businessOperation"]["parameters"]}
    assert params["newPlanId"] == "301"


def test_upsell_match_requires_tariff_sms_upsell_and_tg(monkeypatch):
    from agents import agent_builder
    from agents.base import AgentContext
    from agents.builder.brief import CampaignBriefAnalysis

    store = _FakeStore()

    def _ctx(artifacts: list[dict[str, Any]]) -> AgentContext:
        return AgentContext(
            session_id="s", run_id="r", store=store, message="",
            artifacts=artifacts, inputs={},
        )

    tg = [{
        "id": "tg", "type": "target_group_draft",
        "content": {"target_group_id": 1, "name": "TG"}, "metadata": {},
    }]

    upsell_sms_tariff = CampaignBriefAnalysis(
        goal="апсейл", product="Тариф Семейный", channels=["sms"],
        audience={"description": "x"}, scenario="upsell",
        missing_critical=[], confidence=0.9, notes=[],
    )
    assert asyncio.run(agent_builder._matches_upsell_with_reminder(upsell_sms_tariff, _ctx(tg))) is True

    # Без ТГ — не матчится.
    assert asyncio.run(agent_builder._matches_upsell_with_reminder(upsell_sms_tariff, _ctx([]))) is False

    # Канал email вместо sms — не матчится.
    no_sms = CampaignBriefAnalysis(
        goal="апсейл", product="Тариф Семейный", channels=["email"],
        audience={"description": "x"}, scenario="upsell",
        missing_critical=[], confidence=0.9, notes=[],
    )
    assert asyncio.run(agent_builder._matches_upsell_with_reminder(no_sms, _ctx(tg))) is False

    # Goal=onboarding вместо апсейла — не матчится.
    not_upsell = CampaignBriefAnalysis(
        goal="onboarding новых SIM", product="Тариф Семейный", channels=["sms"],
        audience={"description": "x"}, scenario="onboarding",
        missing_critical=[], confidence=0.9, notes=[],
    )
    assert asyncio.run(agent_builder._matches_upsell_with_reminder(not_upsell, _ctx(tg))) is False
