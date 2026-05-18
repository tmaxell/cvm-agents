"""Pydantic-схемы для запросов/ответов агентов."""

from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field, model_validator


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

class CampaignAudienceMatchedTargetGroup(BaseModel):
    """Target Group match selected in Audience Builder."""
    id: int | str | None = None
    target_group_id: int | None = None
    name: str = ""
    clients_count: int | None = None
    match_score: float | None = None
    match_reasons: list[str] = Field(default_factory=list)


class CampaignAudienceSelectedSegment(BaseModel):
    """Structured selected-segment payload passed to Campaign Builder."""
    hypothesis: dict[str, Any] = Field(default_factory=dict)
    selection_criteria: dict[str, Any] = Field(default_factory=dict)
    matched_target_group: CampaignAudienceMatchedTargetGroup | None = None
    is_existing_target_group: bool = False
    risk_or_limitation: str | None = None
    recommendationOnly: bool = False


class CampaignAudienceRef(BaseModel):
    """Нормализованное описание аудитории для Campaign Builder."""
    target_groups: list[str] = Field(default_factory=list)
    description: str | None = None
    selected_segment: CampaignAudienceSelectedSegment | None = None


class CampaignChannel(BaseModel):
    """Канал коммуникации в нормализованном brief."""
    name: str
    channel_id: int | None = None
    content_type: str | None = None


class CampaignConstraints(BaseModel):
    """Ограничения и рекомендации для сборки кампании."""
    content: str | None = None
    offer_recommendations: str | None = None


class CampaignBrief(BaseModel):
    """Typed campaign brief used by new Builder clients."""
    product: str | None = None
    goal: str | None = None
    audience: CampaignAudienceRef = Field(default_factory=CampaignAudienceRef)
    channels: list[CampaignChannel] = Field(default_factory=list)
    constraints: CampaignConstraints = Field(default_factory=CampaignConstraints)

    @staticmethod
    def _clean_text(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @classmethod
    def from_builder_preferences(cls, preferences: dict[str, Any] | None) -> "CampaignBrief":
        """Normalize legacy BuilderPreferences payload into typed brief."""
        preferences = preferences or {}
        channels_text = cls._clean_text(preferences.get("channels"))
        target_groups_text = cls._clean_text(preferences.get("targetGroups"))
        channels = [
            CampaignChannel(name=part.strip())
            for part in (channels_text or "").replace(";", ",").split(",")
            if part.strip()
        ]
        target_groups = [
            part.strip()
            for part in (target_groups_text or "").replace(";", ",").split(",")
            if part.strip()
        ]
        return cls(
            product=cls._clean_text(preferences.get("product")),
            goal=cls._clean_text(preferences.get("goal")),
            audience=CampaignAudienceRef(
                target_groups=target_groups,
                description=target_groups_text,
            ),
            channels=channels,
            constraints=CampaignConstraints(
                content=cls._clean_text(preferences.get("content")),
                offer_recommendations=cls._clean_text(preferences.get("offerRecommendations")),
            ),
        )

    def to_builder_preferences(self) -> dict[str, Any]:
        """Expose normalized brief as legacy preferences while migration is in progress."""
        preferences: dict[str, Any] = {}
        if self.product:
            preferences["product"] = self.product
        if self.goal:
            preferences["goal"] = self.goal
        audience_text = self.audience.description or ", ".join(self.audience.target_groups)
        if audience_text:
            preferences["targetGroups"] = audience_text
        channels_text = ", ".join(channel.name for channel in self.channels if channel.name)
        if channels_text:
            preferences["channels"] = channels_text
        if self.constraints.content:
            preferences["content"] = self.constraints.content
        if self.constraints.offer_recommendations:
            preferences["offerRecommendations"] = self.constraints.offer_recommendations
        return preferences


# ── Typed draft flow patch contract ───────────────────────────────────────────

class FlowPatchActivity(BaseModel):
    """Activity payload inside a typed draft-flow patch."""
    type: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    id: str | None = None
    occurrence: Literal["first", "last"] = "last"


class FlowPatch(BaseModel):
    """Typed patch contract for safe draft-flow edits.

    base_version must match the current draft_flow_version before the patch is
    applied. The first supported operation is used with the shared activity and
    anchor fields; the list shape keeps the contract forward-compatible with
    multi-step patches.
    """
    base_version: int = Field(ge=0)
    operations: list[Literal["add_activity", "remove_activity"]] = Field(min_length=1)
    anchor_activity_id: str | None = None
    anchor_activity_type: str | None = None
    insert_position: Literal["before", "after", "end"] | None = None
    activity: FlowPatchActivity


ReviewChecklistCategory = Literal["audience", "consent", "contact_policy", "offer", "content", "validation"]
ReviewChecklistItemStatus = Literal["green", "warning", "blocker"]
ReviewStatus = Literal["green", "warnings", "blocked"]
BuilderStatus = Literal[
    "collect_brief",
    "draft_ready",
    "needs_review",
    "created_in_adtarget",
    "running",
    "error",
]
CampaignRuntimeStatus = Literal["editing", "active", "paused"]


class ReviewChecklistItem(BaseModel):
    category: ReviewChecklistCategory
    label: str
    status: ReviewChecklistItemStatus
    message: str


class ReviewChecklist(BaseModel):
    items: list[ReviewChecklistItem] = Field(default_factory=list)
    status: ReviewStatus = "blocked"


class BuilderRequest(BaseModel):
    goal: str                           # «хочу кампанию по утилизации пакета данных»
    context: AgentContext = AgentContext()
    history: list[dict[str, str]] = []
    session_id: str | None = None          # id backend-сессии Builder для продолжения диалога
    # Контекст текущей сессии — передаётся при follow-up запросах
    session_campaign_id: int | None = None    # campaignId из предыдущего ответа
    session_flow_json: str | None = None      # JSON flow из предыдущего ответа
    draft_flow_version: int | None = None      # версия draft flow из предыдущего ответа
    campaign_brief: CampaignBrief | None = None
    builder_preferences: dict[str, Any] = Field(default_factory=dict)  # legacy UI payload during brief migration
    review_checklist_acknowledged: bool = False  # user explicitly accepted non-blocking review warnings

    @model_validator(mode="after")
    def normalize_campaign_brief(self) -> "BuilderRequest":
        """Keep new campaign_brief and legacy builder_preferences in sync."""
        if self.campaign_brief is None:
            self.campaign_brief = CampaignBrief.from_builder_preferences(self.builder_preferences)
        if not self.builder_preferences and self.campaign_brief is not None:
            self.builder_preferences = self.campaign_brief.to_builder_preferences()
        return self


class CampaignBriefCompleteness(BaseModel):
    """Completeness metadata for the Campaign Builder brief."""
    missing_fields: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    safety_checks: list[str] = Field(default_factory=list)


class BuilderResponse(BaseModel):
    message: str                        # ответ агента для чата
    builder_preferences: dict[str, Any] | None = None  # обновлённые preferences после запоминания
    preference_patch: dict[str, Any] | None = None      # частичное обновление preferences
    session_id: str | None = None       # backend-сессия, к которой сохранён ответ
    campaign_id: int | None = None      # если кампания уже создана
    draft_flow: dict[str, Any] | None = None  # черновик flow, если ещё не создан
    draft_flow_version: int | None = None      # версия черновика flow
    validation_errors: list[dict] = []
    brief_completeness: CampaignBriefCompleteness | None = None
    review_checklist: ReviewChecklist | None = None
    review_status: ReviewStatus = "blocked"
    review_checklist_acknowledged: bool = False
    status: BuilderStatus = "collect_brief"


# ── Segment Suggest ──────────────────────────────────────────────────────────

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
    status: str = "collect_brief"
    campaign_brief: CampaignBrief | None = None
    draft_flow: dict[str, Any] | None = None
    draft_flow_version: int | None = None
    brief_completeness: CampaignBriefCompleteness | None = None
    review_checklist: ReviewChecklist | None = None
    review_status: ReviewStatus = "blocked"
    review_checklist_acknowledged: bool = False


class SessionDetail(Session):
    messages: list[Message] = []


class SessionCreate(BaseModel):
    session_id: str | None = None
    campaign_id: int | None = None
    title: str | None = None
    status: str = "collect_brief"


class MessageCreate(BaseModel):
    role: str
    content: str
    metadata: dict[str, Any] | None = None


# ── Runtime campaign actions ──────────────────────────────────────────────────

class BuilderCreateRequest(BaseModel):
    session_id: str
    draft_flow: dict[str, Any]
    draft_flow_version: int = Field(ge=1)
    campaign_brief: CampaignBrief | None = None
    validation_errors: list[dict] = []
    review_checklist_acknowledged: bool = False


class CampaignActionRequest(BaseModel):
    campaign_id: int
    review_status: ReviewStatus = "blocked"
    review_checklist_acknowledged: bool = False


class CampaignActionResponse(BaseModel):
    campaign_id: int
    status: CampaignRuntimeStatus
    result: Any


# ── F3: Campaign Monitor ──────────────────────────────────────────────────────

class MonitorRequest(BaseModel):
    campaign_id: int
    draft_flow_json: str                # JSON flow кампании (activities[])
    refresh_seed: int = 0               # инкрементируется при каждом Refresh
    campaign_status: CampaignRuntimeStatus = "editing"


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


class OptimizationRecommendation(BaseModel):
    id: str
    phase: Literal["pre_launch", "post_launch"]
    category: Literal["channel", "contact_time", "offer", "control_group", "content", "flow"]
    change: str
    reason: str
    expected_effect: str
    confidence: Literal["low", "medium", "high"]
    source: Literal["flow", "metrics", "heuristic", "llm"]
    activity_id: str | None = None


class OptimizationResponse(BaseModel):
    summary: str
    recommendations: list[OptimizationRecommendation]


class MonitorResponse(BaseModel):
    metrics: MonitorMetrics
    recommendations: list[str] = Field(default_factory=list)  # legacy: объединённый список рекомендаций
    structure_recommendations: list[str] = Field(default_factory=list)  # рекомендации по структуре до/во время сборки
    launch_recommendations: list[str] = Field(default_factory=list)     # рекомендации по результатам после запуска
    similar_campaign_actions: list[str] = Field(default_factory=list)   # что сработало в похожих кампаниях
    optimization_recommendations: list[OptimizationRecommendation] = Field(default_factory=list)
    overall_score: int                  # 0–100, общая оценка кампании
    summary: str                        # краткое заключение
