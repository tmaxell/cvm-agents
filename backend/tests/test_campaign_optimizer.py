import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from agents.campaign_optimizer import run
from schemas import ChannelDeliveryMetric, MonitorMetrics


def _metrics(**overrides):
    base = {
        "delivery_rate": 88.0,
        "open_rate": 12.0,
        "conversion_rate": 9.0,
        "click_rate": 2.0,
        "sent_count": 1000,
        "delivered_count": 880,
        "opened_count": 105,
        "clicked_count": 2,
        "activation_count": 18,
        "channel_deliveries": [
            ChannelDeliveryMetric(
                channel_id=11,
                channel_name="Email",
                content_type="EmailContent",
                sent_count=1000,
                delivered_count=780,
                delivery_rate=78.0,
            )
        ],
    }
    base.update(overrides)
    return MonitorMetrics(**base)


def test_optimizer_returns_prioritized_heuristic_recommendations():
    flow = {
        "activities": [
            {"id": "tg-1", "type": "TargetGroupActivity", "clientSourceId": 101},
            {
                "id": "email-1",
                "type": "PushCommunicationActivity",
                "name": "Email",
                "contentType": "EmailContent",
                "channelId": 11,
            },
        ]
    }

    recs = run(flow, _metrics(), "editing")

    assert 3 <= len(recs) <= 5
    assert [rec.category for rec in recs] == ["control_group", "channel", "flow", "content", "contact_time"]
    assert recs[0].phase == "pre_launch"
    assert recs[1].phase == "post_launch"
    assert recs[-1].id == "contact-window-review"
    assert recs[-1].confidence in {"low", "medium"}


def test_optimizer_uses_conversion_offer_rule_for_active_campaign_with_transaction():
    flow = {
        "activities": [
            {"id": "tg-1", "type": "TargetGroupActivity", "useLocalControlGroup": True},
            {
                "id": "push-1",
                "type": "PushCommunicationActivity",
                "name": "Push",
                "contentType": "CustomContent",
                "channelId": 7,
            },
            {"id": "bt-1", "type": "BusinessTransactionActivity", "offerTemplateId": 300},
            {"id": "wait-1", "type": "WaitActivity"},
        ]
    }
    metrics = _metrics(
        delivery_rate=94.0,
        open_rate=35.0,
        click_rate=8.0,
        conversion_rate=7.0,
        channel_deliveries=[
            ChannelDeliveryMetric(
                channel_id=7,
                channel_name="Push",
                content_type="CustomContent",
                sent_count=1000,
                delivered_count=940,
                delivery_rate=94.0,
            )
        ],
    )

    recs = run(flow, metrics, "active")

    assert any(rec.category == "offer" and rec.activity_id == "bt-1" for rec in recs)
    assert recs[-1].category == "contact_time"
    assert recs[-1].confidence == "medium"
    assert 3 <= len(recs) <= 5
