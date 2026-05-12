"""Pydantic-схемы для запросов/ответов агентов."""

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
    # Контекст текущей сессии — передаётся при follow-up запросах
    session_campaign_id: int | None = None    # campaignId из предыдущего ответа
    session_flow_json: str | None = None      # JSON flow из предыдущего ответа


class BuilderResponse(BaseModel):
    message: str                        # ответ агента для чата
    campaign_id: int | None = None      # если кампания уже создана
    draft_flow: dict[str, Any] | None = None  # черновик flow, если ещё не создан
    validation_errors: list[dict] = []
    status: str = "in_progress"         # "in_progress" | "created" | "started" | "error"


# ── F3: Campaign Monitor ──────────────────────────────────────────────────────

class MonitorRequest(BaseModel):
    campaign_id: int
    draft_flow_json: str                # JSON flow кампании (activities[])
    refresh_seed: int = 0               # инкрементируется при каждом Refresh


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
    delivery_rate: float                # 0–100, % доставки
    open_rate: float                    # 0–100, % открытий / прочтений
    conversion_rate: float              # 0–100, % целевых действий
    click_rate: float                   # 0–100, % переходов (для push/email)
    sent_count: int = 0                 # всего отправлено по всем каналам
    delivered_count: int = 0            # всего доставлено по всем каналам
    activation_count: int = 0           # количество активаций / целевых действий
    channel_deliveries: list[ChannelDeliveryMetric] = Field(default_factory=list)
    control_group: ControlGroupComparison | None = None


class MonitorResponse(BaseModel):
    metrics: MonitorMetrics
    recommendations: list[str] = Field(default_factory=list)  # legacy: объединённый список рекомендаций
    structure_recommendations: list[str] = Field(default_factory=list)  # рекомендации по структуре до/во время сборки
    launch_recommendations: list[str] = Field(default_factory=list)     # рекомендации по результатам после запуска
    overall_score: int                  # 0–100, общая оценка кампании
    summary: str                        # краткое заключение
