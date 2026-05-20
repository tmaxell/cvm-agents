"""Async DB setup + единый репозиторий для unified chat (sessions, messages, runs, artifacts)."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from models import (
    Base,
    ChatMessageModel,
    ChatRunEventModel,
    ChatRunModel,
    ChatSessionModel,
    SavedArtifactModel,
)
from schemas import ChatTraceEvent

_DEFAULT_SQLITE_PATH = Path(__file__).parent / "data" / "cvm_agents.sqlite3"

# Все легаси-таблицы Builder-сессий + старая chat_sessions с FK на sessions — дропаем при init.
_LEGACY_TABLES = ("messages", "campaign_states", "sessions")


def get_database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return database_url
    _DEFAULT_SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite+aiosqlite:///{_DEFAULT_SQLITE_PATH}"


engine = create_async_engine(get_database_url(), pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Создаёт таблицы и дропает легаси, если они есть."""
    async with engine.begin() as connection:
        # Drop legacy tables first (chat_sessions FK might reference sessions, drop in dep order).
        for table in ("chat_sessions",):
            await connection.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
        for table in _LEGACY_TABLES:
            await connection.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
        # Drop chat_messages/runs/events/artifacts too — they referenced the old chat_sessions FK.
        for table in ("chat_run_events", "chat_runs", "saved_artifacts", "chat_messages"):
            await connection.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))

        await connection.run_sync(Base.metadata.create_all)


# ── Repository ────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(UTC)


def _preview(text_content: str, limit: int = 200) -> str:
    cleaned = " ".join(text_content.split())
    return cleaned[:limit]


class ChatStore:
    """Единый репозиторий для unified chat виджета."""

    _SUPPORTED_ARTIFACT_TYPES = {
        "draft_flow",
        "campaign_draft",
        "segment_draft",
        "target_group_draft",
        "monitor_report",
        "recommendation_bundle",
        "attention_report",
    }

    # ── Sessions ──────────────────────────────────────────────────────────────

    async def list_sessions(self) -> list[dict[str, Any]]:
        async with session_scope() as db:
            result = await db.scalars(
                select(ChatSessionModel).order_by(ChatSessionModel.updated_at.desc())
            )
            return [self._session_dict(s) for s in result]

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        async with session_scope() as db:
            session = await db.get(ChatSessionModel, session_id)
            if session is None:
                return None
            return self._session_dict(session)

    async def get_session_with_messages(self, session_id: str) -> dict[str, Any] | None:
        async with session_scope() as db:
            session = await db.get(ChatSessionModel, session_id)
            if session is None:
                return None
            messages = await db.scalars(
                select(ChatMessageModel)
                .where(ChatMessageModel.session_id == session_id)
                .order_by(ChatMessageModel.created_at)
            )
            artifacts = await db.scalars(
                select(SavedArtifactModel)
                .where(SavedArtifactModel.session_id == session_id)
                .order_by(SavedArtifactModel.created_at)
            )
            payload = self._session_dict(session)
            payload["messages"] = [self._message_dict(m) for m in messages]
            payload["artifacts"] = [self._artifact_dict(a) for a in artifacts]
            return payload

    async def list_messages(self, session_id: str) -> list[dict[str, Any]]:
        async with session_scope() as db:
            result = await db.scalars(
                select(ChatMessageModel)
                .where(ChatMessageModel.session_id == session_id)
                .order_by(ChatMessageModel.created_at)
            )
            return [self._message_dict(m) for m in result]

    async def ensure_session(
        self,
        *,
        session_id: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        async with session_scope() as db:
            sid = session_id or str(uuid4())
            session = await db.get(ChatSessionModel, sid)
            if session is None:
                session = ChatSessionModel(
                    id=sid,
                    title=title or "Новый диалог",
                )
                db.add(session)
                await db.flush()
            elif title and session.title in (None, "", "Новый диалог"):
                session.title = title
            return self._session_dict(session)

    async def update_session_title(self, session_id: str, title: str) -> None:
        async with session_scope() as db:
            session = await db.get(ChatSessionModel, session_id)
            if session is not None and session.title != title:
                session.title = title

    async def add_message(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        async with session_scope() as db:
            session = await db.get(ChatSessionModel, session_id)
            if session is None:
                raise KeyError(session_id)
            msg_id = str(uuid4())
            msg = ChatMessageModel(
                id=msg_id,
                session_id=session_id,
                role=role,
                content=content,
                metadata_json=metadata,
                created_at=_now(),
            )
            session.updated_at = _now()
            session.last_message_preview = _preview(content)
            # Авто-генерация title из первого user-сообщения, если стандартное.
            if role == "user" and session.title in (None, "", "Новый диалог"):
                session.title = _preview(content, limit=60)
            db.add(msg)
            await db.flush()
            return msg_id

    # ── Runs / trace ──────────────────────────────────────────────────────────

    async def create_run(self, *, session_id: str, user_message: str | None = None) -> str:
        async with session_scope() as db:
            run_id = str(uuid4())
            db.add(ChatRunModel(
                id=run_id,
                session_id=session_id,
                user_message=user_message,
                status="running",
                created_at=_now(),
                updated_at=_now(),
            ))
            await db.flush()
            return run_id

    async def add_event(
        self,
        *,
        run_id: str,
        event: str,
        status: str = "info",
        detail: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        async with session_scope() as db:
            run = await db.get(ChatRunModel, run_id)
            if run is None:
                return
            run.updated_at = _now()
            db.add(ChatRunEventModel(
                id=str(uuid4()),
                run_id=run_id,
                event=event,
                status=status,
                detail=detail,
                metadata_json=metadata,
                created_at=_now(),
            ))

    async def complete_run(self, *, run_id: str, status: str, intent: str | None = None) -> None:
        async with session_scope() as db:
            run = await db.get(ChatRunModel, run_id)
            if run is None:
                return
            run.status = status
            if intent:
                run.intent = intent
            run.updated_at = _now()

    async def list_events(self, *, run_id: str) -> list[ChatTraceEvent]:
        async with session_scope() as db:
            result = await db.scalars(
                select(ChatRunEventModel)
                .where(ChatRunEventModel.run_id == run_id)
                .order_by(ChatRunEventModel.created_at)
            )
            return [
                ChatTraceEvent(
                    event=item.event,
                    status=item.status,  # type: ignore[arg-type]
                    detail=item.detail,
                    ts=item.created_at,
                    metadata=item.metadata_json or {},
                )
                for item in result
            ]

    # ── Artifacts ─────────────────────────────────────────────────────────────

    async def save_artifact(
        self,
        *,
        session_id: str,
        artifact_type: str,
        content_json: dict[str, Any] | None,
        metadata_json: dict[str, Any] | None = None,
        schema_version: int = 1,
        source_run_id: str | None = None,
    ) -> str:
        if artifact_type not in self._SUPPORTED_ARTIFACT_TYPES:
            raise ValueError(f"Unsupported artifact_type: {artifact_type}")
        h = hashlib.sha256(
            json.dumps(content_json or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        async with session_scope() as db:
            session = await db.get(ChatSessionModel, session_id)
            if session is None:
                raise KeyError(session_id)
            if source_run_id:
                existing = await db.scalar(
                    select(SavedArtifactModel).where(
                        SavedArtifactModel.source_run_id == source_run_id,
                        SavedArtifactModel.artifact_hash == h,
                    )
                )
                if existing is not None:
                    return existing.id
            art_id = str(uuid4())
            db.add(SavedArtifactModel(
                id=art_id,
                session_id=session_id,
                source_run_id=source_run_id,
                artifact_type=artifact_type,
                schema_version=schema_version,
                content_json=content_json,
                metadata_json=metadata_json,
                artifact_hash=h,
                created_at=_now(),
            ))
            await db.flush()
            return art_id

    async def list_artifacts(self, *, session_id: str) -> list[dict[str, Any]]:
        async with session_scope() as db:
            result = await db.scalars(
                select(SavedArtifactModel)
                .where(SavedArtifactModel.session_id == session_id)
                .order_by(SavedArtifactModel.created_at)
            )
            return [self._artifact_dict(a) for a in result]

    async def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        async with session_scope() as db:
            artifact = await db.get(SavedArtifactModel, artifact_id)
            return self._artifact_dict(artifact) if artifact else None

    async def set_campaign_id(self, *, session_id: str, campaign_id: int | None) -> None:
        async with session_scope() as db:
            session = await db.get(ChatSessionModel, session_id)
            if session is not None:
                session.campaign_id = campaign_id
                session.updated_at = _now()

    # ── Serializers ───────────────────────────────────────────────────────────

    @staticmethod
    def _session_dict(s: ChatSessionModel) -> dict[str, Any]:
        return {
            "id": s.id,
            "title": s.title or "Новый диалог",
            "status": s.status or "active",
            "campaign_id": s.campaign_id,
            "last_message_preview": s.last_message_preview or "",
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "updated_at": s.updated_at.isoformat() if s.updated_at else None,
        }

    @staticmethod
    def _message_dict(m: ChatMessageModel) -> dict[str, Any]:
        return {
            "id": m.id,
            "session_id": m.session_id,
            "role": m.role,
            "content": m.content,
            "metadata": m.metadata_json or {},
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }

    @staticmethod
    def _artifact_dict(a: SavedArtifactModel) -> dict[str, Any]:
        return {
            "id": a.id,
            "session_id": a.session_id,
            "type": a.artifact_type,
            "schema_version": a.schema_version,
            "content": a.content_json,
            "metadata": a.metadata_json or {},
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
