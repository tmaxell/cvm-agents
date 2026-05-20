"""Общие типы и протокол для мультиагентной системы CVM.

Архитектура:
- AgentContext         — что агент знает (сессия, история, текущее сообщение, action, store).
- AgentResult          — что агент возвращает супервизору.
- AgentProtocol        — единый интерфейс execute().
- Plan / PlanStep      — план выполнения (один или несколько шагов).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal, Protocol, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from db import ChatStore
    from schemas import ChatAction

logger = logging.getLogger(__name__)


IntentName = Literal[
    "campaign_attention",
    "build_campaign",
    "suggest_segments",
    "refine_campaign",
    "documentation_qa",
    "runtime_action",
]


@dataclass(slots=True)
class AgentContext:
    """Полный контекст одного запроса /api/chat, прокидываемый агенту."""
    session_id: str
    run_id: str
    store: "ChatStore"
    message: str
    history: list[dict[str, Any]] = field(default_factory=list)
    action: "ChatAction | None" = None
    # Текущие артефакты сессии (последний draft_flow, последний сегмент и т.д.)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    # Свободные параметры, проброшенные из supervisor (intent, plan_step inputs).
    inputs: dict[str, Any] = field(default_factory=dict)

    def latest_artifact(self, *types: str) -> dict[str, Any] | None:
        for artifact in reversed(self.artifacts):
            if not types or artifact.get("type") in types:
                return artifact
        return None

    async def emit(self, event: str, *, status: str = "info", detail: str | None = None,
                   metadata: dict[str, Any] | None = None) -> None:
        await self.store.add_event(
            run_id=self.run_id, event=event, status=status, detail=detail, metadata=metadata or {},
        )


@dataclass(slots=True)
class AgentResult:
    """Что вернул агент супервизору."""
    assistant_message: str = ""
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    actions: list["ChatAction"] = field(default_factory=list)
    status: Literal["ok", "error", "needs_input"] = "ok"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PlanStep:
    """Один шаг плана выполнения."""
    agent: str
    description: str
    inputs: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Plan:
    """Высокоуровневый план для одного intent."""
    intent: IntentName
    steps: list[PlanStep]
    summary: str = ""


class AgentProtocol(Protocol):
    """Единый интерфейс агента."""

    name: str
    description: str
    supported_intents: tuple[IntentName, ...]

    async def execute(self, context: AgentContext) -> AgentResult: ...


# ── Helper для регистрации функций-агентов ────────────────────────────────────

AgentFn = Callable[[AgentContext], Awaitable[AgentResult]]


@dataclass(slots=True)
class FunctionAgent:
    """Простой адаптер: оборачивает async function в Agent."""
    name: str
    description: str
    supported_intents: tuple[IntentName, ...]
    fn: AgentFn

    async def execute(self, context: AgentContext) -> AgentResult:
        return await self.fn(context)
