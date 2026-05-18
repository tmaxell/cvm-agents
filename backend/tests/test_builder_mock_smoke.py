import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

import types

_fake_db = types.ModuleType("db")


class _PlaceholderDatabaseSessionStore:
    pass


async def _fake_init_db():
    return None


_fake_db.DatabaseSessionStore = _PlaceholderDatabaseSessionStore
_fake_db.init_db = _fake_init_db
sys.modules.setdefault("db", _fake_db)

import app as app_module
from schemas import Message, Session, SessionDetail
from tools import mock_data


class SegmentSmokeFakeLLM:
    async def ainvoke(self, messages):
        return SimpleNamespace(content=json.dumps({
            "hypotheses": [
                {
                    "name": "Семейные пользователи с потенциалом апсейла",
                    "audience_description": "Клиенты семейных тарифов, которым релевантен переход на Family Max.",
                    "relevance_reason": "Сегмент совпадает с целью апсейла семейной аудитории.",
                    "selection_criteria": {"tariff.family": "Family", "arpu_band": "700-1500₽"},
                    "risk_or_limitation": "Нужно отдельно исключить opt-out и клиентов с контактом за последние 7 дней.",
                    "matched_target_group": {"id": 105, "name": "Утилизаторы пакета данных (≥80%)"},
                    "is_existing_target_group": True,
                    "segment_source": "existing_target_group",
                    "demo_insight": "Совпадение с mock Target Group для smoke-теста.",
                    "estimated_reach_label": "Средний",
                    "confidence": 0.86,
                },
                {
                    "name": "Мульти-SIM домохозяйства",
                    "audience_description": "Демо-сегмент клиентов с несколькими SIM в семье.",
                    "relevance_reason": "Может расширить семейный тариф.",
                    "selection_criteria": {"demo_signal": "multi_sim_household"},
                    "risk_or_limitation": "Это только рекомендация; контактность и opt-out требуют отдельной проверки.",
                    "matched_target_group": None,
                    "is_existing_target_group": False,
                    "segment_source": "llm_composed_demo",
                    "demo_insight": "Сформировано по mock contact-base profile.",
                    "estimated_reach_label": "Низкий",
                    "confidence": 0.71,
                },
            ]
        }, ensure_ascii=False))


class DraftGraph:
    async def ainvoke(self, payload):
        return {
            "messages": [AIMessage(content="Draft кампании собран.")],
            "campaign_id": None,
            "last_flow_json": payload["last_flow_json"],
        }


class AsyncInMemorySessionStore:
    def __init__(self):
        self.sessions = {}
        self.messages = []
        self.states = {}

    async def list_sessions(self):
        sessions = [self._to_session(session) for session in self.sessions.values()]
        return sorted(sessions, key=lambda session: session.updated_at, reverse=True)

    async def get_session(self, session_id):
        session = self.sessions.get(session_id)
        if session is None:
            return None
        messages = [message for message in self.messages if message.session_id == session_id]
        state = self.states.get(session_id, {})
        return SessionDetail(**{**session, **state}, messages=messages)

    async def ensure_session(self, *, session_id, title, campaign_id=None, status="collect_brief"):
        if session_id and session_id in self.sessions:
            return self._to_session(self.sessions[session_id])
        now = datetime.now(UTC)
        new_session = {
            "id": session_id or str(uuid4()),
            "campaign_id": campaign_id,
            "title": title,
            "created_at": now,
            "updated_at": now,
            "status": status,
        }
        self.sessions[new_session["id"]] = new_session
        return self._to_session(new_session)

    async def add_message(self, *, session_id, role, content, metadata=None):
        if session_id not in self.sessions:
            raise KeyError(session_id)
        message = Message(
            id=str(uuid4()),
            session_id=session_id,
            role=role,
            content=content,
            created_at=datetime.now(UTC),
            metadata=metadata,
        )
        self.messages.append(message)
        self.sessions[session_id]["updated_at"] = message.created_at
        if metadata:
            if "campaign_id" in metadata:
                self.sessions[session_id]["campaign_id"] = metadata["campaign_id"]
            if metadata.get("status"):
                self.sessions[session_id]["status"] = metadata["status"]
        return message

    async def update_session(self, session_id, *, campaign_id=None, status=None, title=None):
        session = self.sessions.get(session_id)
        if session is None:
            return None
        if campaign_id is not None:
            session["campaign_id"] = campaign_id
        if status is not None:
            session["status"] = status
        if title:
            session["title"] = title
        session["updated_at"] = datetime.now(UTC)
        return self._to_session(session)

    async def upsert_campaign_state(
        self,
        *,
        session_id,
        campaign_id=None,
        draft_flow_json=None,
        runtime_status="editing",
        draft_flow_version=None,
        campaign_brief_json=None,
        brief_completeness_json=None,
        review_checklist_json=None,
        review_status=None,
        review_checklist_acknowledged=False,
    ):
        if session_id not in self.sessions:
            raise KeyError(session_id)
        self.states[session_id] = {
            "campaign_brief": campaign_brief_json,
            "draft_flow": draft_flow_json,
            "draft_flow_version": draft_flow_version,
            "brief_completeness": brief_completeness_json,
            "review_checklist": review_checklist_json,
            "review_status": review_status or "blocked",
            "review_checklist_acknowledged": review_checklist_acknowledged,
        }
        if campaign_id is not None:
            self.sessions[session_id]["campaign_id"] = campaign_id
        self.sessions[session_id]["updated_at"] = datetime.now(UTC)

    @staticmethod
    def _to_session(session):
        return Session(**session)


def _assert_not_500(response):
    assert response.status_code < 500, response.text


def _selected_segment_from_hypothesis(hypothesis):
    return {
        "hypothesis": hypothesis,
        "selection_criteria": hypothesis["selection_criteria"],
        "matched_target_group": hypothesis["matched_target_group"],
        "is_existing_target_group": hypothesis["is_existing_target_group"],
        "risk_or_limitation": hypothesis["risk_or_limitation"],
        "recommendationOnly": False,
    }


def test_builder_mock_mode_full_happy_path_smoke(monkeypatch):
    from agents import campaign_builder, segment_agent

    async def fake_list_target_groups():
        return mock_data.MOCK_TARGET_GROUPS

    async def fake_list_channels():
        return mock_data.MOCK_CHANNELS

    async def fake_list_events():
        return mock_data.MOCK_EVENTS

    async def fake_list_offer_templates():
        return mock_data.MOCK_OFFER_TEMPLATES

    async def fake_create_campaign(_flow):
        return {**mock_data.make_mock_campaign_result(), "campaignId": 777001}

    monkeypatch.setattr(app_module, "session_store", AsyncInMemorySessionStore())
    monkeypatch.setattr(segment_agent, "get_llm", lambda for_tools=False: SegmentSmokeFakeLLM())
    monkeypatch.setattr(segment_agent.adtarget, "list_target_groups", fake_list_target_groups)
    monkeypatch.setattr(campaign_builder.adtarget, "list_target_groups", fake_list_target_groups)
    monkeypatch.setattr(campaign_builder.adtarget, "list_channels", fake_list_channels)
    monkeypatch.setattr(campaign_builder.adtarget, "list_events", fake_list_events)
    monkeypatch.setattr(campaign_builder.adtarget, "list_offer_templates", fake_list_offer_templates)
    monkeypatch.setattr(campaign_builder, "get_graph", lambda: DraftGraph())
    monkeypatch.setattr(app_module.adtarget, "create_campaign", fake_create_campaign)

    client = TestClient(app_module.app)
    segment_response = client.post(
        "/api/segments/suggest",
        json={
            "product": "Тариф Family Max",
            "campaign_goal": "Апсейл семейной аудитории",
            "audience_constraints": {
                "note": "Исключить opt-out и клиентов с контактом за последние 7 дней"
            },
        },
    )
    _assert_not_500(segment_response)
    assert segment_response.status_code == 200
    segment_body = segment_response.json()
    selected_segment = _selected_segment_from_hypothesis(segment_body["hypotheses"][0])

    campaign_brief = {
        "product": "Тариф Family Max",
        "goal": "Апсейл семейной аудитории",
        "audience": {
            "target_groups": ["Target Group #105"],
            "description": "Выбранный сегмент из Audience Builder",
            "selected_segment": selected_segment,
        },
        "channels": [{"name": "SMS", "channel_id": 1, "content_type": "SmsContent"}],
        "constraints": {
            "content": "Подключите Family Max для всей семьи на выгодных условиях.",
            "offer_recommendations": "Апсейл семейного тарифа",
        },
    }

    builder_response = client.post(
        "/api/builder",
        json={
            "goal": "Собери кампанию для апсейла Family Max по выбранному сегменту",
            "campaign_brief": campaign_brief,
        },
    )
    _assert_not_500(builder_response)
    assert builder_response.status_code == 200
    builder_body = builder_response.json()
    assert builder_body["session_id"]
    assert builder_body["draft_flow"]
    assert builder_body["draft_flow_version"] == 1

    sessions_response = client.get("/api/sessions")
    _assert_not_500(sessions_response)
    assert sessions_response.status_code == 200
    assert any(item["id"] == builder_body["session_id"] for item in sessions_response.json())

    session_response = client.get(f"/api/sessions/{builder_body['session_id']}")
    _assert_not_500(session_response)
    assert session_response.status_code == 200
    session_body = session_response.json()
    assert session_body["draft_flow_version"] == builder_body["draft_flow_version"]
    assert session_body["campaign_brief"]["audience"]["selected_segment"]["matched_target_group"]["target_group_id"] == 105

    create_response = client.post(
        "/api/builder/create",
        json={
            "session_id": builder_body["session_id"],
            "draft_flow": builder_body["draft_flow"],
            "draft_flow_version": builder_body["draft_flow_version"],
            "campaign_brief": campaign_brief,
            "review_checklist_acknowledged": True,
        },
    )
    _assert_not_500(create_response)
    assert create_response.status_code == 200
    create_body = create_response.json()
    assert create_body["campaign_id"] == 777001
    assert create_body["status"] == "created_in_adtarget"

    stale_create_response = client.post(
        "/api/builder/create",
        json={
            "session_id": builder_body["session_id"],
            "draft_flow": builder_body["draft_flow"],
            "draft_flow_version": builder_body["draft_flow_version"] + 1,
            "campaign_brief": campaign_brief,
            "review_checklist_acknowledged": True,
        },
    )
    _assert_not_500(stale_create_response)
    assert stale_create_response.status_code == 409
    detail = stale_create_response.json()["detail"]
    assert detail["message"] == "Draft flow version is stale"
    assert detail["expected_draft_flow_version"] == builder_body["draft_flow_version"]
    assert detail["received_draft_flow_version"] == builder_body["draft_flow_version"] + 1
