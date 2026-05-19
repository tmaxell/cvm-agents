from __future__ import annotations

import logging
from typing import Any

from agents.campaign_monitor import run as monitor_run
from schemas import MonitorRequest
from agents.orchestrator import AgentAdapter, AgentResult, AgentTask

logger = logging.getLogger(__name__)


class MonitorAdapter(AgentAdapter):
    async def execute(self, task: AgentTask) -> AgentResult:
        logger.warning("monitor adapter is deprecated; use unified chat orchestrator endpoint")
        request = MonitorRequest.model_validate(task.payload)
        response = await monitor_run(request)
        return AgentResult(agent="monitor", payload=response)


def to_unified_payload(request: MonitorRequest) -> dict[str, Any]:
    return request.model_dump()


def from_unified_payload(payload: Any):
    return payload
