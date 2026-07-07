"""Конфигурация приложения. Значения берутся из .env (см. .env.example)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Корень репозитория (на два уровня выше: src/max_ekrom_bot/config.py -> repo root)
REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Все настройки в одном месте. Читаются из переменных окружения / .env."""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Telegram (бот для менеджеров)
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    # Список Telegram user_id менеджеров через запятую (узнать: @userinfobot)
    telegram_allowed_ids: str = Field(default="", alias="TELEGRAM_ALLOWED_IDS")
    # Прокси для api.telegram.org (если без VPN не подключается), напр.:
    # socks5://127.0.0.1:10808  или  http://127.0.0.1:8080
    telegram_proxy: str = Field(default="", alias="TELEGRAM_PROXY")
    # Telegram user_id админов через запятую — кто может добавлять/удалять
    # менеджеров прямо из бота. Если пусто — управление менеджерами выключено.
    telegram_admin_ids: str = Field(default="", alias="TELEGRAM_ADMIN_IDS")

    # LLM
    llm_provider: str = Field(default="openai", alias="LLM_PROVIDER")
    llm_model: str = Field(default="gpt-4o-mini", alias="LLM_MODEL")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    # Groq (OpenAI-совместимый API, бесплатный тариф)
    groq_api_key: str = Field(default="", alias="GROQ_API_KEY")
    groq_base_url: str = Field(
        default="https://api.groq.com/openai/v1", alias="GROQ_BASE_URL"
    )
    yandex_api_key: str = Field(default="", alias="YANDEX_API_KEY")
    yandex_folder_id: str = Field(default="", alias="YANDEX_FOLDER_ID")
    # Модель для резолвера категорий (может быть сильнее основной — выбор из ~57
    # категорий требователен). Пусто — использовать основную LLM_MODEL. Для
    # YandexGPT рекомендуется yandexgpt (Pro): Lite грубо ошибается в категориях.
    resolver_model: str = Field(default="", alias="LLM_RESOLVER_MODEL")
    gigachat_auth_key: str = Field(default="", alias="GIGACHAT_AUTH_KEY")
    # LLM-ре-ранк шортлиста подбора (#6): выбор лучшего товара моделью. По
    # умолчанию выкл — работает детерминированный подбор. Включение добавляет
    # один LLM-вызов на позицию (с детерминированным фолбэком при сбое).
    llm_rerank: bool = Field(default=False, alias="LLM_RERANK")

    # ekrom.ru
    ekrom_base_url: str = Field(default="https://www.ekrom.ru", alias="EKROM_BASE_URL")
    ekrom_login: str = Field(default="", alias="EKROM_LOGIN")
    ekrom_password: str = Field(default="", alias="EKROM_PASSWORD")
    ekrom_storage_state: str = Field(default="data/storage_state.json", alias="EKROM_STORAGE_STATE")

    # Поведение
    ekrom_headful: bool = Field(default=False, alias="EKROM_HEADFUL")
    output_dir: str = Field(default="data/out", alias="OUTPUT_DIR")
    require_confirmation: bool = Field(default=True, alias="REQUIRE_CONFIRMATION")

    # Брендинг клиентского PDF (свой шаблон, не партнёрская корзина ekrom)
    shop_name: str = Field(default="ekrom", alias="SHOP_NAME")
    shop_contacts: str = Field(default="", alias="SHOP_CONTACTS")
    shop_site: str = Field(default="https://www.ekrom.ru", alias="SHOP_SITE")
    # Путь к TTF-шрифту с кириллицей; пусто — ищем системный (DejaVu/Arial)
    pdf_font_path: str = Field(default="", alias="PDF_FONT_PATH")
    pdf_logo_path: str = Field(default="", alias="PDF_LOGO_PATH")

    # Шапка/подвал PDF в стиле корзины ekrom (эталонный документ).
    pdf_header_phone: str = Field(default="8 (343) 379-98-68", alias="PDF_HEADER_PHONE")
    pdf_city: str = Field(default="Екатеринбург", alias="PDF_CITY")
    manager_name: str = Field(default="", alias="MANAGER_NAME")
    manager_email: str = Field(default="", alias="MANAGER_EMAIL")
    manager_phone: str = Field(default="", alias="MANAGER_PHONE")
    manager_city_phone: str = Field(default="8 (343) 379-98-68", alias="MANAGER_CITY_PHONE")

    # Клиентский режим: бот открыт всем (клиент пишет сам), PDF — свой шаблон без
    # партнёрской корзины, лид уходит менеджерам. Выкл — прежнее поведение «для
    # менеджеров» (доступ по списку, PDF через корзину ekrom).
    client_mode: bool = Field(default=False, alias="CLIENT_MODE")
    # Лимиты на одного клиента (защита бюджета LLM от спама). 0 — без лимита.
    client_rate_per_hour: int = Field(default=8, alias="CLIENT_RATE_PER_HOUR")
    client_rate_per_day: int = Field(default=30, alias="CLIENT_RATE_PER_DAY")
    # Уведомлять операторов (admin_user_ids) о новом клиентском запросе-лиде.
    notify_lead: bool = Field(default=True, alias="NOTIFY_LEAD")

    # Кэш каталога (масштаб): кандидаты для 6 базовых типов берём из локальной БД
    # (наполняется краулером), а не обходом сайта — быстро и параллельно. Выкл —
    # прежний живой обход. TTL — сколько секунд кэш считается свежим.
    use_catalog_cache: bool = Field(default=False, alias="USE_CATALOG_CACHE")
    catalog_cache_ttl: int = Field(default=86400, alias="CATALOG_CACHE_TTL")

    @staticmethod
    def _parse_ids(raw: str) -> frozenset[int]:
        ids: set[int] = set()
        for part in (raw or "").split(","):
            part = part.strip()
            if part:
                ids.add(int(part))
        return frozenset(ids)

    @property
    def allowed_telegram_user_ids(self) -> frozenset[int]:
        """Стартовый список менеджеров из .env (сид для data/managers.json)."""
        return self._parse_ids(self.telegram_allowed_ids)

    @property
    def admin_user_ids(self) -> frozenset[int]:
        """Кто может управлять менеджерами из бота."""
        return self._parse_ids(self.telegram_admin_ids)

    @property
    def managers_store_path(self) -> Path:
        return REPO_ROOT / "data" / "managers.json"

    @property
    def catalog_db_path(self) -> Path:
        return REPO_ROOT / "data" / "catalog.db"

    @property
    def storage_state_path(self) -> Path:
        p = Path(self.ekrom_storage_state)
        return p if p.is_absolute() else REPO_ROOT / p

    @property
    def output_path(self) -> Path:
        p = Path(self.output_dir)
        return p if p.is_absolute() else REPO_ROOT / p

    @property
    def analytics_db_path(self) -> Path:
        return REPO_ROOT / "data" / "bot_analytics.db"

    @property
    def pick_history_dir(self) -> Path:
        return REPO_ROOT / "data" / "history"


@lru_cache
def get_settings() -> Settings:
    """Кэшированный доступ к настройкам."""
    return Settings()
