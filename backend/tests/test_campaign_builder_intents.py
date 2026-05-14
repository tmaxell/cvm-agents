import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from agents.campaign_builder import _parse_flow_edit_intent


def test_add_transaction_intent_targets_business_transaction():
    intent = _parse_flow_edit_intent("Добавь транзакцию")

    assert intent is not None
    assert intent.action == "add_business_transaction"
    assert intent.activity_type == "BusinessTransactionActivity"
    assert intent.anchor_activity_type is None


def test_realtime_check_after_transaction_uses_transaction_as_anchor():
    intent = _parse_flow_edit_intent("Добавь после транзакции реал-тайм проверку")

    assert intent is not None
    assert intent.action == "add_activity"
    assert intent.activity_type == "RealTimeCheckActivity"
    assert intent.anchor_activity_type == "BusinessTransactionActivity"


def test_realtime_check_without_add_marker_still_targets_realtime_activity():
    intent = _parse_flow_edit_intent("нет, real-time check должно добавится")

    assert intent is not None
    assert intent.action == "add_activity"
    assert intent.activity_type == "RealTimeCheckActivity"
    assert intent.anchor_activity_type is None
