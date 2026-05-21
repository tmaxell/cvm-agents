from __future__ import annotations

import os
import sys
from dataclasses import dataclass
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
    attention_score: int
    severity: str
    issues_json: list[dict[str, Any]]
    recommended_actions_json: list[dict[str, Any]]


@dataclass
class SeededCampaign:
    # Поля совпадают с DemoCampaignModel — fixture подсовывается
    # в build_campaign_attention_report() вместо ORM-объекта.
    id: int
    name: str
    channel: str
    audience_size: int
    budget: int
    spent: int
    open_rate: int
    click_rate: int
    conversion_rate: int
    updated_at: datetime
    health: SeededHealth | None


@pytest.fixture()
def seeded_campaigns() -> list[SeededCampaign]:
    now = datetime.now(UTC)
    return [
        SeededCampaign(
            id=201,
            name="Retention SMS",
            channel="sms",
            audience_size=50_000,
            budget=1000,
            spent=970,
            open_rate=22,
            click_rate=8,
            conversion_rate=5,
            updated_at=now,
            health=SeededHealth(
                attention_score=38,
                severity="critical",
                issues_json=[{"code": "low_ctr", "message": "CTR drop"}],
                recommended_actions_json=[{"action": "Rotate creatives"}],
            ),
        ),
        SeededCampaign(
            id=202,
            name="Family Upsell Push",
            channel="push",
            audience_size=120_000,
            budget=1400,
            spent=880,
            open_rate=35,
            click_rate=16,
            conversion_rate=12,
            updated_at=now,
            health=SeededHealth(
                attention_score=72,
                severity="medium",
                issues_json=[{"code": "low_open_rate", "message": "Open rate plateau"}],
                recommended_actions_json=[{"action": "Refine send time"}],
            ),
        ),
    ]


