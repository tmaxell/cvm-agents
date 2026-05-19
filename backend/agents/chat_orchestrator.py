"""Unified chat orchestration primitives for MVP intent routing.

This module provides a lightweight orchestration layer that can sit in front of
existing specialized agents and route user messages by intent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal, Protocol

from langchain_core.messages import HumanMessage, SystemMessage

from llm import get_llm

IntentName = Literal[
    "campaign_attention_report",
    "build_campaign",
    "suggest_segments",
    "save_campaign",
    "save_segment",
    "clarify",
]


@dataclass(slots=True)
class IntentDecision:
    intent: IntentName
    confidence: float
    reason: str = ""
    clarify_question: str | None = None


@dataclass(slots=True)
class RoutingContext:
    session_id: str | None = None
    campaign_id: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PlanStep:
    plan_step_id: str
    agent: str
    tool: str
    input: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ExecutionPlan:
    intent: IntentName
    steps: list[PlanStep]


@dataclass(slots=True)
class RoutePlan:
    intent: IntentName
    capability: str | None
    payload: dict[str, Any]
    execution_plan: ExecutionPlan | None = None
    clarify_question: str | None = None


@dataclass(slots=True)
class ExecutionResult:
    intent: IntentName
    capability: str
    output: Any
    state: dict[str, Any] = field(default_factory=dict)
    executed_steps: list[str] = field(default_factory=list)


class AgentCapability(Protocol):
    """Unified capability contract for orchestrator-managed agents."""

    name: str
    supported_intents: set[IntentName]

    async def execute(self, payload: dict[str, Any], context: RoutingContext) -> Any:
        ...


class FunctionCapability:
    """Adapter that wraps legacy agent entrypoints into a unified contract."""

    def __init__(
        self,
        name: str,
        supported_intents: set[IntentName],
        handler: Callable[[dict[str, Any], RoutingContext], Awaitable[Any]],
    ) -> None:
        self.name = name
        self.supported_intents = supported_intents
        self._handler = handler

    async def execute(self, payload: dict[str, Any], context: RoutingContext) -> Any:
        return await self._handler(payload, context)


class IntentClassifier:
    """Hybrid classifier: rules first, LLM fallback for ambiguous utterances."""

    def __init__(self, confidence_threshold: float = 0.68) -> None:
        self.confidence_threshold = confidence_threshold

    def _rule_based(self, message: str) -> IntentDecision | None:
        text = message.strip().lower()
        if not text:
            return IntentDecision("clarify", 0.0, reason="empty message", clarify_question="Что именно нужно сделать с кампанией?")

        rules: list[tuple[IntentName, tuple[str, ...], float]] = [
            ("campaign_attention_report", ("отчет", "report", "вниман", "attention", "monitor", "метрик"), 0.9),
            ("build_campaign", ("создай кампанию", "build campaign", "new campaign", "собери кампанию"), 0.9),
            ("suggest_segments", ("segment", "сегмент", "аудитори", "цг", "target group"), 0.88),
            ("save_campaign", ("сохрани кампанию", "save campaign", "publish campaign"), 0.9),
            ("save_segment", ("сохрани сегмент", "save segment"), 0.9),
        ]

        matches: list[IntentDecision] = []
        for intent, patterns, conf in rules:
            if any(p in text for p in patterns):
                matches.append(IntentDecision(intent, conf, reason=f"rule match: {intent}"))

        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return IntentDecision("clarify", 0.45, reason="multiple rules matched", clarify_question="Правильно понимаю: нужна сборка кампании, сегменты или отчёт?")
        return None

    async def _llm_classify(self, message: str) -> IntentDecision:
        llm = get_llm(temperature=0)
        prompt = (
            "Classify user intent into one of: campaign_attention_report, build_campaign, suggest_segments, "
            "save_campaign, save_segment, clarify. Return strict JSON with keys intent, confidence, reason, clarify_question."
        )
        result = await llm.ainvoke([
            SystemMessage(content=prompt),
            HumanMessage(content=message),
        ])
        raw_text = getattr(result, "content", result)
        try:
            payload = json.loads(raw_text if isinstance(raw_text, str) else str(raw_text))
            intent = payload.get("intent", "clarify")
            if intent not in {
                "campaign_attention_report",
                "build_campaign",
                "suggest_segments",
                "save_campaign",
                "save_segment",
                "clarify",
            }:
                intent = "clarify"
            return IntentDecision(
                intent=intent,
                confidence=float(payload.get("confidence", 0.0)),
                reason=str(payload.get("reason", "llm_classification")),
                clarify_question=payload.get("clarify_question") or "Уточните, какой именно результат вам нужен?",
            )
        except Exception:
            return IntentDecision("clarify", 0.0, reason="llm_parse_failed", clarify_question="Уточните, что нужно сделать: собрать кампанию, сегменты или отчёт?")

    async def classify(self, message: str) -> IntentDecision:
        rule_decision = self._rule_based(message)
        if rule_decision is not None and rule_decision.intent != "clarify":
            return rule_decision
        llm_decision = await self._llm_classify(message)
        if llm_decision.confidence < self.confidence_threshold:
            return IntentDecision(
                intent="clarify",
                confidence=llm_decision.confidence,
                reason=f"low_confidence: {llm_decision.reason}",
                clarify_question=llm_decision.clarify_question or "Уточните, что нужно сделать в первую очередь?",
            )
        return llm_decision


class AgentRouter:
    def __init__(self, capabilities: list[AgentCapability]) -> None:
        self._capability_by_intent: dict[IntentName, AgentCapability] = {}
        self._capability_by_name: dict[str, AgentCapability] = {}
        for capability in capabilities:
            self._capability_by_name[capability.name] = capability
            for intent in capability.supported_intents:
                self._capability_by_intent[intent] = capability

    def resolve(self, intent: IntentName) -> AgentCapability | None:
        return self._capability_by_intent.get(intent)

    def resolve_by_name(self, capability_name: str) -> AgentCapability | None:
        return self._capability_by_name.get(capability_name)


class PlanBuilder:
    _INTENT_AGENT: dict[IntentName, str] = {
        "campaign_attention_report": "campaign_monitor",
        "build_campaign": "campaign_builder",
        "suggest_segments": "segment_agent",
    }

    _INTENT_STEPS: dict[IntentName, list[tuple[str, str]]] = {
        "campaign_attention_report": [
            ("fetch_campaigns", "fetch campaigns"),
            ("compute_health", "compute health"),
            ("rank_campaigns", "rank"),
            ("explain_rank", "explain"),
        ],
        "build_campaign": [
            ("collect_brief", "collect brief"),
            ("generate_draft", "generate draft"),
            ("review_draft", "review"),
            ("propose_save_campaign", "propose save"),
        ],
        "suggest_segments": [
            ("gather_constraints", "gather constraints"),
            ("generate_segment_options", "generate options"),
            ("rank_segment_options", "rank"),
            ("propose_save_segment", "propose save"),
        ],
    }

    def _build_execution_plan(self, intent: IntentName, capability_name: str, payload: dict[str, Any]) -> ExecutionPlan | None:
        template = self._INTENT_STEPS.get(intent)
        if template is None:
            return None
        steps: list[PlanStep] = []
        prev_step_id: str | None = None
        for plan_step_id, tool in template:
            depends_on = [prev_step_id] if prev_step_id else []
            steps.append(
                PlanStep(
                    plan_step_id=plan_step_id,
                    agent=capability_name,
                    tool=tool,
                    input=dict(payload),
                    depends_on=depends_on,
                )
            )
            prev_step_id = plan_step_id
        return ExecutionPlan(intent=intent, steps=steps)

    def build(self, decision: IntentDecision, context: RoutingContext) -> RoutePlan:
        if decision.intent == "clarify":
            return RoutePlan(
                intent="clarify",
                capability=None,
                payload={"session_id": context.session_id},
                clarify_question=decision.clarify_question or "Можете уточнить задачу одним предложением?",
            )
        payload = {"session_id": context.session_id, "campaign_id": context.campaign_id, "metadata": context.metadata}
        execution_plan = self._build_execution_plan(decision.intent, self._INTENT_AGENT.get(decision.intent, decision.intent), payload)
        return RoutePlan(
            intent=decision.intent,
            capability=decision.intent,
            payload=payload,
            execution_plan=execution_plan,
        )


class ExecutionEngine:
    def __init__(self, classifier: IntentClassifier, router: AgentRouter, planner: PlanBuilder) -> None:
        self.classifier = classifier
        self.router = router
        self.planner = planner

    async def _execute_plan_sequentially(self, plan: ExecutionPlan, context: RoutingContext) -> tuple[dict[str, Any], list[str]]:
        state: dict[str, Any] = {"steps": {}}
        executed_steps: list[str] = []

        for step in plan.steps:
            missing_dependencies = [dep for dep in step.depends_on if dep not in state["steps"]]
            if missing_dependencies:
                raise ValueError(f"Step {step.plan_step_id} has unmet dependencies: {missing_dependencies}")

            capability = self.router.resolve_by_name(step.agent)
            if capability is None:
                raise ValueError(f"Capability '{step.agent}' is not registered")

            step_payload = {
                "plan_step_id": step.plan_step_id,
                "tool": step.tool,
                "input": step.input,
                "depends_on": step.depends_on,
                "state": state,
            }
            result = await capability.execute(step_payload, context)
            state["steps"][step.plan_step_id] = result
            executed_steps.append(step.plan_step_id)

        return state, executed_steps

    def get_parallelizable_batches(self, plan: ExecutionPlan) -> list[list[PlanStep]]:
        """MVP helper for future parallel execution of independent steps."""
        batches: list[list[PlanStep]] = []
        resolved: set[str] = set()
        remaining = list(plan.steps)
        while remaining:
            ready = [step for step in remaining if all(dep in resolved for dep in step.depends_on)]
            if not ready:
                raise ValueError("Execution plan contains cyclic or unresolved dependencies")
            batches.append(ready)
            for step in ready:
                resolved.add(step.plan_step_id)
            ready_ids = {step.plan_step_id for step in ready}
            remaining = [step for step in remaining if step.plan_step_id not in ready_ids]
        return batches

    async def run(self, message: str, context: RoutingContext) -> ExecutionResult | RoutePlan:
        decision = await self.classifier.classify(message)
        plan = self.planner.build(decision, context)
        if plan.intent == "clarify":
            return plan
        capability = self.router.resolve(plan.intent)
        if capability is None:
            return RoutePlan(
                intent="clarify",
                capability=None,
                payload=plan.payload,
                clarify_question="Не удалось подобрать обработчик. Уточните запрос, пожалуйста.",
            )

        if plan.execution_plan is not None:
            state, executed_steps = await self._execute_plan_sequentially(plan.execution_plan, context)
            output = state["steps"].get(executed_steps[-1]) if executed_steps else None
            return ExecutionResult(intent=plan.intent, capability=capability.name, output=output, state=state, executed_steps=executed_steps)

        output = await capability.execute(plan.payload, context)
        return ExecutionResult(intent=plan.intent, capability=capability.name, output=output)


# ---- Wrappers for existing agents -------------------------------------------------


async def _campaign_builder_handler(payload: dict[str, Any], context: RoutingContext) -> dict[str, Any]:
    return {"agent": "campaign_builder", "status": "not_executed", "payload": payload, "context": context.metadata}


async def _segment_agent_handler(payload: dict[str, Any], context: RoutingContext) -> dict[str, Any]:
    return {"agent": "segment_agent", "status": "not_executed", "payload": payload, "context": context.metadata}


async def _campaign_monitor_handler(payload: dict[str, Any], context: RoutingContext) -> dict[str, Any]:
    return {"agent": "campaign_monitor", "status": "not_executed", "payload": payload, "context": context.metadata}


async def _qa_copilot_handler(payload: dict[str, Any], context: RoutingContext) -> dict[str, Any]:
    return {"agent": "qa_copilot", "status": "not_executed", "payload": payload, "context": context.metadata}


DEFAULT_CAPABILITIES: list[AgentCapability] = [
    FunctionCapability("campaign_builder", {"build_campaign", "save_campaign"}, _campaign_builder_handler),
    FunctionCapability("segment_agent", {"suggest_segments", "save_segment"}, _segment_agent_handler),
    FunctionCapability("campaign_monitor", {"campaign_attention_report"}, _campaign_monitor_handler),
    FunctionCapability("qa_copilot", {"clarify"}, _qa_copilot_handler),
]


def build_default_engine(confidence_threshold: float = 0.68) -> ExecutionEngine:
    classifier = IntentClassifier(confidence_threshold=confidence_threshold)
    router = AgentRouter(DEFAULT_CAPABILITIES)
    planner = PlanBuilder()
    return ExecutionEngine(classifier=classifier, router=router, planner=planner)
