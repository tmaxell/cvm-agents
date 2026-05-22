"""Доступ к продуктовому каталогу (таблица product_catalog).

Каталог — справочник тарифов/пакетов/услуг. Используется при подборе
таргет-группы: знаем ли продукт, сколько подключивших, какие похожие.
Агент умеет отдать все продукты и последние использованные.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from db import session_scope
from models import ProductCatalogModel


def _to_dict(row: ProductCatalogModel) -> dict[str, Any]:
    """ORM-строку → плоский dict в формате, который ожидают агенты."""
    return {
        "id": row.id,
        "name": row.name,
        "category": row.category,
        "status": row.status,
        "description": row.description,
        "subscribers": row.subscribers,
        "nbo_audience": row.nbo_audience_json,
        "similar_to": list(row.similar_to_json or []),
        "properties": dict(row.properties_json or {}),
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
    }


async def list_products() -> list[dict[str, Any]]:
    """Все продукты каталога (по имени)."""
    async with session_scope() as db:
        rows = (await db.scalars(
            select(ProductCatalogModel).order_by(ProductCatalogModel.name)
        )).all()
        return [_to_dict(r) for r in rows]


async def list_recent_products(limit: int = 8) -> list[dict[str, Any]]:
    """Последние использованные продукты (по last_used_at).

    Продукты без last_used_at идут после использованных. Подходит для
    быстрых подсказок «недавно работали с этими тарифами».
    """
    async with session_scope() as db:
        rows = (await db.scalars(select(ProductCatalogModel))).all()
    products = [_to_dict(r) for r in rows]
    products.sort(
        key=lambda p: (p["last_used_at"] is not None, p["last_used_at"] or ""),
        reverse=True,
    )
    return products[: max(1, limit)]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("ё", "е").strip().lower())


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-zа-я0-9]+", _norm(text)))


async def find_product(name: str) -> dict[str, Any] | None:
    """Ищет продукт в каталоге по имени.

    Сначала точное/подстрочное совпадение, затем — лучшее совпадение по
    пересечению значимых токенов (без стоп-слов «тариф/пакет/услуга/…»).
    """
    if not name:
        return None
    products = await list_products()
    if not products:
        return None

    target_norm = _norm(name)
    for p in products:
        pname = _norm(p["name"])
        if pname and (pname == target_norm or pname in target_norm or target_norm in pname):
            return p

    stop = {"тариф", "пакет", "услуга", "подписка", "продукт", "опция"}
    target_tokens = _tokens(name)
    best, best_score = None, 0.0
    for p in products:
        ptokens = _tokens(p["name"])
        meaningful = (target_tokens & ptokens) - stop
        if not meaningful:
            continue
        score = len(meaningful) / max(1, len((target_tokens | ptokens) - stop))
        if score > best_score:
            best, best_score = p, score
    return best if best_score >= 0.34 else None


async def mark_product_used(product_id: int) -> None:
    """Отмечает продукт как недавно использованный (last_used_at = now)."""
    async with session_scope() as db:
        row = await db.get(ProductCatalogModel, product_id)
        if row is not None:
            row.last_used_at = datetime.now(UTC)
