from __future__ import annotations

import logging
from typing import Any

from agents.campaign_builder import run as builder_run
from schemas import BuilderRequest
from agents.orchestrator import AgentAdapter, AgentResult, AgentTask

logger = logging.getLogger(__name__)
_DEPRECATED_MSG = "builder adapter is deprecated; use unified chat orchestrator endpoint"


class BuilderAdapter(AgentAdapter):
    async def execute(self, task: AgentTask) -> AgentResult:
        logger.warning(_DEPRECATED_MSG)
        request = BuilderRequest.model_validate(task.payload)
        response = await builder_run(request)
        if hasattr(response, "message"):
            response.message = f"⚠️ {_DEPRECATED_MSG}. {response.message}"
        return AgentResult(agent="builder", payload=response)


def to_unified_payload(request: BuilderRequest) -> dict[str, Any]:
    return request.model_dump()


def from_unified_payload(payload: Any):
    return payload
