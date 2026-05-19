"""Seed demo campaigns and health diagnostics for local development."""

from __future__ import annotations

import asyncio
import random

from db import init_db, session_scope
from models import CampaignHealthModel, DemoCampaignModel


_HEALTHY_ISSUES: list[dict[str, str]] = []

_PROBLEM_TEMPLATES: list[tuple[list[dict[str, str]], list[dict[str, str]]]] = [
    (
        [{"code": "low_open_rate", "message": "Open rate ниже целевого порога 12%."}],
        [
            {"action": "Пересобрать сегмент", "details": "Сузить аудиторию по недавней активности."},
            {"action": "A/B-тест заголовка", "details": "Проверить 2 новых варианта текста."},
        ],
    ),
    (
        [{"code": "high_unsubscribe", "message": "Рост отписок выше 1.8% за 24 часа."}],
        [
            {"action": "Снизить частоту", "details": "Установить частотный cap до 2 касаний в неделю."},
            {"action": "Обновить оффер", "details": "Заменить скидку на более релевантную механику."},
        ],
    ),
    (
        [{"code": "budget_burn", "message": "Расход бюджета опережает план на 25%."}],
        [
            {"action": "Понизить ставку", "details": "Снизить дневной лимит и CPC-бид."},
            {"action": "Сместить слот", "details": "Перенести часть трафика в вечерний слот."},
        ],
    ),
]


async def seed_demo_campaigns(count: int = 30) -> None:
    """Populate demo_campaigns and campaign_health tables with diverse campaign states."""
    count = min(max(count, 20), 50)
    rng = random.Random()

    async with session_scope() as db:
        await db.execute(CampaignHealthModel.__table__.delete())
        await db.execute(DemoCampaignModel.__table__.delete())

        for i in range(1, count + 1):
            problematic = i % 4 == 0
            status = rng.choice(["active", "paused", "draft"]) if not problematic else rng.choice(["active", "at_risk"])
            audience_size = rng.randint(5_000, 250_000)
            budget = rng.randint(80_000, 1_200_000)
            spent = int(budget * rng.uniform(0.2, 0.95))
            open_rate = rng.randint(13, 34) if not problematic else rng.randint(3, 11)
            click_rate = rng.randint(3, 14) if not problematic else rng.randint(1, 4)
            conversion_rate = rng.randint(2, 9) if not problematic else rng.randint(0, 2)

            campaign = DemoCampaignModel(
                name=f"Demo Campaign {i:02d}",
                status=status,
                channel=rng.choice(["sms", "push", "email", "ussd"]),
                audience_size=audience_size,
                budget=budget,
                spent=spent,
                open_rate=open_rate,
                click_rate=click_rate,
                conversion_rate=conversion_rate,
            )
            db.add(campaign)
            await db.flush()

            if problematic:
                issues, actions = rng.choice(_PROBLEM_TEMPLATES)
                attention_score = rng.randint(20, 59)
                severity = rng.choice(["high", "critical"])
            else:
                issues, actions = _HEALTHY_ISSUES, []
                attention_score = rng.randint(75, 98)
                severity = "low"

            db.add(
                CampaignHealthModel(
                    campaign_id=campaign.id,
                    attention_score=attention_score,
                    severity=severity,
                    issues_json=issues,
                    recommended_actions_json=actions,
                )
            )


if __name__ == "__main__":
    async def _main() -> None:
        await init_db()
        await seed_demo_campaigns()

    asyncio.run(_main())
