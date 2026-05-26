"""SQLAlchemy models — единая chat-схема для unified chat виджета."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base for backend ORM models."""


# ── Unified chat ──────────────────────────────────────────────────────────────

class ChatSessionModel(Base):
    """Единая chat-сессия, которую видит виджет в истории."""

    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="Новый диалог")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    campaign_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    last_message_preview: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    messages: Mapped[list["ChatMessageModel"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="ChatMessageModel.created_at"
    )


class ChatMessageModel(Base):
    """Одно сообщение чата (user/assistant)."""

    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    session: Mapped[ChatSessionModel] = relationship(back_populates="messages")


class ChatRunModel(Base):
    """Логический run одного запроса /api/chat → исполнение агента."""

    __tablename__ = "chat_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    user_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    intent: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    events: Mapped[list["ChatRunEventModel"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="ChatRunEventModel.created_at"
    )


class ChatRunEventModel(Base):
    """Trace-событие плана: route_selected, plan_created, step_started/completed, tool_called/result."""

    __tablename__ = "chat_run_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("chat_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    event: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="info")
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    run: Mapped[ChatRunModel] = relationship(back_populates="events")


class SavedArtifactModel(Base):
    """Сохранённый артефакт чата: draft_flow, segment, campaign_draft и т.д."""

    __tablename__ = "saved_artifacts"
    __table_args__ = (UniqueConstraint("source_run_id", "artifact_hash", name="uq_saved_artifacts_source_hash"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("chat_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    artifact_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# ── Demo campaigns (для campaign_attention отчёта) ────────────────────────────
# Метрики оперируют тем же контуром, что показан на дашбордах платформы AdTarget
# (см. examples/01-06 - Dashboards*): доставка сообщений, задержки, тайм-ауты
# событий/откликов, очереди обработки и блокировки. Маркетинговых KPI вроде
# open_rate/CTR на этих дашбордах нет — анализатор работает строго на
# операционных сигналах.


class DemoCampaignModel(Base):
    """Метаданные кампании, как они выглядят в платформе AdTarget."""

    __tablename__ = "demo_campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # running | paused | blocked | draft | completed
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # sms_push | push | email_push | ussd_push | ussd_pull | json_pull | json_push | text_push
    channel: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # event-triggered / scheduled / pull — определяет, должны ли приходить события.
    campaign_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="scheduled")
    audience_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    health: Mapped["CampaignHealthModel | None"] = relationship(
        back_populates="campaign", cascade="all, delete-orphan", uselist=False
    )


class CampaignHealthModel(Base):
    """Снапшот операционного состояния кампании.

    Поля соответствуют метрикам, которые видны на дашбордах AdTarget:
      • messages_sent_24h        — сколько сообщений отправлено за сутки;
      • delivery_rate_pct        — доля доставленных от отправленных, %;
      • delivery_failure_rate_pct — доля доставок с ошибкой, %;
      • slow_delivery_share_pct  — доля сообщений с задержкой >300с, %;
      • p95_delivery_latency_sec — 95-й перцентиль задержки доставки, с;
      • event_lag_minutes        — для event-triggered: минут с последнего события;
      • response_lag_minutes     — минут с последней успешной обработки отклика;
      • queue_lag_minutes        — отставание consumer'а очереди обработки, мин;
      • blocked_reason           — если status=blocked, причина блокировки.
    """

    __tablename__ = "campaign_health"

    campaign_id: Mapped[int] = mapped_column(ForeignKey("demo_campaigns.id", ondelete="CASCADE"), primary_key=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, index=True)   # critical/high/medium/low
    attention_score: Mapped[int] = mapped_column(Integer, nullable=False)            # 0-100, выше = здоровее

    messages_sent_24h: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    delivery_rate_pct: Mapped[float] = mapped_column(Float, nullable=False, default=100.0)
    delivery_failure_rate_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    slow_delivery_share_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    p95_delivery_latency_sec: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    event_lag_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_lag_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    queue_lag_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_traffic_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    blocked_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    issues_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    recommended_actions_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    last_checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    campaign: Mapped[DemoCampaignModel] = relationship(back_populates="health")


# ── Продуктовый каталог ───────────────────────────────────────────────────────

class ProductCatalogModel(Base):
    """Продуктовый каталог (тарифы, пакеты, услуги).

    Имитация продуктового каталога AdTarget. Используется при подборе
    таргет-группы: по продукту смотрим, есть ли он в каталоге (для опции
    look-alike по подключившим), сколько подключивших, какие похожие продукты.
    last_used_at обновляется, когда продукт фигурирует в сборке кампании —
    чтобы агент мог отдать «последние использованные продукты».
    """

    __tablename__ = "product_catalog"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="other")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    subscribers: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    nbo_audience_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    similar_to_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    properties_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
