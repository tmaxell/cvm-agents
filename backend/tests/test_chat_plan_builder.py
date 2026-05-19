import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from agents.chat_orchestrator import IntentDecision, PlanBuilder, RoutingContext


def test_execution_plan_build_campaign_dependency_chain():
    planner = PlanBuilder()
    plan = planner.build(IntentDecision(intent="build_campaign", confidence=0.91), RoutingContext(session_id="s-1"))
    assert plan.execution_plan is not None
    steps = plan.execution_plan.steps
    assert [s.plan_step_id for s in steps] == ["collect_brief", "generate_draft", "review_draft", "propose_save_campaign"]
    assert steps[0].depends_on == []
    assert steps[1].depends_on == ["collect_brief"]
    assert steps[2].depends_on == ["generate_draft"]
    assert steps[3].depends_on == ["review_draft"]
