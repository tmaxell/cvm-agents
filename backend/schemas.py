"""Pydantic-схемы для запросов/ответов агентов."""

from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field


# ── Контекст экрана ───────────────────────────────────────────────────────────

class AgentContext(BaseModel):
    """Контекст, который фронтенд передаёт вместе с каждым запросом к агенту.

    screen — текущий экран пользователя:
        "campaign_list" | "campaign_flow" | "campaign_wizard" |
        "strategy_list" | "reports" | "unknown"
    """
    screen: str = "unknown"
    campaign_id: int | None = None
    strategy_id: int | None = None
    user_role: str = "analyst"          # "analyst" | "manager" | "admin"
    platform_url: str = "http://192.168.15.102:3000"


# ── F1: CVM Copilot ───────────────────────────────────────────────────────────

class SourceCitation(BaseModel):
    """Ссылка на источник из RAG-индекса."""
    id: str                             # уникальный id чанка
    title: str                          # заголовок документа
    source: str                         # путь к файлу (относительный)
    heading_path: list[str] = []        # путь по заголовкам [h1, h2, h3]
    score: float = 0.0                  # relevance score


class CopilotRequest(BaseModel):
    question: str
    context: AgentContext = AgentContext()
    history: list[dict[str, str]] = []  # [{"role": "user"|"assistant", "content": "..."}]


class CopilotResponse(BaseModel):
    answer: str
    citations: list[SourceCitation] = []  # цитируемые источники из RAG


# ── F2: Campaign Builder ──────────────────────────────────────────────────────

class BuilderRequest(BaseModel):
    goal: str                           # «хочу кампанию по утилизации пакета данных»
    context: AgentContext = AgentContext()
    history: list[dict[str, str]] = []
    session_id: str | None = None          # id backend-сессии Builder для продолжения диалога
    # Контекст текущей сессии — передаётся при follow-up запросах
    session_campaign_id: int | None = None    # campaignId из предыдущего ответа
    session_flow_json: str | None = None      # JSON flow из предыдущего ответа
    builder_preferences: dict[str, Any] = Field(default_factory=dict)  # каналы/ЦГ/офферы/цель из UI


class BuilderResponse(BaseModel):
    message: str                        # ответ агента для чата
    builder_preferences: dict[str, Any] | None = None  # обновлённые preferences после запоминания
    preference_patch: dict[str, Any] | None = None      # частичное обновление preferences
    session_id: str | None = None       # backend-сессия, к которой сохранён ответ
    campaign_id: int | None = None      # если кампания уже создана
    draft_flow: dict[str, Any] | None = None  # черновик flow, если ещё не создан
    validation_errors: list[dict] = []
    status: str = "in_progress"         # "in_progress" | "created" | "started" | "error"


# ── Segment Suggest ──────────────────────────────────────────────────────────

class SegmentSuggestRequest(BaseModel):
    product: str
    campaign_goal: str
    audience_constraints: dict[str, Any] = Field(default_factory=dict)
    current_campaign_context: dict[str, Any] | None = None


class MatchedTargetGroup(BaseModel):
    target_group_id: int | None = None
    name: str
    clients_count: int | None = None
    match_score: float = Field(ge=0.0, le=1.0)
    match_reasons: list[str] = Field(default_factory=list)


class SegmentHypothesis(BaseModel):
    title: str
    description: str
    rationale: str
    product_fit: str
    expected_effect: str
    audience_filters: dict[str, Any] = Field(default_factory=dict)
    matched_target_groups: list[MatchedTargetGroup] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    priority: int = Field(ge=1, le=3)
    confidence: float = Field(ge=0.0, le=1.0)


class SegmentSuggestResponse(BaseModel):
    summary: str
    hypotheses: list[SegmentHypothesis] = Field(min_length=2, max_length=3)
    warnings: list[str] = Field(default_factory=list)


# ── Builder sessions ─────────────────────────────────────────────────────────

class Message(BaseModel):
    id: str
    session_id: str
    role: str
    content: str
    created_at: datetime
    metadata: dict[str, Any] | None = None


class Session(BaseModel):
    id: str
    campaign_id: int | None = None
    title: str
    created_at: datetime
    updated_at: datetime
    status: str = "in_progress"


class SessionDetail(Session):
    messages: list[Message] = []


class SessionCreate(BaseModel):
    session_id: str | None = None
    campaign_id: int | None = None
    title: str | None = None
    status: str = "in_progress"


class MessageCreate(BaseModel):
    role: str
    content: str
    metadata: dict[str, Any] | None = None


# ── Runtime campaign actions ──────────────────────────────────────────────────

class CampaignActionRequest(BaseModel):
    campaign_id: int


class CampaignActionResponse(BaseModel):
    campaign_id: int
    status: str
    result: Any


# ── F3: Campaign Monitor ──────────────────────────────────────────────────────

class MonitorRequest(BaseModel):
    campaign_id: int
    draft_flow_json: str                # JSON flow кампании (activities[])
    refresh_seed: int = 0               # инкрементируется при каждом Refresh
    campaign_status: str = "editing"     # "editing" | "active" | "paused"


class ChannelDeliveryMetric(BaseModel):
    channel_id: int | None = None
    channel_name: str
    content_type: str
    sent_count: int
    delivered_count: int
    delivery_rate: float                # 0–100, % доставки по каналу


class ControlGroupComparison(BaseModel):
    test_group_size: int
    control_group_size: int
    test_conversion_rate: float         # 0–100, % конверсии/активаций в тестовой группе
    control_conversion_rate: float      # 0–100, % конверсии/активаций в контрольной группе
    uplift_pp: float                    # разница в процентных пунктах
    uplift_percent: float               # относительный uplift к контрольной группе
    test_activations: int
    control_activations: int


class MonitorMetrics(BaseModel):
    delivery_rate: float                # 0–100, доставлено / отправлено
    open_rate: float                    # 0–100, открыто / доставлено
    conversion_rate: float              # 0–100, активации / клики для click-flow, иначе активации / доставки
    click_rate: float                   # 0–100, клики / открытия (для push/email; 0 для каналов без кликов)
    sent_count: int = 0                 # всего отправлено по всем каналам
    delivered_count: int = 0            # всего доставлено по всем каналам
    opened_count: int = 0               # всего открыто / прочитано
    clicked_count: int = 0              # всего переходов (для push/email)
    activation_count: int = 0           # количество активаций / целевых действий
    channel_deliveries: list[ChannelDeliveryMetric] = Field(default_factory=list)
    control_group: ControlGroupComparison | None = None


class MonitorResponse(BaseModel):
    metrics: MonitorMetrics
    recommendations: list[str] = Field(default_factory=list)  # legacy: объединённый список рекомендаций
    structure_recommendations: list[str] = Field(default_factory=list)  # рекомендации по структуре до/во время сборки
    launch_recommendations: list[str] = Field(default_factory=list)     # рекомендации по результатам после запуска
    similar_campaign_actions: list[str] = Field(default_factory=list)   # что сработало в похожих кампаниях
    overall_score: int                  # 0–100, общая оценка кампании
    summary: str                        # краткое заключение
