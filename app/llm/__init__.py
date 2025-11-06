"""Утилиты для работы с LLM."""

from .client import LLMClient, LLMUnavailableError

__all__ = ["LLMClient", "LLMUnavailableError"]
