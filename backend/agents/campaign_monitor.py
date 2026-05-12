"""
F3 — Campaign Monitor Agent

Прямой LLM-вызов (без LangGraph) для анализа кампании и выдачи рекомендаций.
Получает campaign_id + flow JSON и возвращает:
  - Метрики (моковые, но детерминированные по campaign_id + refresh_seed)
  - Список рекомендаций от LLM
  - Общую оценку

Точка входа: run(request: MonitorRequest) -> MonitorResponse
"""

import json
import random
import re

from langchain_core.messages import HumanMessage, SystemMessage

from llm import get_llm
from schemas import MonitorRequest, MonitorMetrics, MonitorResponse


# ── Детерминированная генерация метрик ────────────────────────────────────────

def _generate_metrics(campaign_id: int, refresh_seed: int, activities: list[dict]) -> MonitorMetrics:
    """Генерирует правдоподобные метрики на основе campaign_id и типа flow.

    Используем seeded random, чтобы одна и та же кампания давала одинаковые числа.
    refresh_seed позволяет «обновить» метрики при явном запросе.
    """
    rng = random.Random(campaign_id * 1000 + refresh_seed)

    activity_types = [a.get("type", "") for a in activities]
    has_event = "EventActivity" in activity_types
    has_bt = "BusinessTransactionActivity" in activity_types
    has_email = any(
        a.get("contentType") == "EmailContent" or "Email" in a.get("name", "")
        for a in activities
    )

    # Базовые диапазоны по типу канала
    if has_email:
        delivery_base = (72, 91)
        open_base = (18, 34)
        click_base = (3, 9)
    else:  # SMS/Push
        delivery_base = (88, 97)
        open_base = (45, 72)
        click_base = (8, 18)

    # Event-triggered кампании обычно релевантнее
    if has_event:
        conv_base = (12, 28)
    elif has_bt:
        conv_base = (8, 20)
    else:
        conv_base = (4, 12)

    delivery_rate = round(rng.uniform(*delivery_base), 1)
    open_rate = round(rng.uniform(*open_base), 1)
    click_rate = round(rng.uniform(*click_base), 1)
    conversion_rate = round(rng.uniform(*conv_base), 1)

    return MonitorMetrics(
        delivery_rate=delivery_rate,
        open_rate=open_rate,
        conversion_rate=conversion_rate,
        click_rate=click_rate,
    )


# ── Системный промпт ──────────────────────────────────────────────────────────

MONITOR_SYSTEM_PROMPT = """Ты — CVM-аналитик платформы AdTarget. Анализируешь кампанию и даёшь конкретные рекомендации.

На вход — описание flow кампании (список активностей) и метрики.

Верни ответ ТОЛЬКО в формате JSON без markdown-обёртки:
{
  "overall_score": <число 0-100>,
  "summary": "<2-3 предложения о кампании>",
  "recommendations": [
    "<рекомендация 1>",
    "<рекомендация 2>",
    "<рекомендация 3>",
    "<рекомендация 4>"
  ]
}

Правила оценки (overall_score):
- 80-100: отличная кампания, верный сегмент, есть событие-триггер и/или бизнес-транзакция
- 60-79: хорошая, но есть очевидные улучшения
- 40-59: средняя, явные пробелы в структуре
- <40: слабая, критические недостатки

Правила рекомендаций:
- Пиши конкретно и actionable, без воды
- Каждая рекомендация — одно предложение
- Если нет EventActivity — рекомендуй добавить триггер
- Если нет BusinessTransactionActivity — рекомендуй добавить для закрепления результата
- Если есть Event — предложи добавить Wait перед коммуникацией
- Давай советы по тексту сообщения, времени отправки, сегменту, A/B тесту
- Ориентируйся на отраслевые бенчмарки: SMS delivery >92%, conversion >15%
"""


# ── Основная функция ──────────────────────────────────────────────────────────

async def run(request: MonitorRequest) -> MonitorResponse:
    """Анализирует кампанию и возвращает метрики + рекомендации."""
    # Парсим flow
    try:
        flow_data = json.loads(request.draft_flow_json)
        activities = flow_data.get("activities", [])
    except (json.JSONDecodeError, TypeError):
        activities = []

    # Генерируем метрики детерминированно
    metrics = _generate_metrics(request.campaign_id, request.refresh_seed, activities)

    # Подготавливаем краткое описание flow для LLM (только нужные поля)
    flow_summary = []
    for act in activities:
        entry = {
            "type": act.get("type"),
            "name": act.get("name"),
        }
        if act.get("eventCode"):
            entry["eventCode"] = act["eventCode"]
        if act.get("contentType"):
            entry["contentType"] = act["contentType"]
        if act.get("clientSourceId"):
            entry["targetGroupId"] = act["clientSourceId"]
        if act.get("offerTemplateId"):
            entry["offerTemplateId"] = act["offerTemplateId"]
        # Текст сообщения (первые 100 символов)
        content = act.get("content", {})
        if content:
            for param in content.get("parameters", []):
                if param.get("name") == "Text":
                    entry["messageText"] = param.get("value", "")[:100]
        flow_summary.append(entry)

    user_message = (
        f"Кампания ID: {request.campaign_id}\n\n"
        f"Структура flow ({len(activities)} активностей):\n"
        f"{json.dumps(flow_summary, ensure_ascii=False, indent=2)}\n\n"
        f"Текущие метрики:\n"
        f"- Доставка: {metrics.delivery_rate}%\n"
        f"- Прочтения/Открытия: {metrics.open_rate}%\n"
        f"- Переходы: {metrics.click_rate}%\n"
        f"- Конверсия: {metrics.conversion_rate}%\n\n"
        f"Дай оценку и рекомендации по улучшению этой кампании."
    )

    llm = get_llm(for_tools=False)
    try:
        response = await llm.ainvoke([
            SystemMessage(content=MONITOR_SYSTEM_PROMPT),
            HumanMessage(content=user_message),
        ])
        raw = response.content.strip()

        # Стрипаем markdown-обёртку если есть
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        data = json.loads(raw)
        return MonitorResponse(
            metrics=metrics,
            overall_score=int(data.get("overall_score", 65)),
            summary=str(data.get("summary", "")),
            recommendations=list(data.get("recommendations", [])),
        )
    except Exception as e:
        print(f"[campaign_monitor] LLM error: {e}")
        # Fallback без LLM
        return MonitorResponse(
            metrics=metrics,
            overall_score=62,
            summary="Кампания создана. Анализ временно недоступен.",
            recommendations=[
                "Добавьте событие-триггер для повышения релевантности.",
                "Включите бизнес-транзакцию в конце flow для фиксации результата.",
                "Проверьте текст сообщения на соответствие сегменту ЦГ.",
                "Настройте A/B тест для оптимизации конверсии.",
            ],
        )
