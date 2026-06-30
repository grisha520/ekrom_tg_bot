"""Оркестрация ядра: сообщение клиента → подбор → корзина → PDF.

Не зависит от мессенджера. Может вызываться из бота, CLI или тестов.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import Settings, get_settings
from .ekrom.catalog import build_category_url, category_for
from .ekrom.client import EkromAuthError, EkromCartError, EkromClient, product_slug
from .matching import ProductSelector
from .models import (
    ApplianceType,
    CatalogProduct,
    CustomerRequest,
    MatchedItem,
    ProductSpec,
    SelectionResult,
)
from .parsing import get_parser
from .parsing.factory import Parser

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    request: CustomerRequest
    selection: SelectionResult
    pdf_path: Path | None = None


# Сколько кандидатов максимум проверять на странице товара (остальные — слишком медленно).
_MAX_AVAILABILITY_CHECKS = 15

# Сколько страниц товаров максимум открывать при поиске точной модели (за ценой/
# наличием). Ограничивает добавочное время для запросов с конкретным артикулом.
_MAX_MODEL_FETCHES = 3

# Сколько позиций обрабатывать одновременно (вкладок браузера параллельно).
# Нагрузка сетевая (вкладки ждут ответ ekrom), поэтому держим выше числа ядер —
# ограничение теперь RAM (вкладка Chromium ~0.1–0.2 ГБ). На 2 vCPU / 4 ГБ — 6
# (покрывает многопозиционные запросы целиком). На 1 vCPU стоит снизить до 2.
_MAX_PARALLEL_SPECS = 6

# Размер шортлиста для LLM-ре-ранка (#6): сколько доступных+обогащённых
# кандидатов собираем, прежде чем отдать модели на выбор.
_LLM_SHORTLIST_K = 5

# Ключи характеристик, которые показываем LLM в шортлисте (по подстроке имени).
_RERANK_ATTR_KEYS = (
    "тип", "вид", "цвет", "ширина", "конфор", "производит", "управлен", "очист", "объ",
)


def _candidate_brief(product: CatalogProduct) -> str:
    """Краткое описание кандидата для LLM: название, цена и ключевые характеристики."""
    head = product.title
    if product.price is not None:
        head = f"{head} — {product.price:.0f} ₽"
    attrs = []
    for k, v in (product.attributes or {}).items():
        if any(s in k.lower() for s in _RERANK_ATTR_KEYS):
            attrs.append(f"{k}: {v}")
        if len(attrs) >= 8:
            break
    return head + ("; " + "; ".join(attrs) if attrs else "")


_RERANK_SYSTEM = """\
Ты выбираешь ОДИН товар из списка под запрос клиента на встраиваемую технику.
Учитывай ВСЕ заданные требования (тип, цвет, размер, топливо, число конфорок,
бренд/модель) и ценовой сегмент. Если указаны бренд/модель — при прочих равных
предпочитай именно их. Верни СТРОГО JSON-объект:
{"index": <номер выбранного варианта, целое>, "reason": "<краткое обоснование по-русски>"}
Никакого текста, кроме JSON."""


def _rerank_prompts(spec: ProductSpec, products: list[CatalogProduct], segment: str) -> tuple[str, str]:
    """Системный и пользовательский промпты для LLM-ре-ранка шортлиста."""
    req = [f"Тип техники: {spec.appliance.value}", f"Ценовой сегмент: {segment}"]
    if spec.brand:
        req.append(f"Бренд: {spec.brand}")
    if spec.model:
        req.append(f"Модель: {spec.model}")
    if spec.color:
        req.append(f"Цвет: {spec.color}")
    if spec.fuel_type:
        req.append(f"Тип/топливо: {spec.fuel_type}")
    if spec.burners:
        req.append(f"Число конфорок: {spec.burners}")
    if spec.width_mm:
        req.append(f"Ширина, мм: {spec.width_mm}")
    if spec.exclude_colors:
        req.append("Исключить цвета: " + ", ".join(spec.exclude_colors))

    cands = [f"Вариант {i}: {_candidate_brief(p)}" for i, p in enumerate(products, 1)]
    user = "Запрос клиента:\n" + "\n".join(req) + "\n\nКандидаты:\n" + "\n".join(cands)
    return _RERANK_SYSTEM, user


def _model_tokens(model: str | None) -> list[str]:
    """Значимые токены модели/артикула (буквы/цифры), напр. 'OGG 771 CFX'."""
    if not model:
        return []
    toks = [t for t in re.split(r"[^a-zа-я0-9]+", model.lower()) if t]
    # Отсеиваем слишком короткие токены-однобуквенники (кроме цифр) — шумные.
    return [t for t in toks if len(t) >= 2 or t.isdigit()]


def _title_matches_model(title: str, tokens: list[str]) -> bool:
    """Все значимые токены модели присутствуют в названии товара."""
    if not tokens:
        return False
    low = title.lower()
    return all(t in low for t in tokens)


def _title_has_brand(title: str, brand: str) -> bool:
    return bool(brand) and brand.lower() in title.lower()


class Pipeline:
    def __init__(self, settings: Settings | None = None, parser: Parser | None = None) -> None:
        self._settings = settings or get_settings()
        self._parser = parser or get_parser(self._settings)
        self._llm = None  # ленивый LLM-провайдер для ре-ранка (#6)
        self._resolver = None  # ленивый резолвер категорий
        self._cache = None  # ленивый кэш каталога

    def _get_cache(self):
        """Кэш каталога (или None, если выключен в настройках)."""
        if not self._settings.use_catalog_cache:
            return None
        if self._cache is None:
            from .ekrom.catalog_cache import CatalogCache

            self._cache = CatalogCache(self._settings.catalog_db_path)
        return self._cache

    def _get_llm(self):
        if self._llm is None:
            from .llm import get_llm_provider

            self._llm = get_llm_provider(self._settings)
        return self._llm

    def _get_resolver(self):
        if self._resolver is None:
            from .parsing.category_resolver import CategoryResolver

            self._resolver = CategoryResolver(settings=self._settings)
        return self._resolver

    def parse_message(self, message: str) -> CustomerRequest:
        """Разобрать текст клиента в структурированный запрос (только LLM, без сети сайта).

        Для позиций, не легших в 6 встроенных типов, дополнительно выбираем
        категорию каталога моделью (расширение на весь каталог).
        """
        request = self._parser.parse(message)
        if any(s.appliance is ApplianceType.UNKNOWN for s in request.specs):
            try:
                self._get_resolver().resolve(request.specs)
            except Exception:  # noqa: BLE001 — выбор категории не критичен
                logger.warning("выбор категории не удался", exc_info=True)
        return request

    async def select_products(
        self, request: CustomerRequest, enrich_chosen: bool = False
    ) -> SelectionResult:
        """Найти и подобрать товары под каждую позицию запроса (параллельно).

        Позиции независимы, поэтому обрабатываем их одновременно — каждая в
        своей вкладке браузера. Одновременность ограничена семафором, чтобы не
        перегружать сайт ekrom.

        enrich_chosen=True — выбранный товар дочитывается (характеристики + фото)
        на том же браузере, для клиентского PDF. Стоит чуть больше времени, но не
        запускает второй браузер.
        """
        selector = ProductSelector(segment=request.segment)
        async with EkromClient(self._settings) as ekrom:
            sem = asyncio.Semaphore(_MAX_PARALLEL_SPECS)

            async def handle(spec: ProductSpec) -> MatchedItem:
                async with sem:
                    page = await ekrom.new_page()
                    try:
                        candidates = await self._candidates_for_spec(ekrom, spec, page)
                        item = await self._select_available(
                            ekrom, selector, spec, candidates, page
                        )
                        if enrich_chosen and item.product is not None:
                            await ekrom.ensure_enriched(
                                item.product, page, with_image=True
                            )
                            cache = self._get_cache()
                            if cache is not None:
                                cache.update_enrichment(
                                    item.product.url,
                                    item.product.attributes,
                                    item.product.image_url,
                                )
                        return item
                    finally:
                        await page.close()

            items = await asyncio.gather(*(handle(spec) for spec in request.specs))
        return SelectionResult(items=list(items))

    async def _select_available(
        self,
        ekrom: EkromClient,
        selector: ProductSelector,
        spec: ProductSpec,
        candidates: list,
        page=None,
    ) -> MatchedItem:
        """Подобрать первый подходящий товар в наличии с учётом бренда/модели.

        Каскад приоритетов (если клиент указал бренд/модель):
          1) точная модель;
          2) тот же бренд;
          3) любой подходящий (текущая сегментная стратегия).
        Если запрошенная модель/бренд недоступны и выбран вариант ниже по
        каскаду — помечаем результат как замену.
        """
        ranked = selector.ranked_candidates(spec, candidates)
        if not ranked:
            return MatchedItem(
                spec=spec,
                product=None,
                candidates_count=len(candidates),
                reason="Подходящих вариантов с ценой не найдено",
            )

        want_model, want_brand, tiers = self._build_tiers(spec, ranked)

        # LLM-ре-ранк (#6) — включаемый слой. По умолчанию (флаг выкл) работает
        # прежний детерминированный выбор «первого подходящего в наличии».
        if self._settings.llm_rerank:
            chosen, chosen_tier, llm_reason = await self._rerank_with_llm(
                ekrom, selector, spec, tiers, page
            )
        else:
            chosen, chosen_tier = await self._first_available(
                ekrom, selector, spec, tiers, page
            )
            llm_reason = None

        if chosen is None:
            return MatchedItem(
                spec=spec,
                product=None,
                candidates_count=len(candidates),
                reason="Подходящие варианты найдены, но нет в наличии на ekrom",
            )

        reason = self._build_reason(
            selector, spec, chosen, ranked, chosen_tier, want_model, want_brand, base=llm_reason
        )
        return MatchedItem(
            spec=spec,
            product=chosen,
            candidates_count=len(candidates),
            reason=reason,
        )

    @staticmethod
    def _build_tiers(spec: ProductSpec, ranked: list) -> tuple[bool, bool, list[tuple[str, list]]]:
        """Разложить кандидатов по приоритету: точная модель → бренд → прочие."""
        tokens = _model_tokens(spec.model)
        brand = (spec.brand or "").strip().lower()
        want_model = bool(tokens)
        want_brand = bool(brand)

        exact = [p for p in ranked if want_model and _title_matches_model(p.title, tokens)]
        same_brand = [
            p for p in ranked
            if want_brand and _title_has_brand(p.title, brand) and p not in exact
        ]
        rest = [p for p in ranked if p not in exact and p not in same_brand]

        tiers: list[tuple[str, list]] = []
        if exact:
            tiers.append(("exact", exact))
        if same_brand:
            tiers.append(("brand", same_brand))
        tiers.append(("rest", rest))
        return want_model, want_brand, tiers

    async def _first_available(self, ekrom, selector, spec, tiers, page):
        """Первый подходящий товар по приоритету тиров (детерминированно).

        Доступность оцениваем ОПТИМИСТИЧНО по данным карточки: отбрасываем только
        товары, которые карточка ЯВНО пометила «нет в наличии». Страницу товара
        ради статуса не открываем — на слабом сервере это медленно и ненадёжно
        (карточки под нагрузкой не отдают статус → ложное «нет в наличии»).
        Фактический состав всё равно перепроверяется при добавлении в корзину.

        Цвет: если он подтверждён названием — доверяем карточке (быстрый путь).
        Если нет (напр. «BLANC») — открываем страницу и валидируем по реальным
        характеристикам; валидация снисходительна к оттенкам (графит≈чёрный), но
        отсекает явно другой цвет.
        """
        for tier_name, group in tiers:
            scan = group
            # Замена бренда/модели: запрошенного нет, берём аналог из «прочих».
            # Чтобы замена не улетала в премиум (Indesit → встраиваемый за 96к),
            # перебираем аналоги от самых дешёвых — клиент получает бюджетную
            # замену, а не дорогую. На обычный подбор (без бренда) не влияет.
            if tier_name == "rest" and (spec.brand or spec.model):
                scan = sorted(group, key=lambda p: p.price if p.price is not None else float("inf"))
            for product in scan[:_MAX_AVAILABILITY_CHECKS]:
                if product.in_stock is False:
                    continue
                if not selector.color_confirmed_by_title(spec, product):
                    await ekrom.ensure_enriched(product, page)
                    if not selector.passes_filters(spec, product):
                        continue
                return product, tier_name
        return None, None

    async def _rerank_with_llm(self, ekrom, selector, spec, tiers, page):
        """#6: собрать шортлист доступных+обогащённых кандидатов и выбрать лучший
        моделью. При любой осечке LLM — детерминированный фолбэк на первый.

        Возвращает (product, tier_name, llm_reason|None).
        """
        shortlist: list[tuple[str, CatalogProduct]] = []
        for tier_name, group in tiers:
            for product in group[:_MAX_AVAILABILITY_CHECKS]:
                if len(shortlist) >= _LLM_SHORTLIST_K:
                    break
                if product.in_stock is False:  # оптимистично: только явный OOS
                    continue
                await ekrom.ensure_enriched(product, page)
                if not selector.passes_filters(spec, product):
                    continue
                shortlist.append((tier_name, product))
            if len(shortlist) >= _LLM_SHORTLIST_K:
                break

        if not shortlist:
            return None, None, None
        if len(shortlist) == 1:
            tier_name, product = shortlist[0]
            return product, tier_name, None

        idx, reason = await self._llm_pick(
            spec, [p for _, p in shortlist], selector._segment
        )
        tier_name, product = shortlist[idx]
        return product, tier_name, reason

    async def _llm_pick(
        self, spec: ProductSpec, products: list[CatalogProduct], segment: str
    ) -> tuple[int, str | None]:
        """Спросить LLM, какой кандидат лучший. Возвращает (индекс, обоснование).

        Любая ошибка (сеть/таймаут/кривой JSON/индекс вне диапазона) → (0, None):
        детерминированный фолбэк на первого кандидата шортлиста.
        """
        try:
            system_prompt, user_prompt = _rerank_prompts(spec, products, segment)
            # complete_json синхронный (OpenAI SDK) — уводим в поток, чтобы не
            # блокировать event loop во время параллельной обработки позиций.
            raw = await asyncio.to_thread(
                self._get_llm().complete_json, system_prompt, user_prompt
            )
            data = json.loads(raw)
            idx = int(data.get("index")) - 1  # в промпте варианты с 1
            reason = (str(data.get("reason") or "")).strip() or None
            if 0 <= idx < len(products):
                return idx, reason
            logger.warning("LLM-ре-ранк вернул индекс вне диапазона: %s", data.get("index"))
        except Exception:  # noqa: BLE001 — ре-ранк не критичен, есть фолбэк
            logger.warning("LLM-ре-ранк не удался — фолбэк на детерминированный выбор", exc_info=True)
        return 0, None

    @staticmethod
    def _build_reason(
        selector: ProductSelector,
        spec: ProductSpec,
        chosen,
        ranked: list,
        chosen_tier: str | None,
        want_model: bool,
        want_brand: bool,
        base: str | None = None,
    ) -> str:
        """Текст-обоснование с пометкой о замене, если запрошенное недоступно.

        base — готовое обоснование (например, от LLM-ре-ранка); если не задано,
        берём детерминированное объяснение селектора.
        """
        if base is None:
            base = selector._explain(spec, chosen, ranked)
        req_label = " ".join(x for x in [spec.brand, spec.model] if x).strip()

        # Замена по модели: просили конкретную модель, но взяли не её. Точную
        # причину (нет в наличии / нет на сайте) мы не всегда знаем, поэтому
        # формулируем аккуратно — «не найдена среди доступных».
        if want_model and chosen_tier != "exact":
            same = " (тот же бренд)" if chosen_tier == "brand" else ""
            return (
                f"Запрошенная модель «{req_label}» не найдена среди доступных на ekrom. "
                f"Предложена замена{same}. {base}"
            )
        # Замена по бренду: просили только бренд, но взяли другой бренд.
        if want_brand and not want_model and chosen_tier == "rest":
            return (
                f"Бренд «{spec.brand}» не найден среди доступных на ekrom. "
                f"Предложена замена. {base}"
            )
        # Запрошенная модель найдена и в наличии — отметим это явно.
        if want_model and chosen_tier == "exact":
            return f"Запрошенная модель в наличии. {base}"
        return base

    async def _candidates_for_spec(self, ekrom: EkromClient, spec, page=None) -> list:
        """Кандидаты под позицию (гибрид):

        - известный из 6 типов → смарт-фильтр Bitrix (быстро), откат к категории
          без фильтров при пустой выдаче;
        - иначе категория, выбранная моделью (расширение каталога) → общий обход;
        - иначе текстовый поиск как последний вариант.
        Если указан бренд/модель — дополняем выдачу прямым поиском.
        """
        base = self._settings.ekrom_base_url
        candidates: list = []
        cat_path = category_for(spec.appliance)  # не None только для 6 типов
        filtered_url = build_category_url(base, spec)

        # Кэш каталога: отдаём кандидатов из БД (если свежо) — без браузера;
        # фильтрацию по характеристикам делает селектор клиентски. Ключ — путь
        # категории: для 6 базовых типов это cat_path, для прочих — category_url
        # (резолвер). Краулер наполняет обе группы.
        cache = self._get_cache()
        cache_key = cat_path or spec.category_url
        if cache and cache_key:
            age = cache.category_age(cache_key)
            if age is not None and age < self._settings.catalog_cache_ttl:
                candidates = cache.query_category(cache_key)

        if not candidates and filtered_url:
            candidates = await ekrom.browse_url(filtered_url, page=page)
            if not candidates and cat_path:
                # фильтры могли пересушить выдачу — откат к категории без фильтров
                candidates = await ekrom.browse_category(cat_path, page=page)
        elif not candidates and spec.category_url:
            # категория выбрана моделью — идём в неё (фильтруем уже клиентски)
            url = base.rstrip("/") + spec.category_url
            candidates = await ekrom.browse_url(url, page=page)
        elif not candidates:
            # категории нет — текстовый поиск как последний вариант
            candidates = await ekrom.search(spec.raw_text or spec.appliance.value, page=page)

        # #1: в мелких разделах карточки иногда без цены (например обогреватели) —
        # тогда подбор отсеивает всё как «без цены». Дочитываем цену/наличие со
        # страниц первых товаров (кап ограничивает время).
        if candidates and not any(c.price is not None for c in candidates):
            for product in candidates[:_MAX_MODEL_FETCHES]:
                await ekrom.fetch_product_details(product, page)

        # Если указана конкретная модель — ищем её прямым поиском по сайту и
        # дочитываем цену/наличие со страниц совпавших товаров (точной модели
        # часто нет в первых карточках категории). Бренд без модели не ищем —
        # его закрывает тир «тот же бренд» из категории.
        tokens = _model_tokens(spec.model)
        if tokens:
            query = " ".join(x for x in [spec.brand, spec.model] if x).strip()
            try:
                found = await ekrom.search(query, page=page)
            except Exception:  # noqa: BLE001 — поиск не критичен
                found = []
            seen = {c.url for c in candidates}
            matches = [
                c for c in found
                if c.url not in seen and _title_matches_model(c.title, tokens)
            ][:_MAX_MODEL_FETCHES]
            for product in matches:
                await ekrom.fetch_product_details(product, page)
            candidates += [c for c in matches if c.price is not None]
        return candidates

    async def is_ekrom_authenticated(self) -> bool:
        """Только проверка статуса сессии ekrom (без попытки входа)."""
        async with EkromClient(self._settings) as ekrom:
            return await ekrom.is_authenticated()

    async def refresh_ekrom_session(self) -> bool:
        """Проверить/обновить сессию ekrom.

        Возвращает True, если вход уже был активен (ничего делать не пришлось),
        и False, если выполнен новый вход. Бросает ValueError, если не заданы
        учётные данные, и EkromAuthError, если войти не удалось.
        """
        async with EkromClient(self._settings) as ekrom:
            if await ekrom.is_authenticated():
                return True
            await ekrom.login()  # ValueError, если EKROM_LOGIN/PASSWORD пусты
            await ekrom.save_storage_state()
            if not await ekrom.is_authenticated():
                raise EkromAuthError(
                    "Не удалось войти в кабинет ekrom — проверьте EKROM_LOGIN и "
                    "EKROM_PASSWORD в .env."
                )
            return False

    async def build_pdf(self, selection: SelectionResult) -> Path:
        """Добавить подобранные товары в корзину и скачать PDF."""
        out_dir = self._settings.output_path
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = out_dir / f"podbor_{stamp}.pdf"

        async with EkromClient(self._settings) as ekrom:
            # Открываем корзину один раз и тут же проверяем авторизацию по
            # редиректу needAuth — это экономит отдельный заход на /personal/.
            await ekrom.open_cart()
            if "needauth" in ekrom.page.url.lower():
                raise EkromAuthError(
                    "Сессия ekrom истекла — выполните повторный вход "
                    "(python scripts/login_ekrom.py)."
                )
            # Каждый запрос — с чистой корзины (страница уже открыта).
            await ekrom.clear_cart(already_open=True)
            targets = [item.product for item in selection.matched if item.product]

            # Корзина общая для сессии — добавляем параллельно, затем сверяем
            # фактический состав (ответ basket.php иногда теряется при параллели).
            oks = await self._add_to_cart_parallel(ekrom, targets)
            failed = [p for p, ok in zip(targets, oks) if not ok]
            if failed:
                await self._add_to_cart_parallel(ekrom, failed)

            await ekrom.open_cart()
            in_cart = await ekrom.read_cart_slugs()
            missing = [p for p in targets if product_slug(p.url) not in in_cart]
            if missing:
                # Последняя попытка — по одному в основной вкладке (надёжнее).
                for product in missing:
                    await ekrom.add_to_cart(product)
                await ekrom.open_cart()
                in_cart = await ekrom.read_cart_slugs()
                still_missing = [p for p in targets if product_slug(p.url) not in in_cart]
                if still_missing:
                    raise EkromCartError([p.title for p in still_missing])

            # PDF берём прямым запросом к cart_pdf.php — без рендера order/make.
            await ekrom.download_cart_pdf_direct(out_path)
        return out_path

    async def _add_to_cart_parallel(
        self, ekrom: EkromClient, products: list
    ) -> list[bool]:
        """Добавить товары в корзину одновременно (вкладка на товар, с лимитом).

        Возвращает список результатов в порядке products (True — добавлено).
        """
        if not products:
            return []
        sem = asyncio.Semaphore(_MAX_PARALLEL_SPECS)

        async def add(product) -> bool:
            async with sem:
                page = await ekrom.new_page()
                try:
                    return await ekrom.add_to_cart(product, page)
                finally:
                    await page.close()

        return list(await asyncio.gather(*(add(p) for p in products)))

    async def run(self, message: str, generate_pdf: bool = True) -> PipelineResult:
        """Полный проход. Если REQUIRE_CONFIRMATION=true — PDF обычно генерится
        отдельным шагом после подтверждения клиента, поэтому generate_pdf можно
        выключить и сначала вернуть selection на согласование."""
        request = self.parse_message(message)
        selection = await self.select_products(request)
        pdf_path = None
        if generate_pdf and not self._settings.require_confirmation:
            pdf_path = await self.build_pdf(selection)
        return PipelineResult(request=request, selection=selection, pdf_path=pdf_path)
