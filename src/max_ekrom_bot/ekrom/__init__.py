"""Автоматизация ekrom.ru: поиск, корзина, выгрузка PDF (с авторизацией)."""

from .catalog import CATEGORY_URLS, build_search_url, category_for
from .client import EkromClient

__all__ = ["EkromClient", "CATEGORY_URLS", "build_search_url", "category_for"]
