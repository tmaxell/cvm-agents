from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

# Тесты юнит-уровневые и не должны зависеть от живого Postgres —
# заставляем `db` использовать SQLite-fallback (нужно до любого import db).
os.environ.setdefault("USE_SQLITE_FALLBACK", "true")

# Сделать `backend/` доступным для `from agents.* import ...` и т. п.
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@dataclass
class SeededHealth:
    """Снапшот операционного здоровья кампании. Поля повторяют
    CampaignHealthModel (см. backend/models.py)."""
    severity: str
    attention_score: int
    messages_sent_24h: int = 0
    delivery_rate_pct: float = 100.0
    delivery_failure_rate_pct: float = 0.0
    slow_delivery_share_pct: float = 0.0
    p95_delivery_latency_sec: int = 0
    event_lag_minutes: int | None = None
    response_lag_minutes: int | None = None
    queue_lag_minutes: int | None = 0
    last_traffic_at: datetime | None = None
    blocked_reason: str | None = None
    issues_json: list[dict[str, Any]] = field(default_factory=list)
    recommended_actions_json: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SeededCampaign:
    """Лёгкая стуктура, имитирующая DemoCampaignModel в тестах."""
    id: int
    name: str
    status: str
    channel: str
    campaign_kind: str
    audience_size: int
    started_at: datetime
    updated_at: datetime
    health: SeededHealth | None


@pytest.fixture()
def seeded_campaigns() -> list[SeededCampaign]:
    now = datetime.now(UTC)
    return [
        SeededCampaign(
            id=201,
            name="Retention SMS",
            status="blocked",
            channel="sms_push",
            campaign_kind="scheduled",
            audience_size=50_000,
            started_at=now,
            updated_at=now,
            health=SeededHealth(
                severity="critical",
                attention_score=18,
                messages_sent_24h=0,
                delivery_rate_pct=0.0,
                delivery_failure_rate_pct=0.0,
                slow_delivery_share_pct=0.0,
                p95_delivery_latency_sec=0,
                blocked_reason="Превышен лимит SMS-провайдера",
                issues_json=[{"code": "blocked_by_system", "message": "Кампания заблокирована системой."}],
                recommended_actions_json=[{"action": "Снять блокировку и проверить лимиты провайдера."}],
            ),
        ),
        SeededCampaign(
            id=202,
            name="Family Upsell Push",
            status="running",
            channel="push",
            campaign_kind="scheduled",
            audience_size=120_000,
            started_at=now,
            updated_at=now,
            health=SeededHealth(
                severity="medium",
                attention_score=58,
                messages_sent_24h=84_500,
                delivery_rate_pct=92.4,
                delivery_failure_rate_pct=7.6,
                slow_delivery_share_pct=2.1,
                p95_delivery_latency_sec=42,
                issues_json=[{"code": "delivery_failure_high", "message": "Failure rate выше нормы."}],
                recommended_actions_json=[{"action": "Проверить провайдера канала push."}],
            ),
        ),
    ]
