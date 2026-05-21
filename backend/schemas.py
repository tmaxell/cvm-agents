"""Pydantic-схемы для запросов/ответов агентов.

Здесь живут только схемы, реально используемые активным кодом
(см. реестр в `agents/registry.py` и FastAPI-роуты в `app.py`).
Историю удалённых легаси-схем смотри в git.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


# ── Контекст экрана ───────────────────────────────────────────────────────────

class AgentContext(BaseModel):
    """Контекст, который фронтенд передаёт вместе с запросом к F1 Copilot.

    screen — текущий экран пользователя:
        "campaign_list" | "campaign_flow" | "campaign_wizard" |
        "strategy_list" | "reports" | "unknown"
    """
    screen: str = "unknown"
    campaign_id: int | None = None
    strategy_id: int | None = None
    user_role: str = "analyst"          # "analyst" | "manager" | "admin"
    platform_url: str = "http://192.168.15.102:3000"


# ── F1: CVM Copilot (docs QA) ────────────────────────────────────────────────

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
    citations: list[SourceCitation] = []


# ── Generic chat contract (используется супервизором + фронтом) ──────────────

class ChatAction(BaseModel):
    id: str
    label: str
    kind: str = "default"
    payload: dict[str, Any] = Field(default_factory=dict)


class ChatTraceEvent(BaseModel):
    event: str
    status: Literal["info", "warning", "error"] = "info"
    detail: str | None = None
    ts: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatArtifact(BaseModel):
    id: str
    type: str
    title: str | None = None
    content: dict[str, Any] | None = None
    url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# ── Segment Suggest (агент `segments`) ───────────────────────────────────────

class SegmentSuggestRequest(BaseModel):
    product: str
    campaign_goal: str
    audience_constraints: dict[str, Any] = Field(default_factory=dict)
    strategy: Literal["existing_groups", "compose_new", "hybrid"] = "hybrid"
    demo_contact_base_profile: dict[str, Any] = Field(default_factory=dict)
    current_campaign_context: dict[str, Any] | None = None


class MatchedTargetGroup(BaseModel):
    target_group_id: int | None = None
    name: str
    clients_count: int | None = None
    match_score: float = Field(ge=0.0, le=1.0)
    match_reasons: list[str] = Field(default_factory=list)


class SegmentHypothesis(BaseModel):
    # LLM-backed segment-agent fields.
    name: str = ""
    audience_description: str = ""
    relevance_reason: str = ""
    selection_criteria: dict[str, Any] = Field(default_factory=dict)
    risk_or_limitation: str = ""
    matched_target_group: MatchedTargetGroup | None = None
    is_existing_target_group: bool = False
    segment_source: Literal["existing_target_group", "llm_composed_demo"] = "llm_composed_demo"
    demo_insight: str = ""
    estimated_reach_label: str = ""
    confidence: float = Field(ge=0.0, le=1.0)

    # Legacy UI fields kept during the transition from segment_suggester.
    # Используются фронтом для рендера карточек гипотез.
    title: str = ""
    description: str = ""
    rationale: str = ""
    product_fit: str = ""
    expected_effect: str = ""
    audience_filters: dict[str, Any] = Field(default_factory=dict)
    matched_target_groups: list[MatchedTargetGroup] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    priority: int = Field(ge=1, le=3)


class SegmentSuggestResponse(BaseModel):
    summary: str
    hypotheses: list[SegmentHypothesis] = Field(min_length=2, max_length=3)
    warnings: list[str] = Field(default_factory=list)
    recommendation_only: bool = True
