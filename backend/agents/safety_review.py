"""Safety/review checklist for Campaign Builder draft flows."""

from __future__ import annotations

from typing import Any

from schemas import CampaignBrief, ReviewChecklist, ReviewChecklistItem, ReviewStatus

_CHECKLIST_LABELS: dict[str, str] = {
    "audience": "Аудитория выбрана и доступна",
    "consent": "Согласия проверены до отправки сообщений",
    "contact_policy": "Контактная политика и лимиты частоты проверены",
    "offer": "Оффер и продукт указаны явно",
    "content": "Текст сообщения заполнен и готов к проверке",
    "validation": "Валидация флоу не нашла критичных ошибок",
}

_OUTBOUND_ACTIVITY_TYPES = {
    "PushCommunicationActivity",
    "PullCommunicationActivity",
}


def build_review_checklist(
    brief: CampaignBrief | None,
    draft_flow: dict[str, Any] | None,
    validation_errors: list[Any] | None = None,
) -> ReviewChecklist:
    """Build a typed checklist from campaign brief, draft flow, and validation errors."""
    items = [
        _check_audience(brief, draft_flow),
        _check_consent(draft_flow),
        _check_contact_policy(draft_flow),
        _check_offer(brief, draft_flow),
        _check_content(brief, draft_flow),
        _check_validation(draft_flow, validation_errors or []),
    ]
    return ReviewChecklist(items=items, status=_overall_status(items))


def is_review_allowed_for_runtime(status: ReviewStatus, acknowledged_warnings: bool) -> bool:
    """Return whether runtime actions may proceed for a review status."""
    if status == "green":
        return True
    if status == "warnings" and acknowledged_warnings:
        return True
    return False


def _overall_status(items: list[ReviewChecklistItem]) -> ReviewStatus:
    if any(item.status == "blocker" for item in items):
        return "blocked"
    if any(item.status == "warning" for item in items):
        return "warnings"
    return "green"


def _check_audience(brief: CampaignBrief | None, draft_flow: dict[str, Any] | None) -> ReviewChecklistItem:
    if _has_brief_audience(brief) and _has_target_group_activity(draft_flow):
        return _item("audience", "green", "Аудитория указана, шаг выбора аудитории есть во флоу.")
    if _has_brief_audience(brief):
        return _item("audience", "warning", "Аудитория описана в брифе, но во флоу нет шага выбора аудитории.")
    return _item("audience", "blocker", "Аудитория не указана; выберите или подтвердите целевую группу.")


def _check_consent(draft_flow: dict[str, Any] | None) -> ReviewChecklistItem:
    activities = _activities(draft_flow)
    outbound_indexes = [idx for idx, activity in enumerate(activities) if activity.get("type") in _OUTBOUND_ACTIVITY_TYPES]
    if not outbound_indexes:
        return _item("consent", "warning", "Во флоу нет исходящего сообщения, поэтому согласия пока нельзя проверить.")
    consent_indexes = [
        idx for idx, activity in enumerate(activities)
        if _looks_like_consent_activity(activity)
    ]
    if consent_indexes and min(consent_indexes) < min(outbound_indexes):
        return _item("consent", "green", "Проверка согласия стоит перед исходящим сообщением.")
    return _item("consent", "blocker", "Добавьте проверку согласия перед исходящим сообщением.")


def _check_contact_policy(draft_flow: dict[str, Any] | None) -> ReviewChecklistItem:
    activities = _activities(draft_flow)
    if not activities:
        return _item("contact_policy", "blocker", "Черновик флоу отсутствует, контактную политику нельзя проверить.")
    if any(_activity_issues(activity, "errors") for activity in activities):
        return _item("contact_policy", "blocker", "Один или несколько шагов флоу содержат критичные ошибки.")
    if _flow_validation_warnings(draft_flow) or any(_activity_issues(activity, "warnings") for activity in activities):
        return _item("contact_policy", "warning", "Во флоу есть предупреждения; подтвердите доступность контакта и лимиты частоты.")
    return _item("contact_policy", "warning", "Доступность контакта, отписка и лимиты частоты требуют финального подтверждения оператора.")


def _check_offer(brief: CampaignBrief | None, draft_flow: dict[str, Any] | None) -> ReviewChecklistItem:
    if _brief_text(getattr(brief, "product", None)) or _brief_text(getattr(getattr(brief, "constraints", None), "offer_recommendations", None)):
        return _item("offer", "green", "Продукт или рекомендация по офферу указаны.")
    if any(activity.get("type") == "BusinessTransactionActivity" for activity in _activities(draft_flow)):
        return _item("offer", "warning", "Во флоу есть активация оффера, но в брифе не хватает контекста по продукту или офферу.")
    return _item("offer", "blocker", "Укажите продукт или оффер.")


def _check_content(brief: CampaignBrief | None, draft_flow: dict[str, Any] | None) -> ReviewChecklistItem:
    if _brief_text(getattr(getattr(brief, "constraints", None), "content", None)):
        return _item("content", "green", "Текст сообщения указан в брифе.")
    for activity in _activities(draft_flow):
        if activity.get("type") in _OUTBOUND_ACTIVITY_TYPES and _activity_has_content(activity):
            return _item("content", "green", "Текст исходящего сообщения есть во флоу.")
    return _item("content", "blocker", "Текст сообщения отсутствует; добавьте копирайтинг.")


def _check_validation(draft_flow: dict[str, Any] | None, validation_errors: list[Any]) -> ReviewChecklistItem:
    if not draft_flow:
        return _item("validation", "blocker", "Черновик флоу недоступен для валидации.")
    if validation_errors:
        return _item("validation", "blocker", f"Валидация вернула критичные ошибки: {len(validation_errors)}.")
    activity_error_count = sum(len(_activity_issues(activity, "errors")) for activity in _activities(draft_flow))
    if activity_error_count:
        return _item("validation", "blocker", f"Шаги флоу содержат ошибки: {activity_error_count}.")
    warning_count = len(_flow_validation_warnings(draft_flow)) + sum(len(_activity_issues(activity, "warnings")) for activity in _activities(draft_flow))
    if warning_count:
        return _item("validation", "warning", f"Валидация вернула предупреждения: {warning_count}.")
    return _item("validation", "green", "Ошибок валидации нет.")


def _item(category: str, status: str, message: str) -> ReviewChecklistItem:
    return ReviewChecklistItem(category=category, label=_CHECKLIST_LABELS[category], status=status, message=message)


def _activities(draft_flow: dict[str, Any] | None) -> list[dict[str, Any]]:
    activities = draft_flow.get("activities") if isinstance(draft_flow, dict) else None
    return [activity for activity in activities if isinstance(activity, dict)] if isinstance(activities, list) else []


def _has_brief_audience(brief: CampaignBrief | None) -> bool:
    audience = getattr(brief, "audience", None)
    if audience is None:
        return False
    if _brief_text(getattr(audience, "description", None)):
        return True
    if any(_brief_text(value) for value in (getattr(audience, "target_groups", None) or [])):
        return True
    selected = getattr(audience, "selected_segment", None)
    return selected is not None and not bool(getattr(selected, "recommendationOnly", False))


def _has_target_group_activity(draft_flow: dict[str, Any] | None) -> bool:
    return any(activity.get("type") == "TargetGroupActivity" for activity in _activities(draft_flow))


def _looks_like_consent_activity(activity: dict[str, Any]) -> bool:
    text = " ".join(str(activity.get(key, "")) for key in ("type", "name", "id")).lower()
    return "consent" in text or "opt" in text or "соглас" in text


def _brief_text(value: Any) -> bool:
    return bool(str(value or "").strip())


def _activity_has_content(activity: dict[str, Any]) -> bool:
    content = activity.get("content")
    if not isinstance(content, dict):
        return False
    parameters = content.get("parameters")
    if not isinstance(parameters, list):
        return False
    return any(_brief_text(parameter.get("value")) for parameter in parameters if isinstance(parameter, dict))


def _activity_issues(activity: dict[str, Any], key: str) -> list[Any]:
    value = activity.get(key)
    return value if isinstance(value, list) else []


def _flow_validation_warnings(draft_flow: dict[str, Any] | None) -> list[Any]:
    validation = draft_flow.get("validation") if isinstance(draft_flow, dict) else None
    warnings = validation.get("warnings") if isinstance(validation, dict) else None
    return warnings if isinstance(warnings, list) else []
