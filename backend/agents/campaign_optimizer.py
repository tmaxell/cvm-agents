"""Deterministic campaign optimization heuristics.

The optimizer intentionally avoids LLM calls: recommendations must be stable for
identical flow, metrics and campaign status inputs so they can be safely shown in
monitoring and tested in CI.
"""

from __future__ import annotations

from typing import Any

from schemas import MonitorMetrics, OptimizationRecommendation


DELIVERY_BENCHMARKS = {
    "SmsContent": 92.0,
    "FlashSmsContent": 92.0,
    "EmailContent": 85.0,
    "CustomContent": 90.0,
    "JsonContent": 90.0,
    "UssdContent": 90.0,
}
DEFAULT_DELIVERY_BENCHMARK = 90.0
OPEN_RATE_BENCHMARK = 20.0
CLICK_RATE_BENCHMARK = 5.0
CONVERSION_RATE_BENCHMARK = 15.0
MIN_POSITIVE_UPLIFT_PP = 0.0
MIN_MESSAGE_LENGTH = 20

CLICK_OPEN_CONTENT_TYPES = {"EmailContent", "CustomContent", "JsonContent"}
CLICK_OPEN_LABELS = ("email", "push", "пуш", "почт")
POST_LAUNCH_STATUSES = {"active", "paused"}

CATEGORY_PRIORITY = {
    "control_group": 10,
    "channel": 20,
    "flow": 25,
    "offer": 30,
    "content": 40,
    "contact_time": 50,
}


def run(flow: dict, metrics: MonitorMetrics, campaign_status: str) -> list[OptimizationRecommendation]:
    """Build 3-5 deterministic optimization recommendations for a campaign.

    Only campaigns with status ``active`` or ``paused`` are treated as
    post-launch. Draft/editing campaigns get flow-only pre-launch checks so
    delivery, engagement and conversion metrics never leak into editing advice.
    """
    activities = _extract_activities(flow)
    activity_types = {str(activity.get("type") or "") for activity in activities}
    channel_activities = _extract_channel_activities(activities)
    is_post_launch = _is_post_launch(campaign_status)
    recommendations: list[OptimizationRecommendation] = []

    target_activity = _find_activity(activities, "TargetGroupActivity")
    if not _has_control_or_test_group(target_activity):
        recommendations.append(
            OptimizationRecommendation(
                id="control-group-add",
                phase="pre_launch",
                category="control_group",
                change="Добавьте локальную контрольную группу 5–10% или A/B-тест в TargetGroupActivity.",
                reason=(
                    "В flow нет control/test group, поэтому после запуска нельзя надежно отделить "
                    "инкрементальный эффект кампании от органических конверсий."
                ),
                expected_effect="Появится корректная база для расчета uplift и сравнения тестовой группы с контролем.",
                confidence="high",
                source="flow",
                activity_id=_activity_id(target_activity),
            )
        )

    if is_post_launch:
        weakest_channel = _find_weakest_delivery_channel(metrics)
        if weakest_channel:
            benchmark = _delivery_benchmark(weakest_channel.content_type)
            if weakest_channel.delivery_rate < benchmark:
                recommendations.append(
                    OptimizationRecommendation(
                        id=f"delivery-{_slug(weakest_channel.channel_name)}",
                        phase="post_launch",
                        category="channel",
                        change=f"Проверьте канал {weakest_channel.channel_name} и настройте fallback-канал для недоставленных сообщений.",
                        reason=(
                            f"Общий delivery rate {metrics.delivery_rate}%; канал {weakest_channel.channel_name} "
                            f"имеет delivery rate {weakest_channel.delivery_rate}% ниже бенчмарка {benchmark}%."
                        ),
                        expected_effect="Снижение потерь на доставке и рост числа клиентов, дошедших до коммуникации.",
                        confidence="high",
                        source="metrics",
                        activity_id=_match_channel_activity_id(
                            channel_activities,
                            weakest_channel.content_type,
                            weakest_channel.channel_id,
                        ),
                    )
                )
        elif metrics.delivery_rate < DEFAULT_DELIVERY_BENCHMARK:
            recommendations.append(
                OptimizationRecommendation(
                    id="delivery-overall",
                    phase="post_launch",
                    category="channel",
                    change="Проверьте общую доставляемость кампании и добавьте fallback-канал для недоставленных сообщений.",
                    reason=f"Общий delivery rate {metrics.delivery_rate}% ниже бенчмарка {DEFAULT_DELIVERY_BENCHMARK}%.",
                    expected_effect="Меньше потерь на доставке и больше клиентов, дошедших до коммуникации.",
                    confidence="high",
                    source="metrics",
                    activity_id=_activity_id(channel_activities[0]) if channel_activities else None,
                )
            )

    business_transaction = _find_activity(activities, "BusinessTransactionActivity")
    if "BusinessTransactionActivity" not in activity_types:
        recommendations.append(
            OptimizationRecommendation(
                id="business-transaction-add",
                phase="pre_launch",
                category="flow",
                change="Добавьте BusinessTransactionActivity для фиксации целевого действия клиента.",
                reason="В flow нет активности, которая явно фиксирует покупку, подключение или другую целевую бизнес-транзакцию.",
                expected_effect="Команда сможет измерять реальные активации и связывать оптимизацию с бизнес-результатом.",
                confidence="high",
                source="flow",
                activity_id=None,
            )
        )

    if not _has_offer_template(business_transaction):
        recommendations.append(
            OptimizationRecommendation(
                id="offer-template-prelaunch",
                phase="pre_launch",
                category="offer",
                change="Выберите и проверьте offer template до запуска кампании.",
                reason="В flow не найден подтвержденный offerTemplateId в BusinessTransactionActivity.",
                expected_effect="Оффер будет явно связан с целевым действием, а риск запуска с неверным продуктом снизится.",
                confidence="medium",
                source="flow",
                activity_id=_activity_id(business_transaction),
            )
        )

    weak_text_channel = _find_weak_prelaunch_text_channel(channel_activities)
    if weak_text_channel:
        channel_name = str(weak_text_channel.get("name") or _channel_label(weak_text_channel))
        recommendations.append(
            OptimizationRecommendation(
                id=f"content-prelaunch-{_slug(channel_name)}",
                phase="pre_launch",
                category="content",
                change=f"Проверьте текст коммуникации в канале {channel_name}: выгода, CTA и обязательные формулировки должны быть явными.",
                reason="До запуска в коммуникации нет достаточно развернутого текста или он не найден в параметре content.Text.",
                expected_effect="Кампания стартует с понятным сообщением, которое можно корректно сравнивать по open/click/conversion после запуска.",
                confidence="medium",
                source="flow",
                activity_id=_activity_id(weak_text_channel),
            )
        )

    if is_post_launch and metrics.conversion_rate < CONVERSION_RATE_BENCHMARK:
        recommendations.append(
            OptimizationRecommendation(
                id="conversion-offer-template",
                phase="post_launch",
                category="offer",
                change="Проверьте offer template, условия продукта и соответствие оффера выбранному сегменту.",
                reason=f"Кампания {campaign_status}, а conversion rate {metrics.conversion_rate}% ниже бенчмарка {CONVERSION_RATE_BENCHMARK}%.",
                expected_effect="Более релевантный оффер должен повысить долю клиентов, дошедших до целевого действия.",
                confidence="medium",
                source="metrics",
                activity_id=_activity_id(business_transaction),
            )
        )

    if is_post_launch:
        engagement_channel = _find_low_engagement_channel(channel_activities, metrics)
        if engagement_channel:
            channel_name = str(engagement_channel.get("name") or _channel_label(engagement_channel))
            activity_id = _activity_id(engagement_channel)
            reason_parts = []
            if metrics.open_rate < OPEN_RATE_BENCHMARK:
                reason_parts.append(f"open rate {metrics.open_rate}% ниже {OPEN_RATE_BENCHMARK}%")
            if metrics.click_rate < CLICK_RATE_BENCHMARK:
                reason_parts.append(f"click rate {metrics.click_rate}% ниже {CLICK_RATE_BENCHMARK}%")
            recommendations.append(
                OptimizationRecommendation(
                    id=f"content-cta-{_slug(channel_name)}",
                    phase="post_launch",
                    category="content",
                    change=f"Улучшите текст и CTA в канале {channel_name}: сделайте выгоду явной и добавьте один конкретный следующий шаг.",
                    reason="Для Email/Push зафиксирована низкая вовлеченность: " + "; ".join(reason_parts) + ".",
                    expected_effect="Рост открытий/кликов и больше клиентов в нижней части воронки.",
                    confidence="medium",
                    source="metrics",
                    activity_id=activity_id,
                )
            )

        if metrics.control_group and metrics.control_group.uplift_pp <= MIN_POSITIVE_UPLIFT_PP:
            recommendations.append(
                OptimizationRecommendation(
                    id="control-group-uplift-review",
                    phase="post_launch",
                    category="control_group",
                    change="Разберите результат контрольной группы и проверьте сегмент, оффер и правила исключения перед масштабированием.",
                    reason=(
                        f"Uplift относительно контроля {metrics.control_group.uplift_pp} п.п. "
                        f"({metrics.control_group.uplift_percent}%) не показывает положительный инкрементальный эффект."
                    ),
                    expected_effect="Решение о продолжении кампании будет опираться на инкрементальный эффект, а не только на сырые конверсии.",
                    confidence="high",
                    source="metrics",
                    activity_id=_activity_id(target_activity),
                )
            )

    recommendations.append(_contact_window_recommendation(activities, is_post_launch))

    sorted_recommendations = sorted(
        recommendations,
        key=lambda rec: (CATEGORY_PRIORITY[rec.category], rec.id),
    )
    limited = _limit_preserving_contact_window(sorted_recommendations)
    return _ensure_minimum_recommendations(limited, activities, is_post_launch)


def _extract_activities(flow: dict) -> list[dict[str, Any]]:
    activities = flow.get("activities", []) if isinstance(flow, dict) else []
    return [activity for activity in activities if isinstance(activity, dict)]


def _find_activity(activities: list[dict[str, Any]], activity_type: str) -> dict[str, Any] | None:
    return next((activity for activity in activities if activity.get("type") == activity_type), None)


def _extract_channel_activities(activities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [activity for activity in activities if activity.get("type") == "PushCommunicationActivity"]


def _has_control_or_test_group(target_activity: dict[str, Any] | None) -> bool:
    if not target_activity:
        return False
    if bool(target_activity.get("useLocalControlGroup")) or bool(target_activity.get("useTestGroup")):
        return True
    settings = target_activity.get("localControlGroupSettings")
    return isinstance(settings, dict) and bool(settings.get("percent"))


def _has_offer_template(business_transaction: dict[str, Any] | None) -> bool:
    if not business_transaction:
        return False
    return business_transaction.get("offerTemplateId") is not None


def _message_text(activity: dict[str, Any]) -> str:
    content = activity.get("content")
    if not isinstance(content, dict):
        return ""
    parameters = content.get("parameters")
    if not isinstance(parameters, list):
        return ""
    for parameter in parameters:
        if isinstance(parameter, dict) and parameter.get("name") == "Text":
            return str(parameter.get("value") or "").strip()
    return ""


def _find_weak_prelaunch_text_channel(channel_activities: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not channel_activities:
        return None
    return next(
        (activity for activity in channel_activities if len(_message_text(activity)) < MIN_MESSAGE_LENGTH),
        None,
    )


def _find_weakest_delivery_channel(metrics: MonitorMetrics):
    if not metrics.channel_deliveries:
        return None
    return min(metrics.channel_deliveries, key=lambda channel: channel.delivery_rate)


def _delivery_benchmark(content_type: str) -> float:
    return DELIVERY_BENCHMARKS.get(content_type, DEFAULT_DELIVERY_BENCHMARK)


def _match_channel_activity_id(
    channel_activities: list[dict[str, Any]],
    content_type: str,
    channel_id: int | None,
) -> str | None:
    for activity in channel_activities:
        if activity.get("contentType") != content_type:
            continue
        if channel_id is None or activity.get("channelId") == channel_id:
            return _activity_id(activity)
    return None


def _find_low_engagement_channel(
    channel_activities: list[dict[str, Any]],
    metrics: MonitorMetrics,
) -> dict[str, Any] | None:
    if metrics.open_rate >= OPEN_RATE_BENCHMARK and metrics.click_rate >= CLICK_RATE_BENCHMARK:
        return None
    return next((activity for activity in channel_activities if _is_click_open_channel(activity)), None)


def _is_click_open_channel(activity: dict[str, Any]) -> bool:
    content_type = str(activity.get("contentType") or "")
    name = str(activity.get("name") or "").lower()
    return content_type in CLICK_OPEN_CONTENT_TYPES or any(label in name for label in CLICK_OPEN_LABELS)


def _is_post_launch(campaign_status: str) -> bool:
    return campaign_status.strip().lower() in POST_LAUNCH_STATUSES


def _contact_window_recommendation(
    activities: list[dict[str, Any]],
    is_post_launch: bool,
) -> OptimizationRecommendation:
    has_wait = _find_activity(activities, "WaitActivity") is not None
    confidence = "medium" if has_wait else "low"
    if has_wait:
        change = "Проверьте contact window на небольшой holdout-группе и сравните текущую задержку с отправкой в ближайший релевантный слот."
        reason = "В flow уже есть WaitActivity, но исторических данных по оптимальному времени контакта в метриках нет."
    else:
        change = "Добавьте явное contact window или короткую задержку перед коммуникацией, затем проверьте результат на holdout-группе."
        reason = "Исторических данных по лучшему времени контакта нет, поэтому рекомендация по окну отправки остается эвристической."
    if is_post_launch:
        expected_effect = "Можно снизить раздражение клиентов и проверить влияние времени отправки без резкой перестройки активной кампании."
        phase = "post_launch"
    else:
        expected_effect = "До запуска появится управляемая гипотеза о времени контакта, которую можно валидировать метриками."
        phase = "pre_launch"
    return OptimizationRecommendation(
        id="contact-window-review",
        phase=phase,
        category="contact_time",
        change=change,
        reason=reason,
        expected_effect=expected_effect,
        confidence=confidence,
        source="heuristic",
        activity_id=_activity_id(_find_activity(activities, "WaitActivity")),
    )


def _limit_preserving_contact_window(
    recommendations: list[OptimizationRecommendation],
) -> list[OptimizationRecommendation]:
    contact = next((rec for rec in recommendations if rec.category == "contact_time"), None)
    if len(recommendations) <= 5:
        return recommendations
    limited = recommendations[:5]
    if contact and contact not in limited:
        limited[-1] = contact
        limited = sorted(limited, key=lambda rec: (CATEGORY_PRIORITY[rec.category], rec.id))
    return limited


def _ensure_minimum_recommendations(
    recommendations: list[OptimizationRecommendation],
    activities: list[dict[str, Any]],
    is_post_launch: bool,
) -> list[OptimizationRecommendation]:
    if len(recommendations) >= 3:
        return recommendations

    existing_ids = {recommendation.id for recommendation in recommendations}
    fallback_candidates = [
        OptimizationRecommendation(
            id="flow-prelaunch-checklist",
            phase="pre_launch",
            category="flow",
            change="Перед запуском проверьте связку сегмент → канал → целевое действие и зафиксируйте владельца каждой метрики.",
            reason="Детерминированные проверки не нашли достаточно критичных проблем, но кампании нужен базовый pre-launch quality gate.",
            expected_effect="Меньше операционных ошибок при запуске и понятнее интерпретация первых метрик.",
            confidence="medium",
            source="heuristic",
            activity_id=_activity_id(_find_activity(activities, "TargetGroupActivity")),
        ),
    ]
    if is_post_launch:
        fallback_candidates.append(
            OptimizationRecommendation(
                id="postlaunch-metric-watch",
                phase="post_launch",
                category="channel",
                change="После запуска ежедневно отслеживайте delivery, open/click и conversion по каждому каналу отдельно.",
                reason="Без регулярного среза по каналам можно пропустить деградацию доставки или вовлеченности.",
                expected_effect="Быстрее обнаружение проблем канала и меньше потерь в воронке.",
                confidence="medium",
                source="heuristic",
                activity_id=None,
            )
        )
    else:
        fallback_candidates.append(
            OptimizationRecommendation(
                id="content-prelaunch-review",
                phase="pre_launch",
                category="content",
                change="До запуска перечитайте текст коммуникации: один оффер, один CTA и понятная причина обратиться к клиенту сейчас.",
                reason="Для pre-launch режима оптимизатор не использует метрики и добавляет ручной quality gate по тексту.",
                expected_effect="Текст будет готов к честной проверке после запуска без смешения нескольких гипотез.",
                confidence="medium",
                source="heuristic",
                activity_id=_activity_id(_find_activity(activities, "PushCommunicationActivity")),
            )
        )
    enriched = recommendations[:]
    for candidate in fallback_candidates:
        if len(enriched) >= 3:
            break
        if candidate.id not in existing_ids:
            enriched.append(candidate)
            existing_ids.add(candidate.id)
    return sorted(enriched, key=lambda rec: (CATEGORY_PRIORITY[rec.category], rec.id))[:5]


def _activity_id(activity: dict[str, Any] | None) -> str | None:
    if not activity:
        return None
    value = activity.get("id") or activity.get("activityId") or activity.get("nodeId")
    return str(value) if value is not None else None


def _channel_label(activity: dict[str, Any]) -> str:
    content_type = activity.get("contentType")
    if content_type == "EmailContent":
        return "Email"
    if content_type in {"CustomContent", "JsonContent"}:
        return "Push"
    if content_type == "UssdContent":
        return "USSD"
    if content_type == "FlashSmsContent":
        return "Flash SMS"
    return "SMS"


def _slug(value: str) -> str:
    slug = "".join(char.lower() if char.isalnum() else "-" for char in value).strip("-")
    return slug or "channel"
