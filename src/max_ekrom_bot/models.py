"""Доменные модели: что просит клиент, что нашли, что положили в корзину.

Эти модели — общий язык между слоями (парсер → поиск → подбор → корзина).
Слой мессенджера и слой сайта зависят только от них, а не друг от друга.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ApplianceType(str, Enum):
    """Типы техники, которые умеем подбирать (категории каталога ekrom)."""

    OVEN = "oven"  # Духовой шкаф
    COOKTOP = "cooktop"  # Варочная панель
    HOOD = "hood"  # Вытяжка
    MICROWAVE = "microwave"  # СВЧ / микроволновая печь
    DISHWASHER = "dishwasher"  # Посудомоечная машина
    FRIDGE = "fridge"  # Холодильник
    UNKNOWN = "unknown"


class ProductSpec(BaseModel):
    """Одна позиция из запроса клиента в структурированном виде.

    Поля специально необязательные: клиент редко указывает всё.
    `raw_text` хранит исходную формулировку строки для трассировки.
    """

    appliance: ApplianceType = ApplianceType.UNKNOWN
    raw_text: str = ""

    width_mm: Optional[int] = Field(default=None, description="Ширина, мм (например 600, 450)")
    color: Optional[str] = Field(default=None, description="Цвет: нержавейка, белый, чёрный…")
    install_type: Optional[str] = Field(default=None, description="Тип установки: полновстраиваемая, встраиваемая…")
    fuel_type: Optional[str] = Field(default=None, description="Электрическая / газовая / индукционная")
    burners: Optional[int] = Field(default=None, description="Количество конфорок (для варочных)")

    # Произвольные дополнительные требования, которые не легли в типовые поля
    extra: dict[str, str] = Field(default_factory=dict)

    # Жёсткие исключения (например «не чёрную»)
    exclude_colors: list[str] = Field(default_factory=list)


class CustomerRequest(BaseModel):
    """Полный разобранный запрос клиента: список позиций + контекст."""

    specs: list[ProductSpec] = Field(default_factory=list)
    segment: Optional[str] = Field(default=None, description="Ценовой сегмент: эконом/средний/премиум")
    notes: Optional[str] = None
    original_message: str = ""


class CatalogProduct(BaseModel):
    """Товар из каталога ekrom (результат поиска)."""

    title: str
    url: str
    price: Optional[float] = None
    sku: Optional[str] = None
    brand: Optional[str] = None
    # ID для добавления в корзину (Bitrix product id), если удалось вытащить
    product_id: Optional[str] = None
    attributes: dict[str, str] = Field(default_factory=dict)


class MatchedItem(BaseModel):
    """Подобранный под одну позицию запроса товар."""

    spec: ProductSpec
    product: Optional[CatalogProduct] = None
    candidates_count: int = 0
    reason: str = ""  # почему выбран именно этот вариант (для подтверждения клиенту)

    @property
    def is_matched(self) -> bool:
        return self.product is not None


class SelectionResult(BaseModel):
    """Итог подбора по всему запросу — то, что показываем на подтверждение."""

    items: list[MatchedItem] = Field(default_factory=list)

    @property
    def matched(self) -> list[MatchedItem]:
        return [i for i in self.items if i.is_matched]

    @property
    def unmatched(self) -> list[MatchedItem]:
        return [i for i in self.items if not i.is_matched]

    @property
    def total_price(self) -> float:
        return sum(i.product.price or 0 for i in self.matched)
