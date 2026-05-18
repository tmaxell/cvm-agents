"""Safety/review checklist for Campaign Builder draft flows."""

from __future__ import annotations

from typing import Any

from schemas import CampaignBrief, ReviewChecklist, ReviewChecklistItem, ReviewStatus

_CHECKLIST_LABELS: dict[str, str] = {
    "audience": "Audience is selected and usable",
    "consent": "Consent/opt-in is checked before outbound messages",
    "contact_policy": "Contact policy and frequency caps are safe for launch",
    "offer": "Offer/product fit is explicit",
    "content": "Message content is present and reviewable",
    "validation": "Flow validation has no blocking errors",
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
    """Return whether create/launch may proceed for a review status."""
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
        return _item("audience", "green", "Audience context and TargetGroupActivity are present.")
    if _has_brief_audience(brief):
        return _item("audience", "warning", "Audience is described in the brief, but the flow has no TargetGroupActivity.")
    return _item("audience", "blocker", "Audience is missing; select or confirm a Target Group before create/launch.")


def _check_consent(draft_flow: dict[str, Any] | None) -> ReviewChecklistItem:
    activities = _activities(draft_flow)
    outbound_indexes = [idx for idx, activity in enumerate(activities) if activity.get("type") in _OUTBOUND_ACTIVITY_TYPES]
    if not outbound_indexes:
        return _item("consent", "warning", "No outbound communication activity found; consent cannot be verified yet.")
    consent_indexes = [
        idx for idx, activity in enumerate(activities)
        if _looks_like_consent_activity(activity)
    ]
    if consent_indexes and min(consent_indexes) < min(outbound_indexes):
        return _item("consent", "green", "Consent/opt-in check appears before outbound communication.")
    return _item("consent", "blocker", "Add a ConsentCheck/opt-in gate before outbound communication.")


def _check_contact_policy(draft_flow: dict[str, Any] | None) -> ReviewChecklistItem:
    activities = _activities(draft_flow)
    if not activities:
        return _item("contact_policy", "blocker", "Draft flow is missing; contact policy cannot be reviewed.")
    if any(_activity_issues(activity, "errors") for activity in activities):
        return _item("contact_policy", "blocker", "One or more flow activities contain blocking errors.")
    if _flow_validation_warnings(draft_flow) or any(_activity_issues(activity, "warnings") for activity in activities):
        return _item("contact_policy", "warning", "Flow has warnings; confirm contactability/frequency caps before create/launch.")
    return _item("contact_policy", "warning", "Contactability, opt-out, and frequency caps require final operator acknowledgement.")


def _check_offer(brief: CampaignBrief | None, draft_flow: dict[str, Any] | None) -> ReviewChecklistItem:
    if _brief_text(getattr(brief, "product", None)) or _brief_text(getattr(getattr(brief, "constraints", None), "offer_recommendations", None)):
        return _item("offer", "green", "Product or offer recommendation is specified.")
    if any(activity.get("type") == "BusinessTransactionActivity" for activity in _activities(draft_flow)):
        return _item("offer", "warning", "Flow activates an offer, but brief product/offer context is incomplete.")
    return _item("offer", "blocker", "Product or offer must be specified before create/launch.")


def _check_content(brief: CampaignBrief | None, draft_flow: dict[str, Any] | None) -> ReviewChecklistItem:
    if _brief_text(getattr(getattr(brief, "constraints", None), "content", None)):
        return _item("content", "green", "Message content is specified in the brief.")
    for activity in _activities(draft_flow):
        if activity.get("type") in _OUTBOUND_ACTIVITY_TYPES and _activity_has_content(activity):
            return _item("content", "green", "Outbound message content is present in the flow.")
    return _item("content", "blocker", "Message content is missing; add copy before create/launch.")


def _check_validation(draft_flow: dict[str, Any] | None, validation_errors: list[Any]) -> ReviewChecklistItem:
    if not draft_flow:
        return _item("validation", "blocker", "No draft flow is available for validation.")
    if validation_errors:
        return _item("validation", "blocker", f"Validation returned {len(validation_errors)} blocking error(s).")
    activity_error_count = sum(len(_activity_issues(activity, "errors")) for activity in _activities(draft_flow))
    if activity_error_count:
        return _item("validation", "blocker", f"Flow activities contain {activity_error_count} error(s).")
    warning_count = len(_flow_validation_warnings(draft_flow)) + sum(len(_activity_issues(activity, "warnings")) for activity in _activities(draft_flow))
    if warning_count:
        return _item("validation", "warning", f"Validation has {warning_count} warning(s).")
    return _item("validation", "green", "No validation errors are present.")


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
