from __future__ import annotations

from typing import Any

from agents.campaign_builder import run as builder_run
from schemas import BuilderRequest
from agents.orchestrator import AgentAdapter, AgentResult, AgentTask


class BuilderAdapter(AgentAdapter):
    async def execute(self, task: AgentTask) -> AgentResult:
        request = BuilderRequest.model_validate(task.payload)
        response = await builder_run(request)
        return AgentResult(agent="builder", payload=response)


def to_unified_payload(request: BuilderRequest) -> dict[str, Any]:
    return request.model_dump()


def from_unified_payload(payload: Any):
    return payload
