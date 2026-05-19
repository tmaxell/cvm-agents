import sys
from pathlib import Path

import types
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

_fake_db = types.ModuleType("db")
_fake_db.DatabaseSessionStore = object

async def _fake_init_db():
    return None

_fake_db.init_db = _fake_init_db
sys.modules.setdefault("db", _fake_db)

import app as app_module


def test_chat_trace_events_match_expected_mvp_sequence(monkeypatch):
    from conftest import InMemoryChatStore

    store = InMemoryChatStore()
    monkeypatch.setattr(app_module, "session_store", store)
    client = TestClient(app_module.app)

    response = client.post("/api/chat", json={"session_id": "chat-2", "message": "Открой билдер"})
    assert response.status_code == 200
    trace_events = [event["event"] for event in response.json()["trace"]]
    assert trace_events == [
        "route_selected",
        "plan_created",
        "step_started",
        "tool_called",
        "tool_result",
        "step_completed",
        "run_completed",
    ]
