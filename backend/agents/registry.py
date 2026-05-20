"""Реестр агентов мультиагентной системы CVM."""

from __future__ import annotations

from agents.base import AgentProtocol, FunctionAgent
from agents import agent_attention, agent_builder, agent_docs, agent_refiner, agent_runtime, agent_segments


def _make(module) -> AgentProtocol:
    return FunctionAgent(
        name=module.NAME,
        description=module.DESCRIPTION,
        supported_intents=module.SUPPORTED_INTENTS,
        fn=module.execute,
    )


_AGENTS: dict[str, AgentProtocol] = {
    agent_attention.NAME: _make(agent_attention),
    agent_builder.NAME: _make(agent_builder),
    agent_refiner.NAME: _make(agent_refiner),
    agent_segments.NAME: _make(agent_segments),
    agent_docs.NAME: _make(agent_docs),
    agent_runtime.NAME: _make(agent_runtime),
}


def get_agent(name: str) -> AgentProtocol | None:
    return _AGENTS.get(name)


def list_agents() -> list[AgentProtocol]:
    return list(_AGENTS.values())


def agent_for_intent(intent: str) -> AgentProtocol | None:
    for agent in _AGENTS.values():
        if intent in agent.supported_intents:
            return agent
    return None
