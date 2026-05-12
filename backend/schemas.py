"""Pydantic-схемы для запросов/ответов агентов."""

from typing import Any
from pydantic import BaseModel


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


class MonitorMetrics(BaseModel):
    delivery_rate: float                # 0–100, % доставки
    open_rate: float                    # 0–100, % открытий / прочтений
    conversion_rate: float              # 0–100, % целевых действий
    click_rate: float                   # 0–100, % переходов (для push/email)


class MonitorResponse(BaseModel):
    metrics: MonitorMetrics
    recommendations: list[str]          # список рекомендаций на русском
    overall_score: int                  # 0–100, общая оценка кампании
    summary: str                        # краткое заключение
