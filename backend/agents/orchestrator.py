from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


@dataclass(slots=True)
class AgentTask:
    agent: str
    payload: dict[str, Any]
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentResult:
    agent: str
    payload: Any
    metadata: dict[str, Any] = field(default_factory=dict)


class AgentAdapter:
    async def execute(self, task: AgentTask) -> AgentResult:  # pragma: no cover - interface
        raise NotImplementedError


class Orchestrator:
    def __init__(self, adapters: dict[str, AgentAdapter]) -> None:
        self._adapters = adapters

    async def execute(self, task: AgentTask) -> AgentResult:
        adapter = self._adapters.get(task.agent)
        if adapter is None:
            raise ValueError(f"Unknown adapter: {task.agent}")
        return await adapter.execute(task)


class FunctionAdapter(AgentAdapter):
    def __init__(
        self,
        agent: str,
        handler: Callable[[dict[str, Any], dict[str, Any]], Awaitable[Any]],
    ) -> None:
        self.agent = agent
        self._handler = handler

    async def execute(self, task: AgentTask) -> AgentResult:
        result = await self._handler(task.payload, task.context)
        return AgentResult(agent=self.agent, payload=result)
