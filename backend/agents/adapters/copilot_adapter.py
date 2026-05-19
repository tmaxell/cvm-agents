from __future__ import annotations

from typing import Any

from agents.qa_copilot import answer as copilot_answer
from schemas import CopilotRequest
from agents.orchestrator import AgentAdapter, AgentResult, AgentTask


class CopilotAdapter(AgentAdapter):
    async def execute(self, task: AgentTask) -> AgentResult:
        request = CopilotRequest.model_validate(task.payload)
        response = await copilot_answer(request)
        return AgentResult(agent="copilot", payload=response)


def to_unified_payload(request: CopilotRequest) -> dict[str, Any]:
    return request.model_dump()


def from_unified_payload(payload: Any):
    return payload
