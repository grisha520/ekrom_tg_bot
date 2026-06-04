"""Оркестрация ядра: сообщение клиента → подбор → корзина → PDF.

Не зависит от мессенджера. Может вызываться из бота, CLI или тестов.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import Settings, get_settings
from .ekrom.catalog import build_category_url, category_for
from .ekrom.client import EkromClient
from .matching import ProductSelector
from .models import CustomerRequest, SelectionResult
from .parsing import get_parser
from .parsing.factory import Parser


@dataclass
class PipelineResult:
    request: CustomerRequest
    selection: SelectionResult
    pdf_path: Path | None = None


class Pipeline:
    def __init__(self, settings: Settings | None = None, parser: Parser | None = None) -> None:
        self._settings = settings or get_settings()
        self._parser = parser or get_parser(self._settings)

    def parse_message(self, message: str) -> CustomerRequest:
        """Разобрать текст клиента в структурированный запрос (только LLM, без сети сайта)."""
        return self._parser.parse(message)

    async def select_products(self, request: CustomerRequest) -> SelectionResult:
        """Найти и подобрать товары под каждую позицию запроса."""
        selector = ProductSelector(segment=request.segment)
        specs_with_candidates = []
        async with EkromClient(self._settings) as ekrom:
            for spec in request.specs:
                candidates = await self._candidates_for_spec(ekrom, spec)
                specs_with_candidates.append((spec, candidates))
        return selector.select_all(specs_with_candidates)

    async def _candidates_for_spec(self, ekrom: EkromClient, spec) -> list:
        """Кандидаты под позицию: сначала с фильтрами, при пустом результате —
        ослабляем до категории без фильтров."""
        base = self._settings.ekrom_base_url
        filtered_url = build_category_url(base, spec)
        if filtered_url:
            candidates = await ekrom.browse_url(filtered_url)
            if candidates:
                return candidates
            # фильтры могли пересушить выдачу — откатываемся к категории без фильтров
            category = category_for(spec.appliance)
            if category:
                return await ekrom.browse_category(category)
            return []
        # категории нет — текстовый поиск как последний вариант
        return await ekrom.search(spec.raw_text or spec.appliance.value)

    async def build_pdf(self, selection: SelectionResult) -> Path:
        """Добавить подобранные товары в корзину и скачать PDF."""
        out_dir = self._settings.output_path
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = out_dir / f"podbor_{stamp}.pdf"

        async with EkromClient(self._settings) as ekrom:
            await ekrom.clear_cart()  # каждый запрос — с чистой корзины
            for item in selection.matched:
                if item.product:
                    await ekrom.add_to_cart(item.product)
            await ekrom.download_cart_pdf(out_path)
        return out_path

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
