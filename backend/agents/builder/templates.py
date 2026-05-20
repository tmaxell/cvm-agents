"""Готовые шаблоны кампаний — точные структуры из реального AdTarget.

Три эталонных flow из examples/:
- demo_campaign        — большая многоэтапная кампания (mock сложного сценария).
- test_1_data_package  — продажа пакетов Мб через ДСТК с RealTimeCheck и ветвлением.
- test_2_gift          — линейный сценарий «Подарок Соцсети» с TransferToCampaign.

Шаблон вытягивается из JSON, регенерируются UUIDs и обновляется campaign name.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"


@dataclass(slots=True)
class CampaignTemplate:
    """Описание одного шаблона."""
    key: str                       # уникальный код: "data_package", "gift", "demo"
    title: str                     # человекочитаемое название (для ответа)
    description: str               # короткое описание сценария
    keywords: tuple[str, ...]      # триггер-фразы из user message
    file_name: str                 # имя файла в templates/


TEMPLATES: dict[str, CampaignTemplate] = {
    "data_package": CampaignTemplate(
        key="data_package",
        title="Продажа пакета Мб через ДСТК",
        description="Триггер по событию → RealTime-проверка баланса → ветвление на 3 цены → SMS/USSD касание → Interactive/Response → Business transaction начисления пакета.",
        keywords=("пакет данн", "пакет мб", "пакеты мб", "data package", "дстк", "продажа пакет", "интернет пакет"),
        file_name="test_1_data_package_campaign.json",
    ),
    "gift": CampaignTemplate(
        key="gift",
        title="Подарок соцсети при пополнении",
        description="SMS-приглашение → ждём событие пополнения → активируем подарочный пакет → SMS-уведомление → Wait → деактивация → Transfer/Exclude из этапа.",
        keywords=("подарок", "gift", "соцсет", "социальн", "facebook", "vk ", "instagram", "успешн", "пополнен"),
        file_name="test_2_gift_campaign.json",
    ),
    "demo": CampaignTemplate(
        key="demo",
        title="Демонстрационная кампания (полная)",
        description="Многоэтапная сложная демо-кампания со множеством каналов и ветвлений.",
        keywords=("демо", "demo", "сложн", "пример полн"),
        file_name="demo_campaign.json",
    ),
}


def list_templates() -> list[CampaignTemplate]:
    return list(TEMPLATES.values())


def find_template(message: str) -> CampaignTemplate | None:
    """Подбирает шаблон по ключевым словам пользовательского сообщения."""
    if not message:
        return None
    lower = message.lower()
    # Возвращаем первый шаблон, любое ключевое слово которого присутствует в сообщении.
    best: CampaignTemplate | None = None
    best_score = 0
    for tpl in TEMPLATES.values():
        score = sum(1 for kw in tpl.keywords if kw in lower)
        if score > best_score:
            best_score = score
            best = tpl
    return best if best_score > 0 else None


def load_template_flow(template: CampaignTemplate, *, campaign_name: str | None = None) -> dict[str, Any]:
    """Возвращает свежий flow из шаблона: регенерирует id-шники, проставляет даты и название кампании."""
    path = _TEMPLATES_DIR / template.file_name
    raw = json.loads(path.read_text(encoding="utf-8"))

    # 1. Регенерируем UUID для всех активностей (старые из реального AdTarget — не годятся).
    activities = raw.get("activities") or []
    id_map: dict[str, str] = {}
    for act in activities:
        old = act.get("id")
        if isinstance(old, str) and old:
            id_map[old] = str(uuid4())

    transition_keys = (
        "nextActivityId",
        "defaultSuccessActivityId",
        "defaultFailActivityId",
        "timeOutNextActivityId",
        "invalidTimeNextActivityId",
    )

    for act in activities:
        old_id = act.get("id")
        if isinstance(old_id, str):
            act["id"] = id_map.get(old_id, str(uuid4()))
        for key in transition_keys:
            value = act.get(key)
            if isinstance(value, str) and value in id_map:
                act[key] = id_map[value]
        cases = act.get("cases")
        if isinstance(cases, dict):
            act["cases"] = {k: id_map.get(v, v) if isinstance(v, str) else v for k, v in cases.items()}

    # 2. Перебиваем расписание на текущий год (примеры из 2023-24 — у пользователя могут быть «в прошлом»).
    _update_schedule(activities)

    # 3. Подменяем имя кампании в CommonActivity (если задано).
    if campaign_name:
        for act in activities:
            if act.get("type") == "CommonActivity":
                act["name"] = campaign_name
                break

    raw["activities"] = activities
    return raw


# ── Helpers ───────────────────────────────────────────────────────────────────

def _update_schedule(activities: list[dict[str, Any]]) -> None:
    """Сдвигает schedule.period в актуальный год (now → now + 1 year)."""
    tz = timezone(timedelta(hours=5))
    now = datetime.now(tz)
    end = now + timedelta(days=365)
    begin_iso = now.replace(microsecond=0).isoformat()
    end_iso = end.replace(microsecond=0).isoformat()
    for act in activities:
        schedule = act.get("schedule")
        if not isinstance(schedule, dict):
            continue
        period = schedule.get("period")
        if not isinstance(period, dict):
            continue
        period["beginDate"] = begin_iso
        period["tzBeginDate"] = begin_iso
        period["endDate"] = end_iso
        period["tzEndDate"] = end_iso


def derive_campaign_name(message: str, fallback: str) -> str:
    """Извлекает человеческое название кампании из user message."""
    if not message:
        return fallback
    # Пытаемся достать продукт после «продукт ...» / «тариф ...»
    m = re.search(r"(?:продукт[ауеа]?|тариф)\s+[«\"']?([^.,;\n«\"']+)[»\"']?", message, re.IGNORECASE)
    if m:
        product = m.group(1).strip()
        return f"Кампания «{product[:60]}»"
    # Иначе первая фраза до точки/запятой
    first = re.split(r"[.,;]", message.strip(), maxsplit=1)[0]
    first = " ".join(first.split())
    if len(first) > 60:
        first = first[:57] + "…"
    return first or fallback
