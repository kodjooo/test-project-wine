"""Обёртка для вызовов LLM (OpenAI)."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from openai import AsyncOpenAI, OpenAIError

from app.config import Settings

LOGGER = logging.getLogger(__name__)


class LLMUnavailableError(RuntimeError):
    """Генерируется при недоступности LLM (нет ключа или ошибка запроса)."""


class LLMClient:
    """Простая обёртка над OpenAI для нормализации данных."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._enabled = bool(settings.openai_api_key)
        self._model = settings.llm_model
        self._client: Optional[AsyncOpenAI] = None

        if self._enabled:
            self._client = AsyncOpenAI(api_key=settings.openai_api_key)
        else:
            LOGGER.info("LLM отключён: отсутствует OPENAI_API_KEY")

    async def normalize_price(self, text: str) -> Dict[str, Any]:
        """Привести строку цены к структуре JSON."""
        prompt = (
            "Верни JSON {price_value:number, currency:string} из строки цены: "
            f"{text}"
        )
        return await self._ask_json(prompt)

    async def parse_volume_abv(self, text: str) -> Dict[str, Any]:
        """Определить объём и крепость из текста."""
        prompt = (
            "Извлеки объём и крепость. Верни JSON {volume_l:number|null, abv:number|null}. "
            f"Вход: {text}"
        )
        return await self._ask_json(prompt)

    async def extract_section(self, section: str, html: str) -> Dict[str, Any]:
        """Очистить HTML блока и вернуть текст, список при необходимости."""
        prompt = (
            f"Из HTML-фрагмента под заголовком «{section}» извлеки чистый текст; "
            "верни JSON {text:string, list?:string[]}. "
            f"HTML: ```{html}```"
        )
        return await self._ask_json(prompt)

    async def _ask_json(self, prompt: str) -> Dict[str, Any]:
        if not self._enabled or not self._client:
            raise LLMUnavailableError("LLM disabled or not configured")
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                temperature=0,
                messages=[
                    {
                        "role": "system",
                        "content": "Отвечай строго валидным JSON без пояснений.",
                    },
                    {"role": "user", "content": prompt},
                ],
            )
        except OpenAIError as exc:
            LOGGER.warning("LLM запрос завершился ошибкой: %s", exc)
            raise LLMUnavailableError(str(exc)) from exc

        try:
            content = response.choices[0].message.content
        except (IndexError, AttributeError) as exc:
            raise LLMUnavailableError("LLM вернул пустой ответ") from exc

        if not content:
            raise LLMUnavailableError("LLM вернул пустой ответ")

        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMUnavailableError("Невозможно распарсить JSON от LLM") from exc
