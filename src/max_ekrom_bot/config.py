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

    # MAX
    max_bot_token: str = Field(default="", alias="MAX_BOT_TOKEN")
    max_api_base_url: str = Field(default="https://botapi.max.ru", alias="MAX_API_BASE_URL")
    max_webhook_secret: str = Field(default="", alias="MAX_WEBHOOK_SECRET")

    # LLM
    llm_provider: str = Field(default="openai", alias="LLM_PROVIDER")
    llm_model: str = Field(default="gpt-4o-mini", alias="LLM_MODEL")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    yandex_api_key: str = Field(default="", alias="YANDEX_API_KEY")
    yandex_folder_id: str = Field(default="", alias="YANDEX_FOLDER_ID")
    gigachat_auth_key: str = Field(default="", alias="GIGACHAT_AUTH_KEY")

    # ekrom.ru
    ekrom_base_url: str = Field(default="https://www.ekrom.ru", alias="EKROM_BASE_URL")
    ekrom_login: str = Field(default="", alias="EKROM_LOGIN")
    ekrom_password: str = Field(default="", alias="EKROM_PASSWORD")
    ekrom_storage_state: str = Field(default="data/storage_state.json", alias="EKROM_STORAGE_STATE")

    # Поведение
    ekrom_headful: bool = Field(default=False, alias="EKROM_HEADFUL")
    output_dir: str = Field(default="data/out", alias="OUTPUT_DIR")
    require_confirmation: bool = Field(default=True, alias="REQUIRE_CONFIRMATION")

    @property
    def storage_state_path(self) -> Path:
        p = Path(self.ekrom_storage_state)
        return p if p.is_absolute() else REPO_ROOT / p

    @property
    def output_path(self) -> Path:
        p = Path(self.output_dir)
        return p if p.is_absolute() else REPO_ROOT / p


@lru_cache
def get_settings() -> Settings:
    """Кэшированный доступ к настройкам."""
    return Settings()
