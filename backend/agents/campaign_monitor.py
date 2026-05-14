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
from schemas import (
    ChannelDeliveryMetric,
    ControlGroupComparison,
    MonitorMetrics,
    MonitorRequest,
    MonitorResponse,
)


CHANNEL_LABELS = {
    "SmsContent": "SMS",
    "EmailContent": "Email",
    "CustomContent": "Push",
    "UssdContent": "USSD",
    "FlashSmsContent": "Flash SMS",
    "JsonContent": "Push",
}

CLICK_BASED_CONTENT_TYPES = {"EmailContent", "CustomContent", "JsonContent"}


# ── Детерминированная генерация метрик ────────────────────────────────────────

def _extract_channel_activities(activities: list[dict]) -> list[dict]:
    """Возвращает коммуникационные активности, по которым нужны доставки."""
    channels = [a for a in activities if a.get("type") == "PushCommunicationActivity"]
    if channels:
        return channels
    # Для старых/неполных flow показываем хотя бы один SMS-канал, чтобы UI не был пустым.
    return [{"contentType": "SmsContent", "channelId": None, "name": "SMS"}]


def _extract_audience_and_control(activities: list[dict], rng: random.Random) -> tuple[int, int]:
    """Оценивает размер тестовой и контрольной групп по TargetGroupActivity."""
    target = next((a for a in activities if a.get("type") == "TargetGroupActivity"), {})
    target_group_id = int(target.get("clientSourceId") or 0)
    # Детерминированная, но правдоподобная база: разные ЦГ дают разные объёмы.
    audience_size = 8_000 + (target_group_id * 137) % 42_000 if target_group_id else rng.randint(12_000, 45_000)

    control_size = 0
    if target.get("useLocalControlGroup"):
        settings = target.get("localControlGroupSettings") or {}
        control_percent = int(settings.get("percent") or 10)
        control_size = max(1, round(audience_size * control_percent / 100))
    elif target.get("useTestGroup"):
        # Если в flow явно включено тестирование без процента — резервируем 10%.
        control_size = max(1, round(audience_size * 0.1))

    return audience_size - control_size, control_size


def _generate_metrics(campaign_id: int, refresh_seed: int, activities: list[dict]) -> MonitorMetrics:
    """Генерирует правдоподобные метрики на основе campaign_id и типа flow.

    Помимо процентов отдаём абсолютные количества: отправки/доставки по каждому
    каналу, суммарные доставки, активации и comparison с контрольной группой,
    если в TargetGroupActivity включено тестирование/контрольная группа.
    """
    rng = random.Random(campaign_id * 1000 + refresh_seed)

    activity_types = [a.get("type", "") for a in activities]
    has_event = "EventActivity" in activity_types
    has_bt = "BusinessTransactionActivity" in activity_types
    channel_activities = _extract_channel_activities(activities)
    has_email = any(a.get("contentType") == "EmailContent" or "Email" in a.get("name", "") for a in channel_activities)
    has_click_based_flow = any(a.get("contentType") in CLICK_BASED_CONTENT_TYPES for a in channel_activities)

    test_group_size, control_group_size = _extract_audience_and_control(activities, rng)

    # Базовые диапазоны по типу канала. Семантика процентов фиксирована:
    # open_rate — от доставленных, click_rate — от открывших, conversion_rate —
    # от кликнувших для click-based flow и от доставленных для SMS/event flow.
    if has_email:
        open_base = (18, 34)
    elif has_click_based_flow:
        open_base = (45, 72)
    else:
        open_base = (60, 88)

    click_base = (3, 9) if has_email else (8, 18) if has_click_based_flow else (0, 0)

    # Event-triggered кампании обычно релевантнее. Для click-based flow конверсия
    # считается от кликнувших, поэтому диапазон выше, чем для delivered-based flow.
    if has_click_based_flow:
        if has_event:
            conv_base = (28, 58)
        elif has_bt:
            conv_base = (22, 48)
        else:
            conv_base = (16, 36)
    elif has_event:
        conv_base = (12, 28)
    elif has_bt:
        conv_base = (8, 20)
    else:
        conv_base = (4, 12)

    channel_deliveries: list[ChannelDeliveryMetric] = []
    sent_total = 0
    delivered_total = 0

    for index, channel in enumerate(channel_activities):
        content_type = channel.get("contentType") or "SmsContent"
        if content_type == "EmailContent":
            delivery_base = (72, 91)
        elif content_type == "CustomContent":
            delivery_base = (78, 94)
        else:
            delivery_base = (88, 97)

        # Если каналов несколько, не вся аудитория обязательно получает каждый канал.
        coverage = 1 if len(channel_activities) == 1 else rng.uniform(0.45, 0.85)
        sent_count = max(1, round(test_group_size * coverage))
        delivery_rate = round(rng.uniform(*delivery_base), 1)
        delivered_count = round(sent_count * delivery_rate / 100)

        sent_total += sent_count
        delivered_total += delivered_count
        channel_deliveries.append(ChannelDeliveryMetric(
            channel_id=channel.get("channelId"),
            channel_name=CHANNEL_LABELS.get(content_type, channel.get("name") or content_type),
            content_type=content_type,
            sent_count=sent_count,
            delivered_count=delivered_count,
            delivery_rate=delivery_rate,
        ))

    delivery_rate = round(delivered_total / sent_total * 100, 1) if sent_total else 0.0
    open_rate = round(rng.uniform(*open_base), 1)
    opened_count = min(delivered_total, round(delivered_total * open_rate / 100))
    click_rate = round(rng.uniform(*click_base), 1) if has_click_based_flow else 0.0
    clicked_count = min(opened_count, round(opened_count * click_rate / 100)) if has_click_based_flow else 0
    conversion_rate = round(rng.uniform(*conv_base), 1)
    conversion_base_count = clicked_count if has_click_based_flow else delivered_total
    activation_count = min(conversion_base_count, round(conversion_base_count * conversion_rate / 100))

    control_group = None
    if control_group_size > 0:
        # Контрольная группа обычно ниже тестовой: нет/меньше воздействия кампании.
        delta = rng.uniform(1.5, 5.5)
        control_conversion_rate = round(max(0.5, conversion_rate - delta), 1)
        control_conversion_base_count = round(control_group_size * conversion_base_count / test_group_size) if test_group_size else 0
        control_activations = min(control_conversion_base_count, round(control_conversion_base_count * control_conversion_rate / 100))
        uplift_pp = round(conversion_rate - control_conversion_rate, 1)
        uplift_percent = round((uplift_pp / control_conversion_rate * 100), 1) if control_conversion_rate else 0.0
        control_group = ControlGroupComparison(
            test_group_size=test_group_size,
            control_group_size=control_group_size,
            test_conversion_rate=conversion_rate,
            control_conversion_rate=control_conversion_rate,
            uplift_pp=uplift_pp,
            uplift_percent=uplift_percent,
            test_activations=activation_count,
            control_activations=control_activations,
        )

    return MonitorMetrics(
        delivery_rate=delivery_rate,
        open_rate=open_rate,
        conversion_rate=conversion_rate,
        click_rate=click_rate,
        sent_count=sent_total,
        delivered_count=delivered_total,
        opened_count=opened_count,
        clicked_count=clicked_count,
        activation_count=activation_count,
        channel_deliveries=channel_deliveries,
        control_group=control_group,
    )


# ── Системный промпт ──────────────────────────────────────────────────────────

MONITOR_SYSTEM_PROMPT = """Ты — CVM-аналитик платформы AdTarget. Анализируешь кампанию и даёшь конкретные рекомендации.

На вход — описание flow кампании (список активностей) и метрики: доставки/активации, доставки по каналам, сравнение с контрольной группой, если включено тестирование.

Верни ответ ТОЛЬКО в формате JSON без markdown-обёртки:
{
  "overall_score": <число 0-100>,
  "summary": "<2-3 предложения о кампании>",
  "structure_recommendations": [
    "<рекомендация по структуре flow 1>",
    "<рекомендация по структуре flow 2>"
  ],
  "launch_recommendations": [
    "<рекомендация по метрикам после запуска 1>",
    "<рекомендация по метрикам после запуска 2>"
  ],
  "similar_campaign_actions": [
    "<что обычно делали в похожих успешных кампаниях 1>",
    "<что обычно делали в похожих успешных кампаниях 2>"
  ]
}

Правила оценки (overall_score):
- 80-100: отличная кампания, верный сегмент, есть событие-триггер и/или бизнес-транзакция, метрики выше бенчмарков
- 60-79: хорошая, но есть очевидные улучшения
- 40-59: средняя, явные пробелы в структуре или метриках
- <40: слабая, критические недостатки

Правила рекомендаций:
- Пиши конкретно и actionable, без воды
- Каждая рекомендация — одно предложение
- structure_recommendations: только советы по flow, сегменту, событиям, wait, бизнес-транзакции, A/B/control setup
- launch_recommendations: только советы по фактическим метрикам после запуска — доставки по каналам, активации, uplift к контролю, текст/время отправки
- similar_campaign_actions: типовые успешные действия из похожих кампаний — контрольная группа, wait, fallback-канал, транзакция, персонализация оффера
- Если нет EventActivity — рекомендуй добавить триггер в structure_recommendations
- Если нет BusinessTransactionActivity — рекомендуй добавить для фиксации результата в structure_recommendations
- Если есть Event — предложи добавить Wait перед коммуникацией в structure_recommendations
- Если есть контрольная группа — обязательно интерпретируй uplift и конверсию контроля в launch_recommendations
- Ориентируйся на бенчмарки: SMS delivery >92%, conversion/activation >15%
"""


def _fallback_structure_recommendations(activities: list[dict]) -> list[str]:
    activity_types = {a.get("type", "") for a in activities}
    recs: list[str] = []
    if "EventActivity" not in activity_types:
        recs.append("Добавьте событие-триггер, чтобы коммуникация отправлялась в момент максимальной релевантности.")
    if "BusinessTransactionActivity" not in activity_types:
        recs.append("Добавьте BusinessTransactionActivity для фиксации активации продукта или скидочного пакета.")
    if "EventActivity" in activity_types and "WaitActivity" not in activity_types:
        recs.append("Проверьте необходимость WaitActivity после события, чтобы не отправлять сообщение слишком рано.")
    recs.append("Зафиксируйте контрольную группу или A/B-тест, чтобы измерять инкрементальный эффект кампании.")
    return recs[:4]




def _fallback_similar_campaign_actions(activities: list[dict]) -> list[str]:
    activity_types = {a.get("type", "") for a in activities}
    actions: list[str] = []
    if "BusinessTransactionActivity" not in activity_types:
        actions.append("В похожих промо-кампаниях добавляли BusinessTransactionActivity для автоматической активации оффера и точного измерения результата.")
    if "EventActivity" in activity_types and "WaitActivity" not in activity_types:
        actions.append("Для событийных кампаний часто добавляли короткий Wait перед коммуникацией, чтобы снизить раздражение клиента после события.")
    if "TargetGroupActivity" in activity_types:
        actions.append("В успешных прошлых кампаниях фиксировали локальную контрольную группу 5–10%, чтобы отделить эффект кампании от органических активаций.")
    if not any(a.get("contentType") == "EmailContent" for a in activities):
        actions.append("Для сегментов с низким откликом добавляли fallback Email или Push-ветку после SMS, если первая коммуникация не дала отклик.")
    return actions[:4]

def _fallback_launch_recommendations(metrics: MonitorMetrics) -> list[str]:
    recs: list[str] = []
    if metrics.channel_deliveries:
        weakest = min(metrics.channel_deliveries, key=lambda ch: ch.delivery_rate)
        if weakest.delivery_rate < 92:
            recs.append(
                f"Проверьте доставляемость канала {weakest.channel_name}: {weakest.delivery_rate}% ниже целевого уровня 92%."
            )
    if metrics.activation_count:
        recs.append(f"Отслеживайте качество активаций: сейчас зафиксировано {metrics.activation_count:,} целевых действий.".replace(",", " "))
    if metrics.control_group:
        recs.append(
            f"Сравните результат с контролем: uplift {metrics.control_group.uplift_pp} п.п. ({metrics.control_group.uplift_percent}%) показывает инкрементальный эффект."
        )
    if metrics.conversion_rate < 15:
        recs.append("Оптимизируйте оффер, текст и время отправки: конверсия ниже бенчмарка 15%.")
    return recs[:4] or ["Метрики выглядят стабильно; продолжайте мониторить доставки и активации по каждому каналу."]


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
            entry["channelId"] = act.get("channelId")
        if act.get("clientSourceId"):
            entry["targetGroupId"] = act["clientSourceId"]
            entry["useLocalControlGroup"] = act.get("useLocalControlGroup")
            entry["localControlGroupSettings"] = act.get("localControlGroupSettings")
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
        f"Кампания ID: {request.campaign_id}\n"
        f"Статус кампании: {request.campaign_status}\n\n"
        f"Структура flow ({len(activities)} активностей):\n"
        f"{json.dumps(flow_summary, ensure_ascii=False, indent=2)}\n\n"
        f"Текущие метрики:\n"
        f"- Отправлено всего: {metrics.sent_count}\n"
        f"- Доставлено всего: {metrics.delivered_count} ({metrics.delivery_rate}% от отправленных)\n"
        f"- Прочитано/открыто: {metrics.opened_count} ({metrics.open_rate}% от доставленных)\n"
        f"- Переходы: {metrics.clicked_count} ({metrics.click_rate}% от открывших; 0 для каналов без кликов)\n"
        f"- Активации: {metrics.activation_count}\n"
        f"- Конверсия/активации: {metrics.conversion_rate}% от кликнувших для click-based flow, иначе от доставленных\n"
        f"- Доставки по каналам: {json.dumps([c.model_dump() for c in metrics.channel_deliveries], ensure_ascii=False)}\n"
        f"- Контрольная группа: {json.dumps(metrics.control_group.model_dump() if metrics.control_group else None, ensure_ascii=False)}\n\n"
        f"Дай оценку, отдельно рекомендации по структуре кампании, похожим прошлым действиям и рекомендации после запуска. "
        f"Если статус editing — фокусируйся на доработке flow до запуска; если active/paused — интерпретируй метрики."
    )

    try:
        llm = get_llm(for_tools=False)
        response = await llm.ainvoke([
            SystemMessage(content=MONITOR_SYSTEM_PROMPT),
            HumanMessage(content=user_message),
        ])
        raw = response.content.strip()

        # Стрипаем markdown-обёртку если есть
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        data = json.loads(raw)
        structure_recs = list(data.get("structure_recommendations") or [])
        launch_recs = list(data.get("launch_recommendations") or [])
        similar_actions = list(data.get("similar_campaign_actions") or [])
        legacy_recs = list(data.get("recommendations") or [])
        if not structure_recs and not launch_recs and legacy_recs:
            midpoint = max(1, len(legacy_recs) // 2)
            structure_recs = legacy_recs[:midpoint]
            launch_recs = legacy_recs[midpoint:]
        if not structure_recs:
            structure_recs = _fallback_structure_recommendations(activities)
        if not launch_recs:
            launch_recs = _fallback_launch_recommendations(metrics)
        if not similar_actions:
            similar_actions = _fallback_similar_campaign_actions(activities)

        return MonitorResponse(
            metrics=metrics,
            overall_score=int(data.get("overall_score", 65)),
            summary=str(data.get("summary", "")),
            structure_recommendations=structure_recs,
            launch_recommendations=launch_recs,
            similar_campaign_actions=similar_actions,
            recommendations=structure_recs + similar_actions + launch_recs,
        )
    except Exception as e:
        print(f"[campaign_monitor] LLM error: {e}")
        structure_recs = _fallback_structure_recommendations(activities)
        launch_recs = _fallback_launch_recommendations(metrics)
        similar_actions = _fallback_similar_campaign_actions(activities)
        return MonitorResponse(
            metrics=metrics,
            overall_score=62,
            summary="Кампания создана. Метрики рассчитаны, AI-анализ временно недоступен.",
            structure_recommendations=structure_recs,
            launch_recommendations=launch_recs,
            similar_campaign_actions=similar_actions,
            recommendations=structure_recs + similar_actions + launch_recs,
        )
