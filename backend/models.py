"""SQLAlchemy models for persistent Campaign Builder storage."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for backend database models."""


class BuilderSessionModel(Base):
    """A Campaign Builder dialog session."""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    campaign_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="collect_brief")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    messages: Mapped[list["BuilderMessageModel"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="BuilderMessageModel.created_at"
    )
    campaign_state: Mapped["CampaignStateModel | None"] = relationship(
        back_populates="session", cascade="all, delete-orphan", uselist=False
    )


class BuilderMessageModel(Base):
    """One user or assistant message in a Campaign Builder session."""

    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON, nullable=True)

    session: Mapped[BuilderSessionModel] = relationship(back_populates="messages")


class CampaignStateModel(Base):
    """Latest persisted runtime state for a Builder-generated campaign."""

    __tablename__ = "campaign_states"

    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), primary_key=True)
    campaign_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    draft_flow_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    draft_flow_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    campaign_brief_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    brief_completeness_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    review_checklist_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    review_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    review_checklist_acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    runtime_status: Mapped[str] = mapped_column(String(32), nullable=False, default="editing")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    session: Mapped[BuilderSessionModel] = relationship(back_populates="campaign_state")


class ChatRunModel(Base):
    """One logical /api/chat run bound to a UI session id."""

    __tablename__ = "chat_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    user_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    events: Mapped[list["ChatRunEventModel"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="ChatRunEventModel.created_at"
    )


class ChatRunEventModel(Base):
    """Trace event stored for one chat run step/tool operation."""

    __tablename__ = "chat_run_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("chat_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    event: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="info")
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    run: Mapped[ChatRunModel] = relationship(back_populates="events")


class ChatSessionModel(Base):
    """Unified chat session used by /api/chat and follow-up history retrieval."""

    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    builder_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    messages: Mapped[list["ChatMessageModel"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="ChatMessageModel.created_at"
    )
    runs: Mapped[list[ChatRunModel]] = relationship(cascade="all, delete-orphan")


class ChatMessageModel(Base):
    """One message of unified chat history."""

    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON, nullable=True)

    session: Mapped[ChatSessionModel] = relationship(back_populates="messages")


class SavedArtifactModel(Base):
    """Persisted artifact generated inside unified chat runs."""

    __tablename__ = "saved_artifacts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[str | None] = mapped_column(ForeignKey("chat_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DemoCampaignModel(Base):
    """Demo campaigns used for monitoring and local development seed data."""

    __tablename__ = "demo_campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(64), nullable=False)
    audience_size: Mapped[int] = mapped_column(Integer, nullable=False)
    budget: Mapped[int] = mapped_column(Integer, nullable=False)
    spent: Mapped[int] = mapped_column(Integer, nullable=False)
    open_rate: Mapped[int] = mapped_column(Integer, nullable=False)
    click_rate: Mapped[int] = mapped_column(Integer, nullable=False)
    conversion_rate: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    health: Mapped["CampaignHealthModel | None"] = relationship(
        back_populates="campaign", cascade="all, delete-orphan", uselist=False
    )


class CampaignHealthModel(Base):
    """Health diagnostics snapshot for demo campaigns."""

    __tablename__ = "campaign_health"

    campaign_id: Mapped[int] = mapped_column(ForeignKey("demo_campaigns.id", ondelete="CASCADE"), primary_key=True)
    attention_score: Mapped[int] = mapped_column(Integer, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    issues_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    recommended_actions_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    last_checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    campaign: Mapped[DemoCampaignModel] = relationship(back_populates="health")
