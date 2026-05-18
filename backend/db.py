"""Async database setup and repository helpers for Campaign Builder state."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import NO_VALUE

from models import Base, BuilderMessageModel, BuilderSessionModel, CampaignStateModel
from schemas import Message, Session, SessionDetail

_DEFAULT_SQLITE_PATH = Path(__file__).parent / "data" / "cvm_agents.sqlite3"


def get_database_url() -> str:
    """Return configured database URL, falling back to local SQLite for demo mode."""
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return database_url
    _DEFAULT_SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite+aiosqlite:///{_DEFAULT_SQLITE_PATH}"


engine = create_async_engine(get_database_url(), pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Provide a transactional async SQLAlchemy session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Create tables if migrations have not been run yet."""
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

        def _has_campaign_state_version_column(sync_connection) -> bool:
            inspector = inspect(sync_connection)
            if not inspector.has_table("campaign_states"):
                return True
            return any(
                column["name"] == "draft_flow_version"
                for column in inspector.get_columns("campaign_states")
            )

        has_version_column = await connection.run_sync(_has_campaign_state_version_column)
        if not has_version_column:
            await connection.execute(text("ALTER TABLE campaign_states ADD COLUMN draft_flow_version INTEGER"))


class DatabaseSessionStore:
    """SQL-backed repository for builder sessions, messages, and campaign state."""

    async def list_sessions(self) -> list[Session]:
        async with session_scope() as db:
            result = await db.scalars(
                select(BuilderSessionModel)
                .options(selectinload(BuilderSessionModel.campaign_state))
                .order_by(BuilderSessionModel.updated_at.desc())
            )
            return [self._to_session(item) for item in result]

    async def get_session(self, session_id: str) -> SessionDetail | None:
        async with session_scope() as db:
            result = await db.scalars(
                select(BuilderSessionModel)
                .where(BuilderSessionModel.id == session_id)
                .options(selectinload(BuilderSessionModel.messages), selectinload(BuilderSessionModel.campaign_state))
            )
            session = result.first()
            if session is None:
                return None
            messages = [self._to_message(message) for message in sorted(session.messages, key=lambda item: item.created_at)]
            return SessionDetail(**self._to_session(session).model_dump(), messages=messages)

    async def create_session(
        self,
        *,
        title: str,
        campaign_id: int | None = None,
        status: str = "in_progress",
        session_id: str | None = None,
    ) -> Session:
        async with session_scope() as db:
            if session_id:
                existing = await db.get(BuilderSessionModel, session_id)
                if existing is not None:
                    return self._to_session(existing)
            now = self._now()
            session = BuilderSessionModel(
                id=session_id or str(uuid4()),
                campaign_id=campaign_id,
                title=title,
                status=status,
                created_at=now,
                updated_at=now,
            )
            db.add(session)
            await db.flush()
            return self._to_session(session)

    async def ensure_session(
        self,
        *,
        session_id: str | None,
        title: str,
        campaign_id: int | None = None,
        status: str = "in_progress",
    ) -> Session:
        if session_id:
            existing = await self.get_session(session_id)
            if existing is not None:
                return Session(**existing.model_dump(exclude={"messages"}))
        return await self.create_session(title=title, campaign_id=campaign_id, status=status, session_id=session_id)

    async def add_message(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> Message:
        async with session_scope() as db:
            session = await db.get(BuilderSessionModel, session_id)
            if session is None:
                raise KeyError(session_id)
            now = self._now()
            message = BuilderMessageModel(
                id=str(uuid4()),
                session_id=session_id,
                role=role,
                content=content,
                created_at=now,
                metadata_json=metadata,
            )
            session.updated_at = now
            if metadata:
                campaign_id = metadata.get("campaign_id")
                status = metadata.get("status")
                if campaign_id is not None:
                    session.campaign_id = campaign_id
                if status:
                    session.status = status
            db.add(message)
            await db.flush()
            return self._to_message(message)

    async def update_session(
        self,
        session_id: str,
        *,
        campaign_id: int | None = None,
        status: str | None = None,
        title: str | None = None,
    ) -> Session | None:
        async with session_scope() as db:
            session = await db.get(BuilderSessionModel, session_id)
            if session is None:
                return None
            if campaign_id is not None:
                session.campaign_id = campaign_id
            if status is not None:
                session.status = status
            if title:
                session.title = title
            session.updated_at = self._now()
            await db.flush()
            return self._to_session(session)

    async def upsert_campaign_state(
        self,
        *,
        session_id: str,
        campaign_id: int | None = None,
        draft_flow_json: dict[str, Any] | None = None,
        runtime_status: str = "in_progress",
        draft_flow_version: int | None = None,
    ) -> None:
        async with session_scope() as db:
            session = await db.get(BuilderSessionModel, session_id)
            if session is None:
                raise KeyError(session_id)
            state = await db.get(CampaignStateModel, session_id)
            now = self._now()
            if state is None:
                state = CampaignStateModel(
                    session_id=session_id,
                    campaign_id=campaign_id,
                    draft_flow_json=draft_flow_json,
                    draft_flow_version=draft_flow_version,
                    runtime_status=runtime_status,
                    created_at=now,
                    updated_at=now,
                )
                db.add(state)
            else:
                state.campaign_id = campaign_id
                state.draft_flow_json = draft_flow_json
                state.draft_flow_version = draft_flow_version
                state.runtime_status = runtime_status
                state.updated_at = now
            if campaign_id is not None:
                session.campaign_id = campaign_id
            session.status = runtime_status
            session.updated_at = now

    @staticmethod
    def _to_session(session: BuilderSessionModel) -> Session:
        state_value = inspect(session).attrs.campaign_state.loaded_value
        state = None if state_value is NO_VALUE else state_value
        return Session(
            id=session.id,
            campaign_id=session.campaign_id,
            title=session.title,
            created_at=session.created_at,
            updated_at=session.updated_at,
            status=session.status,
            draft_flow_version=(
                state.draft_flow_version
                if state is not None
                else None
            ),
        )

    @staticmethod
    def _to_message(message: BuilderMessageModel) -> Message:
        return Message(
            id=message.id,
            session_id=message.session_id,
            role=message.role,
            content=message.content,
            created_at=message.created_at,
            metadata=message.metadata_json,
        )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)
