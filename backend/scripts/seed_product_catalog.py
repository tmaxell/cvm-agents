"""Seed product catalog table from MOCK_PRODUCT_CATALOG.

Продуктовый каталог (тарифы, пакеты, услуги) — справочная таблица,
из которой агент подбора аудитории берёт данные о продукте:
есть ли продукт в каталоге, сколько подключивших, какие похожие продукты.

Идемпотентно: продукты сопоставляются по name, существующие обновляются,
last_used_at и created_at сохраняются.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from db import init_db, session_scope
from models import ProductCatalogModel
from tools.mock_data import MOCK_PRODUCT_CATALOG


async def seed_product_catalog() -> None:
    """Загружает / обновляет каталог продуктов из MOCK_PRODUCT_CATALOG."""
    async with session_scope() as db:
        existing = {
            row.name: row
            for row in (await db.scalars(select(ProductCatalogModel))).all()
        }
        for item in MOCK_PRODUCT_CATALOG:
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            fields = dict(
                name=name,
                category=str(item.get("category") or "other"),
                status=str(item.get("status") or "active"),
                description=item.get("description"),
                subscribers=int(item.get("subscribers") or 0),
                nbo_audience_json=item.get("nbo_audience"),
                similar_to_json=list(item.get("similar_to") or []),
                properties_json=dict(item.get("properties") or {}),
            )
            row = existing.get(name)
            if row is None:
                db.add(ProductCatalogModel(**fields))
            else:
                # Обновляем справочные поля, не трогая last_used_at / created_at.
                for key, value in fields.items():
                    setattr(row, key, value)


if __name__ == "__main__":
    async def _main() -> None:
        await init_db()
        await seed_product_catalog()
        print("product catalog seeded")

    asyncio.run(_main())
