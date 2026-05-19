from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest


@dataclass
class SeededHealth:
    attention_score: int
    severity: str
    issues_json: list[dict[str, Any]]
    recommended_actions_json: list[dict[str, Any]]


@dataclass
class SeededCampaign:
    id: int
    name: str
    budget: int
    spent: int
    open_rate: int
    click_rate: int
    conversion_rate: int
    updated_at: datetime
    health: SeededHealth | None


@pytest.fixture()
def seeded_campaigns() -> list[SeededCampaign]:
    now = datetime.now(UTC)
    return [
        SeededCampaign(
            id=201,
            name="Retention SMS",
            budget=1000,
            spent=970,
            open_rate=22,
            click_rate=8,
            conversion_rate=5,
            updated_at=now,
            health=SeededHealth(
                attention_score=38,
                severity="critical",
                issues_json=[{"issue": "CTR drop"}],
                recommended_actions_json=[{"action": "Rotate creatives"}],
            ),
        ),
        SeededCampaign(
            id=202,
            name="Family Upsell Push",
            budget=1400,
            spent=880,
            open_rate=35,
            click_rate=16,
            conversion_rate=12,
            updated_at=now,
            health=SeededHealth(
                attention_score=72,
                severity="medium",
                issues_json=[{"issue": "Open rate plateau"}],
                recommended_actions_json=[{"action": "Refine send time"}],
            ),
        ),
    ]


class InMemoryChatStore:
    def __init__(self):
        self.sessions: set[str] = set()
        self.events: dict[str, list[Any]] = {}
        self.messages: list[dict[str, Any]] = []
        self.artifacts: list[dict[str, Any]] = []

    async def ensure_chat_session(self, *, session_id: str):
        self.sessions.add(session_id)

    async def add_chat_message(self, *, session_id: str, role: str, content: str, metadata: dict[str, Any] | None = None):
        self.messages.append({"session_id": session_id, "role": role, "content": content, "metadata": metadata or {}})

    async def create_chat_run(self, *, session_id: str, user_message: str):
        run_id = f"run-{uuid4()}"
        self.events[run_id] = []
        return run_id

    async def add_chat_run_event(self, *, run_id: str, event: str, detail: str | None = None, metadata: dict[str, Any] | None = None, status: str = "info"):
        from schemas import ChatTraceEvent

        self.events[run_id].append(ChatTraceEvent(event=event, status=status, detail=detail, metadata=metadata or {}))

    async def complete_chat_run(self, *, run_id: str, status: str):
        return None

    async def list_chat_run_events(self, *, run_id: str):
        return self.events[run_id]

    async def save_artifact(self, *, session_id: str, artifact_type: str, schema_version: int, content_json: dict[str, Any] | None, metadata_json: dict[str, Any] | None, source_run_id: str | None):
        artifact_id = f"artifact-{len(self.artifacts)+1}"
        self.artifacts.append({"id": artifact_id, "session_id": session_id, "artifact_type": artifact_type, "content": content_json or {}, "metadata": metadata_json or {}, "source_run_id": source_run_id})
        return artifact_id
