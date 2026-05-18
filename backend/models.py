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
    runtime_status: Mapped[str] = mapped_column(String(32), nullable=False, default="collect_brief")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    session: Mapped[BuilderSessionModel] = relationship(back_populates="campaign_state")
