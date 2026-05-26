"""Seed demo campaigns + operational health snapshots.

Метрики и каналы соответствуют тому, что показано на дашбордах платформы
AdTarget (см. examples/01-06 - Dashboards*): доставка сообщений, задержки,
тайм-ауты событий/откликов, очереди обработки, блокировки.

Распределение:
- ~70% кампаний — здоровые (delivery_rate ≥97%, нет тайм-аутов);
- ~30% — с проблемами различных категорий, чтобы attention-агент имел
  что показать. Категории:
    delivery_failure_high / delivery_latency_high / low_delivery_rate /
    event_timeout / response_timeout / queue_lag / blocked_by_system / no_traffic.
"""

from __future__ import annotations

import asyncio
import random
from datetime import UTC, datetime, timedelta

from db import init_db, session_scope
from models import CampaignHealthModel, DemoCampaignModel


_CHANNELS_PUSH = ["sms_push", "push", "email_push", "ussd_push", "text_push", "json_push"]
_CHANNELS_PULL = ["ussd_pull", "json_pull", "text_pull"]
_ALL_CHANNELS = _CHANNELS_PUSH + _CHANNELS_PULL

_CAMPAIGN_NAMES = [
    "Promo Семейный — апсейл", "Onboarding новых SIM", "Retention отток 30д",
    "Пакет 5GB — upsell", "Премиум-пользователи Cross-sell", "День рождения — поздравление",
    "USSD меню — балансы", "Push реактивация спящих", "Email рассылка новостей",
    "Charge событие — пополнение", "Roaming — приветствие", "VIP-клиенты — премия",
    "Молодёжный тариф — промо", "Семейная подписка Кино+", "Topup-триггер — бонус",
    "Опрос NPS через USSD", "Stop-spam — сервисное", "Корпоративные — апсейл",
    "Удержание перед churn", "Пакет 20GB ночной — анонс", "Activation — пакет 10GB",
    "Возврат после Stop", "Промо-акция выходные", "Тариф Цифровой — анонс",
    "Сегмент low-ARPU — реактивация", "Push-возврат брошенной корзины",
    "Сегмент high-ARPU — премиум", "Day-1 onboarding", "Day-7 onboarding",
    "Day-30 onboarding",
]


# Шаблоны проблем (issue_code → пример сообщения + рекомендуемые операционные действия).
_PROBLEM_TEMPLATES: dict[str, dict] = {
    "delivery_failure_high": {
        "message": "Доля недоставленных сообщений превышает порог (5% для SMS / 10% для push).",
        "actions": [
            {"action": "Проверить состояние канального провайдера и очередь отправки."},
            {"action": "Сверить лимиты SMPP/HTTP-сессии с провайдером."},
        ],
    },
    "delivery_latency_high": {
        "message": "Существенная доля сообщений уходит с задержкой >300 секунд.",
        "actions": [
            {"action": "Проверить нагрузку отправителя и увеличить пропускную способность канала."},
            {"action": "Уменьшить throttling или поднять параллелизм consumer'а."},
        ],
    },
    "low_delivery_rate": {
        "message": "Общая доля доставленных за сутки ниже базовой нормы (<90%).",
        "actions": [
            {"action": "Проверить контрактные SLA провайдера и долю недоставок по причинам."},
            {"action": "Сегментировать ошибки по контактным политикам (opt-out, частотные ограничения)."},
        ],
    },
    "event_timeout": {
        "message": "Для event-triggered кампании не поступают события более 60 минут.",
        "actions": [
            {"action": "Проверить источник события в DDS и состояние подписки на топик."},
            {"action": "Сверить параметры события (eventCode, фильтры) с дашбордом «События и Отклики»."},
        ],
    },
    "response_timeout": {
        "message": "Отклики не обрабатываются дольше 60 минут — consumer мог зависнуть.",
        "actions": [
            {"action": "Перезапустить consumer обработчика откликов."},
            {"action": "Проверить error-rate ResponseActivity и lag в очереди."},
        ],
    },
    "queue_lag": {
        "message": "Отставание consumer'а очереди обработки превышает 15 минут.",
        "actions": [
            {"action": "Проверить размер consumer-group и rebalance в Kafka."},
            {"action": "Увеличить количество партиций / реплик consumer'а."},
        ],
    },
    "blocked_by_system": {
        "message": "Кампания заблокирована системой — обработка приостановлена.",
        "actions": [
            {"action": "Снять блокировку через раздел Campaigns и проверить причину в логах."},
            {"action": "Сверить настройки доступа TG и контактных политик."},
        ],
    },
    "no_traffic": {
        "message": "Кампания запущена, но за последние 24 часа не отправила ни одного сообщения.",
        "actions": [
            {"action": "Проверить, что таргет-группа обновилась и в ней есть клиенты."},
            {"action": "Проверить триггер запуска и расписание (schedule.period)."},
        ],
    },
}

_BLOCKED_REASONS = [
    "Превышен лимит SMS-провайдера",
    "TargetGroup snapshot отсутствует",
    "Канал недоступен (provider 5xx)",
    "Заблокирована вручную оператором",
    "Несоответствие контактной политики",
]


async def seed_demo_campaigns(count: int = 30) -> None:
    """Создаёт демо-кампании с операционными снапшотами здоровья.

    init_db пересоздаёт таблицы при каждом старте бэкенда — данные всегда
    свежие и соответствуют текущей схеме.
    """
    count = min(max(count, 20), 50)
    rng = random.Random(42)  # детерминированный seed для воспроизводимости demo
    now = datetime.now(UTC)

    # Гарантируем, чтобы в демо были «знакомые» проблемы из дашбордов:
    # 2× blocked_by_system, 1× event_timeout, 1× response_timeout — иначе при
    # случайном raffle’е они могут не выпасть и отчёт будет беднее.
    forced: dict[int, str] = {0: "blocked_by_system", 1: "event_timeout",
                              2: "response_timeout", 3: "blocked_by_system"}

    async with session_scope() as db:
        await db.execute(CampaignHealthModel.__table__.delete())
        await db.execute(DemoCampaignModel.__table__.delete())

        for i in range(count):
            name = _CAMPAIGN_NAMES[i % len(_CAMPAIGN_NAMES)]
            channel = rng.choice(_ALL_CHANNELS)
            campaign_kind = rng.choices(
                ["scheduled", "event_triggered", "pull"],
                weights=[55, 35, 10] if channel in _CHANNELS_PUSH else [10, 10, 80],
            )[0]
            # event_timeout требует event-triggered, response_timeout — pull.
            if forced.get(i) == "event_timeout":
                campaign_kind = "event_triggered"
                channel = rng.choice(_CHANNELS_PUSH)
            elif forced.get(i) == "response_timeout":
                campaign_kind = "pull"
                channel = rng.choice(_CHANNELS_PULL)

            issue_code = forced.get(i) or _pick_issue(rng, campaign_kind, channel)
            severity = _severity_for(issue_code)
            status, blocked_reason = _status_for(rng, issue_code)
            started_at = now - timedelta(days=rng.randint(1, 90))
            audience_size = rng.randint(2_500, 250_000)

            campaign = DemoCampaignModel(
                name=name,
                status=status,
                channel=channel,
                campaign_kind=campaign_kind,
                audience_size=audience_size,
                started_at=started_at,
            )
            db.add(campaign)
            await db.flush()

            db.add(_build_health(rng, campaign, issue_code, severity, blocked_reason, now))


def _pick_issue(rng: random.Random, campaign_kind: str, channel: str) -> str | None:
    """В ~30% случаев у кампании есть операционная проблема."""
    if rng.random() > 0.30:
        return None
    pool: list[str] = ["delivery_failure_high", "delivery_latency_high", "low_delivery_rate", "queue_lag"]
    if campaign_kind == "event_triggered":
        pool += ["event_timeout", "no_traffic"]
    if campaign_kind == "pull":
        pool += ["response_timeout"]
    if rng.random() < 0.2:
        pool.append("blocked_by_system")
    return rng.choice(pool)


def _severity_for(issue: str | None) -> str:
    if issue is None:
        return "low"
    if issue in {"blocked_by_system", "no_traffic"}:
        return "critical"
    if issue in {"delivery_failure_high", "event_timeout", "response_timeout"}:
        return "high"
    return "medium"


def _status_for(rng: random.Random, issue: str | None) -> tuple[str, str | None]:
    if issue == "blocked_by_system":
        return "blocked", rng.choice(_BLOCKED_REASONS)
    if issue is None:
        return rng.choices(["running", "paused", "draft", "completed"], weights=[78, 12, 6, 4])[0], None
    return "running", None


def _build_health(
    rng: random.Random,
    campaign: DemoCampaignModel,
    issue_code: str | None,
    severity: str,
    blocked_reason: str | None,
    now: datetime,
) -> CampaignHealthModel:
    if campaign.channel in _CHANNELS_PUSH:
        sent = rng.randint(3_000, 220_000)
        delivery_rate = rng.uniform(96.5, 99.7)
        slow_share = rng.uniform(0.0, 1.5)
        p95_latency = rng.randint(8, 45)
    else:
        sent = rng.randint(500, 25_000)
        delivery_rate = rng.uniform(97.5, 99.9)
        slow_share = rng.uniform(0.0, 0.6)
        p95_latency = rng.randint(2, 12)
    failure_rate = round(100 - delivery_rate, 2)
    event_lag = rng.randint(0, 12) if campaign.campaign_kind == "event_triggered" else None
    response_lag = rng.randint(0, 5) if campaign.campaign_kind == "pull" else None
    queue_lag = rng.randint(0, 3)
    last_traffic = now - timedelta(minutes=rng.randint(1, 45))

    issues: list[dict[str, str]] = []
    actions: list[dict[str, str]] = []
    if issue_code:
        if issue_code == "delivery_failure_high":
            failure_rate = round(rng.uniform(7.0, 24.0), 2)
            delivery_rate = round(100 - failure_rate, 2)
        elif issue_code == "delivery_latency_high":
            slow_share = round(rng.uniform(8.0, 26.0), 2)
            p95_latency = rng.randint(420, 900)
        elif issue_code == "low_delivery_rate":
            delivery_rate = round(rng.uniform(72.0, 88.0), 2)
            failure_rate = round(100 - delivery_rate, 2)
        elif issue_code == "event_timeout":
            event_lag = rng.randint(75, 240)
            last_traffic = now - timedelta(hours=rng.randint(2, 8))
        elif issue_code == "response_timeout":
            response_lag = rng.randint(70, 200)
        elif issue_code == "queue_lag":
            queue_lag = rng.randint(18, 65)
        elif issue_code == "blocked_by_system":
            sent = 0
            delivery_rate = 0.0
            failure_rate = 0.0
            last_traffic = now - timedelta(hours=rng.randint(2, 24))
        elif issue_code == "no_traffic":
            sent = 0
            delivery_rate = 0.0
            failure_rate = 0.0
            last_traffic = now - timedelta(hours=rng.randint(28, 72))
        tpl = _PROBLEM_TEMPLATES[issue_code]
        issues = [{"code": issue_code, "message": str(tpl["message"])}]
        actions = list(tpl["actions"])

    base_score = {"critical": 18, "high": 38, "medium": 58, "low": 90}[severity]
    attention_score = max(0, min(100, base_score + rng.randint(-6, 6)))

    return CampaignHealthModel(
        campaign_id=campaign.id,
        severity=severity,
        attention_score=attention_score,
        messages_sent_24h=sent,
        delivery_rate_pct=round(delivery_rate, 2),
        delivery_failure_rate_pct=round(failure_rate, 2),
        slow_delivery_share_pct=round(slow_share, 2),
        p95_delivery_latency_sec=p95_latency,
        event_lag_minutes=event_lag,
        response_lag_minutes=response_lag,
        queue_lag_minutes=queue_lag,
        last_traffic_at=last_traffic,
        blocked_reason=blocked_reason,
        issues_json=issues,
        recommended_actions_json=actions,
        last_checked_at=now,
    )


if __name__ == "__main__":
    async def _main() -> None:
        await init_db()
        await seed_demo_campaigns()
        print("demo campaigns seeded")

    asyncio.run(_main())
