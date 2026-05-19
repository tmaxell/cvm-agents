from __future__ import annotations

import logging
from typing import Any

from agents.segment_agent import suggest_segments
from schemas import SegmentSuggestRequest
from agents.orchestrator import AgentAdapter, AgentResult, AgentTask

logger = logging.getLogger(__name__)


class SegmentAdapter(AgentAdapter):
    async def execute(self, task: AgentTask) -> AgentResult:
        logger.warning("segment adapter is deprecated; use unified chat orchestrator endpoint")
        request = SegmentSuggestRequest.model_validate(task.payload)
        response = await suggest_segments(request)
        return AgentResult(agent="segment", payload=response)


def to_unified_payload(request: SegmentSuggestRequest) -> dict[str, Any]:
    return request.model_dump()


def from_unified_payload(payload: Any):
    return payload
