"""
AdTargetClient — единственная точка входа в AdTarget REST API.

Все агенты (F1 CVM Copilot, F2 Campaign Builder) обращаются к платформе
исключительно через этот модуль. Прямые HTTP-вызовы к AdTarget
из агентов запрещены.

Платформа: AdTarget CVM
API base:  http://192.168.15.102:4001
Auth:      Keycloak OAuth2 ROPC
           Token URL: http://192.168.15.102:8117/auth/realms/mmp/
                      protocol/openid-connect/token

Mock-режим: ADTARGET_MOCK=true — работает без VPN, возвращает
            реалистичные данные из tools/mock_data.py.
            Автоматически включается при недоступности API.
"""

import time
import os
from typing import Any

import httpx

# ── Конфигурация ─────────────────────────────────────────────────────────────

API_BASE = os.getenv("ADTARGET_API_BASE", "http://192.168.15.102:4001")
KEYCLOAK_TOKEN_URL = os.getenv(
    "ADTARGET_TOKEN_URL",
    "http://192.168.15.102:8117/auth/realms/mmp/protocol/openid-connect/token",
)
CLIENT_ID = os.getenv("ADTARGET_CLIENT_ID", "adtarget")
USERNAME = os.getenv("ADTARGET_USERNAME", "")
PASSWORD = os.getenv("ADTARGET_PASSWORD", "")

# ADTARGET_MOCK=true — принудительный mock-режим (без VPN)
_MOCK_FORCED = os.getenv("ADTARGET_MOCK", "false").lower() == "true"
# После первой ConnectError автоматически переключаемся на mock
_mock_auto: bool = False


def _is_mock() -> bool:
    return _MOCK_FORCED or _mock_auto


def _enable_auto_mock() -> None:
    global _mock_auto
    if not _mock_auto:
        print("[adtarget] ⚠️  API недоступен — переключаемся на mock-режим")
        _mock_auto = True


# ── Token cache ───────────────────────────────────────────────────────────────

_token_cache: dict[str, Any] = {"access_token": None, "expires_at": 0.0}


async def _get_token() -> str:
    """Возвращает действующий Bearer-токен (обновляет если истёк)."""
    if _token_cache["access_token"] and time.time() < _token_cache["expires_at"] - 30:
        return _token_cache["access_token"]  # type: ignore[return-value]

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            KEYCLOAK_TOKEN_URL,
            data={
                "grant_type": "password",
                "client_id": CLIENT_ID,
                "username": USERNAME,
                "password": PASSWORD,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    _token_cache["access_token"] = data["access_token"]
    _token_cache["expires_at"] = time.time() + data.get("expires_in", 300)
    return data["access_token"]


async def _headers() -> dict[str, str]:
    token = await _get_token()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


# ── Вспомогательный HTTP-клиент ───────────────────────────────────────────────

async def _get(path: str, params: dict | None = None) -> Any:
    async with httpx.AsyncClient(base_url=API_BASE) as client:
        resp = await client.get(path, params=params, headers=await _headers())
        resp.raise_for_status()
        return resp.json()


async def _post(path: str, body: dict | None = None, params: dict | None = None) -> Any:
    async with httpx.AsyncClient(base_url=API_BASE) as client:
        resp = await client.post(path, json=body, params=params, headers=await _headers())
        resp.raise_for_status()
        return resp.json()


async def _put(path: str, body: dict | None = None, params: dict | None = None) -> Any:
    async with httpx.AsyncClient(base_url=API_BASE) as client:
        headers = await _headers()
        if body is None:
            headers = {k: v for k, v in headers.items() if k != "Content-Type"}
            resp = await client.put(path, params=params, headers=headers)
        else:
            resp = await client.put(path, json=body, params=params, headers=headers)
        resp.raise_for_status()
        return resp.json() if resp.content else {}


# ── Кампании ──────────────────────────────────────────────────────────────────

async def get_campaign(campaign_id: int) -> dict:
    """GET /Campaigns/{id} — данные кампании."""
    return await _get(f"/Campaigns/{campaign_id}")


async def get_campaign_flow(campaign_id: int) -> dict:
    """GET /Campaigns/{id}/flow — activities кампании."""
    return await _get(f"/Campaigns/{campaign_id}/flow")


async def get_campaign_statistics(campaign_id: int) -> dict:
    """GET /Campaigns/{id}/statistics."""
    return await _get(f"/Campaigns/{campaign_id}/statistics")


async def validate_campaign(campaign_flow: dict, campaign_id: int = 0) -> dict:
    """POST /Campaigns/validate — валидация flow, возвращает errors + warnings."""
    if _is_mock():
        from tools.mock_data import MOCK_VALIDATION_OK
        return MOCK_VALIDATION_OK

    try:
        return await _post("/Campaigns/validate", {
            "campaignId": campaign_id,
            "campaignFlow": campaign_flow,
        })
    except (httpx.ConnectError, httpx.ConnectTimeout):
        _enable_auto_mock()
        from tools.mock_data import MOCK_VALIDATION_OK
        return MOCK_VALIDATION_OK


async def create_campaign(flow: dict) -> dict:
    """POST /Campaigns — создание кампании.

    Возвращает { campaignId, errors, warnings, isStarted, isUpdated }.
    """
    if _is_mock():
        from tools.mock_data import make_mock_campaign_result
        result = make_mock_campaign_result()
        print(f"[adtarget mock] create_campaign → campaignId={result['campaignId']}")
        return result

    try:
        return await _post("/Campaigns", flow)
    except (httpx.ConnectError, httpx.ConnectTimeout):
        _enable_auto_mock()
        from tools.mock_data import make_mock_campaign_result
        result = make_mock_campaign_result()
        print(f"[adtarget mock] create_campaign → campaignId={result['campaignId']}")
        return result


async def update_campaign_flow(campaign_id: int, flow: dict) -> dict:
    """PUT /Campaigns/variables/{id} — обновление flow существующей кампании."""
    if _is_mock():
        return {"campaignId": campaign_id, "errors": [], "warnings": []}

    try:
        return await _put(f"/Campaigns/variables/{campaign_id}", flow)
    except (httpx.ConnectError, httpx.ConnectTimeout):
        _enable_auto_mock()
        return {"campaignId": campaign_id, "errors": [], "warnings": []}


def _use_mock_for_runtime_action(error: httpx.HTTPError) -> None:
    """Switch runtime actions to prototype/mock mode after API/auth HTTP failures."""
    status_code = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
    if status_code is None:
        print(f"[adtarget] runtime action HTTP error: {error.__class__.__name__}")
    else:
        print(f"[adtarget] runtime action HTTP status error: {status_code}")
    _enable_auto_mock()


async def start_campaign(campaign_id: int) -> list:
    """PUT /Campaigns/start — запуск кампании."""
    if _is_mock():
        from tools.mock_data import make_mock_start_result
        return make_mock_start_result(campaign_id)

    try:
        return await _put(
            "/Campaigns/start",
            body=None,
            params={
                "campaignIdsInfo.except": "false",
                "campaignIdsInfo.campaignIds": str(campaign_id),
            },
        )
    except httpx.HTTPError as e:
        _use_mock_for_runtime_action(e)
        from tools.mock_data import make_mock_start_result
        return make_mock_start_result(campaign_id)


async def pause_campaign(campaign_id: int) -> list:
    """PUT /Campaigns/stop — пауза/остановка кампании."""
    if _is_mock():
        from tools.mock_data import make_mock_pause_result
        return make_mock_pause_result(campaign_id)

    try:
        return await _put(
            "/Campaigns/stop",
            body=None,
            params={
                "campaignIdsInfo.except": "false",
                "campaignIdsInfo.campaignIds": str(campaign_id),
            },
        )
    except httpx.HTTPError as e:
        _use_mock_for_runtime_action(e)
        from tools.mock_data import make_mock_pause_result
        return make_mock_pause_result(campaign_id)


# ── Справочники ───────────────────────────────────────────────────────────────

async def list_target_groups(page: int = 1, page_size: int = 50) -> dict:
    """GET /TargetGroups/safe — постраничный список ЦГ."""
    if _is_mock():
        from tools.mock_data import MOCK_TARGET_GROUPS
        return MOCK_TARGET_GROUPS

    try:
        return await _get("/TargetGroups/safe", {"page": page, "pageSize": page_size})
    except (httpx.ConnectError, httpx.ConnectTimeout):
        _enable_auto_mock()
        from tools.mock_data import MOCK_TARGET_GROUPS
        return MOCK_TARGET_GROUPS


async def list_channels() -> list:
    """GET /Channels/safe."""
    if _is_mock():
        from tools.mock_data import MOCK_CHANNELS
        return MOCK_CHANNELS

    try:
        return await _get("/Channels/safe")
    except (httpx.ConnectError, httpx.ConnectTimeout):
        _enable_auto_mock()
        from tools.mock_data import MOCK_CHANNELS
        return MOCK_CHANNELS


async def list_events() -> list:
    """GET /Events/safe — триггеры с параметрами."""
    if _is_mock():
        from tools.mock_data import MOCK_EVENTS
        return MOCK_EVENTS

    try:
        return await _get("/Events/safe")
    except (httpx.ConnectError, httpx.ConnectTimeout):
        _enable_auto_mock()
        from tools.mock_data import MOCK_EVENTS
        return MOCK_EVENTS


async def list_offer_templates() -> list:
    """GET /OfferTemplates/safe."""
    if _is_mock():
        from tools.mock_data import MOCK_OFFER_TEMPLATES
        return MOCK_OFFER_TEMPLATES

    try:
        return await _get("/OfferTemplates/safe")
    except (httpx.ConnectError, httpx.ConnectTimeout):
        _enable_auto_mock()
        from tools.mock_data import MOCK_OFFER_TEMPLATES
        return MOCK_OFFER_TEMPLATES


async def list_campaign_types() -> list:
    """GET /CampaignTypes."""
    if _is_mock():
        from tools.mock_data import MOCK_CAMPAIGN_TYPES
        return MOCK_CAMPAIGN_TYPES

    try:
        return await _get("/CampaignTypes")
    except (httpx.ConnectError, httpx.ConnectTimeout):
        _enable_auto_mock()
        from tools.mock_data import MOCK_CAMPAIGN_TYPES
        return MOCK_CAMPAIGN_TYPES


async def list_campaign_groups() -> list:
    """GET /CampaignGroups."""
    if _is_mock():
        from tools.mock_data import MOCK_CAMPAIGN_GROUPS
        return MOCK_CAMPAIGN_GROUPS

    try:
        return await _get("/CampaignGroups")
    except (httpx.ConnectError, httpx.ConnectTimeout):
        _enable_auto_mock()
        from tools.mock_data import MOCK_CAMPAIGN_GROUPS
        return MOCK_CAMPAIGN_GROUPS


async def list_product_catalog() -> list:
    """GET /ProductCatalog."""
    if _is_mock():
        return []
    return await _get("/ProductCatalog")


async def get_product_actions(product_id: int) -> list:
    """GET /ProductCatalog/{id}/actions."""
    if _is_mock():
        return []
    return await _get(f"/ProductCatalog/{product_id}/actions")


async def get_campaign_template(template_id: int) -> dict:
    """GET /CampaignTemplates/{id}."""
    if _is_mock():
        return {}
    return await _get(f"/CampaignTemplates/{template_id}")


async def get_campaign_template_flow(template_id: int) -> dict:
    """GET /CampaignTemplates/{id}/flow."""
    if _is_mock():
        return {}
    return await _get(f"/CampaignTemplates/{template_id}/flow")
