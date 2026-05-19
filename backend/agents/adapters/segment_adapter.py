from __future__ import annotations

from typing import Any

from agents.segment_agent import suggest_segments
from schemas import SegmentSuggestRequest
from agents.orchestrator import AgentAdapter, AgentResult, AgentTask


class SegmentAdapter(AgentAdapter):
    async def execute(self, task: AgentTask) -> AgentResult:
        request = SegmentSuggestRequest.model_validate(task.payload)
        response = await suggest_segments(request)
        return AgentResult(agent="segment", payload=response)


def to_unified_payload(request: SegmentSuggestRequest) -> dict[str, Any]:
    return request.model_dump()


def from_unified_payload(payload: Any):
    return payload
