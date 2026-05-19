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


def test_chat_save_campaign_creates_artifact_and_action(monkeypatch):
    from conftest import InMemoryChatStore

    store = InMemoryChatStore()
    monkeypatch.setattr(app_module, "session_store", store)
    client = TestClient(app_module.app)

    response = client.post("/api/chat", json={
        "session_id": "chat-1",
        "message": "Сохрани черновик",
        "action": {
            "id": "save_campaign",
            "label": "Сохранить",
            "payload": {"content_json": {"name": "Draft A"}, "metadata_json": {"version": 1}},
        },
    })
    assert response.status_code == 200
    body = response.json()
    assert body["artifacts"][0]["type"] == "campaign_draft"
    assert body["actions_available"][0]["id"] == "open_artifact"
