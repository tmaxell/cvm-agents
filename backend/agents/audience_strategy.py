"""Сбор сигналов для подбора таргет-группы под продукт.

Метод подбора выбирает ПОЛЬЗОВАТЕЛЬ (меню в BuilderAgent), а не код.
Этот модуль:
  • собирает данные о продукте из каталога (taблица product_catalog);
  • решает, какие пункты меню показать (look-alike по подключившим —
    только если продукт найден в каталоге);
  • даёт сегментатору фактуру под выбранный метод.

Методы подбора (меню):
  • nbo                — берём аудиторию, для которой продукт является NBO.
                         Раз продукт указан — считаем, что такая группа есть.
  • lookalike_existing — look-alike по уже подключившим (нужен продукт в каталоге).
  • lookalike_similar  — look-alike по аудитории похожих продуктов.
  • ask_properties     — расспросить пользователя о свойствах продукта.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from agents import product_catalog


# ── Методы подбора и их подписи ───────────────────────────────────────────────

METHOD_LABELS: dict[str, str] = {
    "nbo": "По модели NBO",
    "lookalike_existing": "Look-alike по подключившим",
    "lookalike_similar": "Look-alike по похожим продуктам",
    "ask_properties": "Расспросить о продукте",
}

METHOD_DESCRIPTIONS: dict[str, str] = {
    "nbo": "аудитория, для которой продукт — лучшее следующее предложение",
    "lookalike_existing": "похожие на тех, кто уже подключил этот продукт",
    "lookalike_similar": "похожие на аудиторию схожих продуктов из каталога",
    "ask_properties": "уточнить свойства продукта и подобрать аудиторию по ним",
}


def method_label(method: str) -> str:
    return METHOD_LABELS.get(method, method)


# ── Модель сигналов ───────────────────────────────────────────────────────────

@dataclass(slots=True)
class AudienceSignals:
    """Данные о продукте, собранные для подбора таргет-группы."""
    product: str
    found_in_catalog: bool = False
    catalog_product: dict[str, Any] | None = None
    nbo: dict[str, Any] | None = None                       # NBO-аудитория продукта
    existing_subscribers: int | None = None                 # сколько уже подключили
    similar_products: list[dict[str, Any]] = field(default_factory=list)
    properties: dict[str, Any] = field(default_factory=dict)

    @property
    def menu_methods(self) -> list[str]:
        """Пункты меню выбора метода подбора.

        - nbo: всегда (продукт указан — считаем, что NBO-группа существует);
        - lookalike_existing: только если продукт найден в каталоге
          (иначе неизвестно, кто его подключал);
        - lookalike_similar: всегда;
        - ask_properties: всегда (запасной вариант).
        """
        methods = ["nbo"]
        if self.found_in_catalog:
            methods.append("lookalike_existing")
        methods.append("lookalike_similar")
        methods.append("ask_properties")
        return methods

    def llm_context(self, method: str | None = None) -> dict[str, Any]:
        """Контекст для промпта сегментатора под выбранный метод."""
        return {
            "product": self.product,
            "found_in_catalog": self.found_in_catalog,
            "chosen_method": method,
            "nbo": self.nbo,
            "existing_subscribers": self.existing_subscribers,
            "similar_products": [
                {"name": p.get("name"), "subscribers": p.get("subscribers"),
                 "category": p.get("category")}
                for p in self.similar_products
            ],
            "product_properties": self.properties,
            "guidance": _method_guidance(method),
        }

    def method_intro(self, method: str) -> str:
        """Короткое пояснение пользователю, как строится подбор для метода."""
        if method == "nbo":
            if self.nbo:
                return (
                    f"Беру аудиторию, для которой «{self.product}» определён моделью NBO "
                    "как лучшее следующее предложение."
                )
            return (
                f"Считаю, что для «{self.product}» есть аудитория, которой он подходит "
                "как лучшее следующее предложение (NBO) — построю варианты этой группы."
            )
        if method == "lookalike_existing":
            if self.existing_subscribers:
                return (
                    f"Строю look-alike по {self.existing_subscribers:,} абонентам, "
                    f"уже подключившим «{self.product}»."
                ).replace(",", " ")
            return f"Строю look-alike по абонентам, уже подключившим «{self.product}»."
        if method == "lookalike_similar":
            if self.similar_products:
                names = ", ".join(p.get("name", "") for p in self.similar_products[:3])
                return f"Строю look-alike по аудитории похожих продуктов: {names}."
            return (
                f"Строю look-alike по аудитории продуктов, схожих с «{self.product}» "
                "по назначению и ценовому сегменту."
            )
        return f"Уточняю свойства продукта «{self.product}», чтобы подобрать аудиторию."


def _method_guidance(method: str | None) -> str:
    """Инструкция LLM, как строить гипотезы под выбранный метод."""
    if method == "nbo":
        return (
            "Метод NBO: все гипотезы — это аудитории, для которых продукт является "
            "лучшим следующим предложением. Если есть поле nbo — опирайся на его "
            "description и estimated_size (как demo-оценку). Если nbo пустой — всё равно "
            "считай, что NBO-аудитория для продукта существует, и опиши её по смыслу продукта."
        )
    if method == "lookalike_existing":
        return (
            "Метод look-alike по подключившим: гипотезы — абоненты, похожие по профилю "
            "на тех, кто уже подключил продукт (existing_subscribers), но ещё не купившие его."
        )
    if method == "lookalike_similar":
        return (
            "Метод look-alike по похожим продуктам: гипотезы — аудитория, похожая на "
            "пользователей схожих продуктов (similar_products). Если список пуст — "
            "опирайся на категорию и назначение продукта."
        )
    if method == "ask_properties":
        return (
            "Данных мало — гипотезы строй осторожно, опираясь только на свойства продукта "
            "и общий профиль базы; явно помечай их как demo-only/recommendation-only."
        )
    return ""


# ── Резолвер ──────────────────────────────────────────────────────────────────

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("ё", "е").strip().lower())


def _similar_products(
    matched: dict[str, Any],
    catalog: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Похожие продукты: явный similar_to + продукты той же категории с подключившими."""
    result: list[dict[str, Any]] = []
    seen: set[str] = {_norm(str(matched.get("name") or ""))}

    explicit = {_norm(str(n)) for n in (matched.get("similar_to") or [])}
    for item in catalog:
        name_norm = _norm(str(item.get("name") or ""))
        if name_norm in explicit and name_norm not in seen:
            result.append(item)
            seen.add(name_norm)

    category = matched.get("category")
    if category:
        for item in catalog:
            name_norm = _norm(str(item.get("name") or ""))
            if name_norm in seen:
                continue
            if item.get("category") == category and (item.get("subscribers") or 0) > 0:
                result.append(item)
                seen.add(name_norm)
    return result


async def resolve_audience_signals(product: str) -> AudienceSignals:
    """Собирает данные о продукте из каталога.

    Не падает, если каталог недоступен — возвращает сигналы с
    found_in_catalog=False (тогда меню не покажет look-alike по подключившим).
    """
    signals = AudienceSignals(product=str(product or "").strip() or "продукт")
    try:
        matched = await product_catalog.find_product(signals.product)
    except Exception as exc:  # noqa: BLE001
        print(f"[audience_strategy] product catalog unavailable: {type(exc).__name__}: {exc}")
        return signals

    if matched is None:
        return signals

    signals.found_in_catalog = True
    signals.catalog_product = matched
    signals.properties = dict(matched.get("properties") or {})

    nbo = matched.get("nbo_audience")
    if isinstance(nbo, dict) and nbo:
        signals.nbo = nbo

    subscribers = matched.get("subscribers")
    if isinstance(subscribers, (int, float)) and subscribers > 0:
        signals.existing_subscribers = int(subscribers)

    try:
        catalog = await product_catalog.list_products()
        signals.similar_products = _similar_products(matched, catalog)
    except Exception:  # noqa: BLE001
        pass

    # Отмечаем продукт как недавно использованный.
    product_id = matched.get("id")
    if isinstance(product_id, int):
        try:
            await product_catalog.mark_product_used(product_id)
        except Exception:  # noqa: BLE001
            pass

    return signals
