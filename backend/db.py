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

        def _campaign_state_columns(sync_connection) -> set[str]:
            inspector = inspect(sync_connection)
            if not inspector.has_table("campaign_states"):
                return set()
            return {column["name"] for column in inspector.get_columns("campaign_states")}

        columns = await connection.run_sync(_campaign_state_columns)
        column_migrations = {
            "draft_flow_version": "ALTER TABLE campaign_states ADD COLUMN draft_flow_version INTEGER",
            "campaign_brief_json": "ALTER TABLE campaign_states ADD COLUMN campaign_brief_json JSON",
            "brief_completeness_json": "ALTER TABLE campaign_states ADD COLUMN brief_completeness_json JSON",
            "review_checklist_json": "ALTER TABLE campaign_states ADD COLUMN review_checklist_json JSON",
            "review_status": "ALTER TABLE campaign_states ADD COLUMN review_status VARCHAR(32)",
            "review_checklist_acknowledged": (
                "ALTER TABLE campaign_states ADD COLUMN review_checklist_acknowledged BOOLEAN NOT NULL DEFAULT 0"
            ),
        }
        for column_name, statement in column_migrations.items():
            if columns and column_name not in columns:
                await connection.execute(text(statement))


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
        status: str = "collect_brief",
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
        status: str = "collect_brief",
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
        runtime_status: str = "collect_brief",
        draft_flow_version: int | None = None,
        campaign_brief_json: dict[str, Any] | None = None,
        brief_completeness_json: dict[str, Any] | None = None,
        review_checklist_json: dict[str, Any] | None = None,
        review_status: str | None = None,
        review_checklist_acknowledged: bool = False,
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
                    campaign_brief_json=campaign_brief_json,
                    brief_completeness_json=brief_completeness_json,
                    review_checklist_json=review_checklist_json,
                    review_status=review_status,
                    review_checklist_acknowledged=review_checklist_acknowledged,
                    runtime_status=runtime_status,
                    created_at=now,
                    updated_at=now,
                )
                db.add(state)
            else:
                state.campaign_id = campaign_id
                state.draft_flow_json = draft_flow_json
                state.draft_flow_version = draft_flow_version
                state.campaign_brief_json = campaign_brief_json
                state.brief_completeness_json = brief_completeness_json
                state.review_checklist_json = review_checklist_json
                state.review_status = review_status
                state.review_checklist_acknowledged = review_checklist_acknowledged
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
            campaign_brief=state.campaign_brief_json if state is not None else None,
            draft_flow=state.draft_flow_json if state is not None else None,
            draft_flow_version=state.draft_flow_version if state is not None else None,
            brief_completeness=state.brief_completeness_json if state is not None else None,
            review_checklist=state.review_checklist_json if state is not None else None,
            review_status=(state.review_status if state is not None and state.review_status else "blocked"),
            review_checklist_acknowledged=(state.review_checklist_acknowledged if state is not None else False),
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
