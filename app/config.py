"""Конфигурация приложения и настройки окружения."""

from __future__ import annotations

import random
from functools import lru_cache
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_CATEGORY_URL = (
    "https://winediscovery.ru/katalog/krepkie_napitki/filtr/drinktype-konyak/"
)


class Settings(BaseSettings):
    """Настройки проекта, загружаемые из переменных окружения."""

    model_config = SettingsConfigDict(
        env_file=(".env",),
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    category_url: str = Field(default=DEFAULT_CATEGORY_URL, alias="CATEGORY_URL")
    gsheet_id: str = Field(default="", alias="GSHEET_ID")
    gsheet_tab: str = Field(default="Products", alias="GSHEET_TAB")
    google_sa_json: str = Field(default="/secrets/sa.json", alias="GOOGLE_SA_JSON")
    headless: bool = Field(default=True, alias="HEADLESS")
    request_delay_ms: int = Field(default=1200, alias="REQUEST_DELAY_MS", ge=0)
    max_concurrency: int = Field(default=3, alias="MAX_CONCURRENCY", ge=1)

    use_proxy: bool = Field(default=False, alias="USE_PROXY")
    http_proxy: str = Field(default="", alias="HTTP_PROXY")
    https_proxy: str = Field(default="", alias="HTTPS_PROXY")

    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    llm_model: str = Field(default="gpt-4o-mini", alias="LLM_MODEL")

    navigation_timeout_ms: int = Field(
        default=20_000, alias="NAVIGATION_TIMEOUT_MS", ge=1_000
    )
    max_retries: int = Field(default=3, alias="MAX_RETRIES", ge=0)
    state_db_path: str = Field(default="state/pipeline.db", alias="STATE_DB_PATH")

    freeimage_api_key: str = Field(default="", alias="FREEIMAGE_API_KEY")
    freeimage_api_endpoint: str = Field(
        default="https://freeimage.host/api/1/upload", alias="FREEIMAGE_API_ENDPOINT"
    )
    freeimage_connect_timeout: float = Field(
        default=15.0, alias="FREEIMAGE_CONNECT_TIMEOUT", ge=1.0
    )
    freeimage_read_timeout: float = Field(
        default=60.0, alias="FREEIMAGE_READ_TIMEOUT", ge=1.0
    )
    freeimage_max_retries: int = Field(
        default=3, alias="FREEIMAGE_MAX_RETRIES", ge=0
    )

    @property
    def request_delay_seconds(self) -> float:
        """Задержка между запросами в секундах."""
        return self.request_delay_ms / 1000.0

    def choice_user_agent(self) -> str:
        """Вернуть случайный User-Agent из пула."""
        return random.choice(USER_AGENT_POOL)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Получить кешированный экземпляр настроек."""
    return Settings()  # type: ignore[call-arg]


USER_AGENT_POOL: List[str] = [
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_3) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.6261.95 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/121.0.6167.85 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.2 Safari/605.1.15"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) "
        "Gecko/20100101 Firefox/122.0"
    ),
]
