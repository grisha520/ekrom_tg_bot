"""Доменные модели: что просит клиент, что нашли, что положили в корзину.

Эти модели — общий язык между слоями (парсер → поиск → подбор → корзина).
Слой мессенджера и слой сайта зависят только от них, а не друг от друга.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class ApplianceType(str, Enum):
    """Типы техники, которые умеем подбирать (категории каталога ekrom)."""

    OVEN = "oven"  # Духовой шкаф
    COOKTOP = "cooktop"  # Варочная панель
    HOOD = "hood"  # Вытяжка
    MICROWAVE = "microwave"  # СВЧ / микроволновая печь
    DISHWASHER = "dishwasher"  # Посудомоечная машина
    FRIDGE = "fridge"  # Холодильник
    UNKNOWN = "unknown"


class AttrConstraint(BaseModel):
    """Ограничение по характеристике товара, привязанное к РЕАЛЬНОМУ атрибуту сайта.

    Заполняется схемо-маппером (LLM выбирает `attribute` из настоящих атрибутов
    категории), поэтому сопоставление точное, а не по угадыванию.
    """

    attribute: str                 # точное имя атрибута с сайта («Скорость отжима, оборотов/мин»)
    op: str = "eq"                 # eq | gte | lte | approx
    value: str

    @field_validator("op", mode="before")
    @classmethod
    def _norm_op(cls, v: object) -> object:
        s = str(v or "eq").strip().lower()
        return s if s in {"eq", "gte", "lte", "approx"} else "eq"

    @field_validator("attribute", "value", mode="before")
    @classmethod
    def _to_str(cls, v: object) -> object:
        return "" if v is None else str(v)


class ProductSpec(BaseModel):
    """Одна позиция из запроса клиента в структурированном виде.

    Поля специально необязательные: клиент редко указывает всё.
    `raw_text` хранит исходную формулировку строки для трассировки.
    """

    appliance: ApplianceType = ApplianceType.UNKNOWN
    raw_text: str = ""

    # Категория каталога ekrom, выбранная моделью из data/categories.json
    # (расширение подбора за пределы 6 встроенных типов).
    category_url: Optional[str] = None
    category_name: Optional[str] = None

    brand: Optional[str] = Field(default=None, description="Бренд/производитель, если указан (Korting, Bosch…)")
    model: Optional[str] = Field(default=None, description="Конкретная модель/артикул, если указан (OGG 771 CFX)")

    width_mm: Optional[int] = Field(default=None, description="Ширина, мм (например 600, 450)")
    color: Optional[str] = Field(default=None, description="Цвет: нержавейка, белый, чёрный…")
    install_type: Optional[str] = Field(default=None, description="Тип установки: полновстраиваемая, встраиваемая…")
    fuel_type: Optional[str] = Field(default=None, description="Электрическая / газовая / индукционная")
    burners: Optional[int] = Field(default=None, description="Количество конфорок (для варочных)")

    # Расширенные характеристики для точного подбора.
    volume_l: Optional[int] = Field(default=None, description="Объём, л (холодильник/морозилка/винный шкаф)")
    load_kg: Optional[float] = Field(default=None, description="Загрузка, кг (стиральная/сушильная машина)")
    energy_class: Optional[str] = Field(default=None, description="Класс энергопотребления: A+++/A++/A+/A/B…")
    material: Optional[str] = Field(default=None, description="Материал: нержавейка/камень/керамика/стекло…")

    # Ценовой потолок клиента, ₽ («до 30000», «не дороже 25 тыс»). Если задан —
    # подбор не превышает его (при наличии вариантов в бюджете).
    max_price: Optional[int] = Field(default=None, description="Максимальная цена, ₽")

    # Произвольные дополнительные требования, которые не легли в типовые поля
    extra: dict[str, str] = Field(default_factory=dict)

    # Жёсткие исключения (например «не чёрную», «кроме газовых»)
    exclude_colors: list[str] = Field(default_factory=list)
    exclude_fuel: list[str] = Field(default_factory=list)

    # Ограничения по характеристикам, привязанные к реальным атрибутам категории
    # (заполняет схемо-маппер). Точнее, чем свободный extra.
    attr_constraints: list[AttrConstraint] = Field(default_factory=list)

    @field_validator("attr_constraints", mode="before")
    @classmethod
    def _coerce_constraints(cls, v: object) -> object:
        if v is None:
            return []
        if isinstance(v, list):
            # Отсеиваем мусор без имени атрибута/значения.
            out = []
            for it in v:
                if isinstance(it, dict) and str(it.get("attribute") or "").strip() \
                        and str(it.get("value") or "").strip():
                    out.append(it)
            return out
        return []

    # LLM нередко присылает null вместо пустого списка/словаря — приводим к default,
    # чтобы валидация не падала (поля по схеме всё равно необязательные).
    @field_validator("exclude_colors", "exclude_fuel", mode="before")
    @classmethod
    def _none_to_empty_list(cls, v: object) -> object:
        return [] if v is None else v

    # Некоторые модели (напр. YandexGPT) присылают строку "null" вместо JSON null
    # для пустых необязательных полей. Приводим такие к None, иначе бренд="null"
    # сломает каскад замен, а цвет="null" — фильтрацию по цвету.
    @field_validator(
        "color", "fuel_type", "install_type", "brand", "model",
        "category_url", "category_name", "energy_class", "material", mode="before",
    )
    @classmethod
    def _blank_to_none(cls, v: object) -> object:
        if isinstance(v, str) and v.strip().lower() in {"", "null", "none", "-", "нет"}:
            return None
        return v

    @field_validator("max_price", "volume_l", mode="before")
    @classmethod
    def _coerce_int(cls, v: object) -> object:
        # LLM может прислать «30000», «300 л», число — берём цифры.
        if v is None or v == "":
            return None
        if isinstance(v, bool):  # bool — подкласс int, отсекаем явно
            return None
        if isinstance(v, (int, float)):
            return int(v)
        if isinstance(v, str):
            digits = re.sub(r"[^\d]", "", v)
            return int(digits) if digits else None
        return None

    @field_validator("load_kg", mode="before")
    @classmethod
    def _coerce_float(cls, v: object) -> object:
        # «8 кг», «8.5», «7,5» → float.
        if v is None or v == "":
            return None
        if isinstance(v, bool):
            return None
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            m = re.search(r"\d+(?:[.,]\d+)?", v)
            return float(m.group().replace(",", ".")) if m else None
        return None

    @field_validator("extra", mode="before")
    @classmethod
    def _coerce_extra(cls, v: object) -> object:
        # LLM кладёт в extra и нестроковые значения (например {"slots": 2}) —
        # приводим ключи/значения к строкам, иначе dict[str, str] падает.
        if v is None:
            return {}
        if isinstance(v, dict):
            return {str(k): str(val) for k, val in v.items()}
        return v

    @field_validator("appliance", mode="before")
    @classmethod
    def _coerce_appliance(cls, v: object) -> object:
        # Незнакомый тип (LLM на расширенном каталоге может прислать «kettle»,
        # «blender»…) приводим к UNKNOWN — категорию дальше выберет резолвер.
        if isinstance(v, ApplianceType):
            return v
        if isinstance(v, str):
            try:
                return ApplianceType(v.strip().lower())
            except ValueError:
                return ApplianceType.UNKNOWN
        return ApplianceType.UNKNOWN


class CustomerRequest(BaseModel):
    """Полный разобранный запрос клиента: список позиций + контекст."""

    specs: list[ProductSpec] = Field(default_factory=list)
    segment: Optional[str] = Field(default=None, description="Ценовой сегмент: эконом/средний/премиум")
    notes: Optional[str] = None
    original_message: str = ""

    @field_validator("specs", mode="before")
    @classmethod
    def _none_to_empty_list(cls, v: object) -> object:
        return [] if v is None else v

    @field_validator("segment", "notes", mode="before")
    @classmethod
    def _blank_to_none(cls, v: object) -> object:
        # YandexGPT иногда шлёт строку "null" вместо JSON null — иначе сегмент
        # "null" просачивается в обоснование («Выбран «null» вариант»).
        if isinstance(v, str) and v.strip().lower() in {"", "null", "none", "-"}:
            return None
        return v


class CatalogProduct(BaseModel):
    """Товар из каталога ekrom (результат поиска)."""

    title: str
    url: str
    price: Optional[float] = None
    sku: Optional[str] = None
    brand: Optional[str] = None
    # ID для добавления в корзину (Bitrix product id), если удалось вытащить
    product_id: Optional[str] = None
    # URL главного фото товара (для клиентского PDF), если удалось вытащить
    image_url: Optional[str] = None
    # Цена до скидки (для акционных товаров) — показываем зачёркнутой в PDF
    old_price: Optional[float] = None
    discount_pct: Optional[float] = None
    attributes: dict[str, str] = Field(default_factory=dict)
    # None — ещё не проверяли; True/False — результат проверки на ekrom
    in_stock: Optional[bool] = None


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
