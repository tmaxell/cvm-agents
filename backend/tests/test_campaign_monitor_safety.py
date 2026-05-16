import asyncio
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from agents import campaign_monitor
from schemas import MonitorRequest


def test_monitor_does_not_return_or_mutate_draft_flow(monkeypatch):
    flow = {
        "activities": [
            {"id": "tg-1", "type": "TargetGroupActivity", "clientSourceId": 101},
            {"id": "sms-1", "type": "PushCommunicationActivity", "contentType": "SmsContent"},
        ]
    }
    draft_flow_json = json.dumps(flow, ensure_ascii=False)

    def unavailable_llm(*args, **kwargs):
        raise RuntimeError("LLM disabled for safety test")

    monkeypatch.setattr(campaign_monitor, "get_llm", unavailable_llm)

    response = asyncio.run(campaign_monitor.run(MonitorRequest(
        campaign_id=123,
        draft_flow_json=draft_flow_json,
        campaign_status="editing",
    )))

    assert json.loads(draft_flow_json) == flow
    assert "draft_flow" not in response.model_dump()


def test_optimizer_module_does_not_reference_update_campaign_flow():
    source = Path(campaign_monitor.optimizer_run.__code__.co_filename).read_text()

    assert "update_campaign_flow" not in source
    assert "from tools import adtarget" not in source
    assert "tools.adtarget" not in source
