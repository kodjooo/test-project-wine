"""Помощники для обработки текстовых значений."""

from __future__ import annotations

import re
from typing import Iterable, List, Optional

WHITESPACE_RE = re.compile(r"\s+")
VOLUME_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*л", re.IGNORECASE)
ABV_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")
PRICE_RE = re.compile(r"(\d[\d\s\u00a0]*)")


def normalize_whitespace(value: str) -> str:
    """Свести все пробельные символы к одиночному пробелу."""
    value = value.replace("\u00a0", " ")
    return WHITESPACE_RE.sub(" ", value).strip()


def clean_text(value: Optional[str]) -> Optional[str]:
    """Нормализовать текст и вернуть None для пустых значений."""
    if value is None:
        return None
    cleaned = normalize_whitespace(value)
    return cleaned or None


def extract_float_with_unit(value: Optional[str]) -> Optional[float]:
    """Извлечь числовое значение объёма в литрах по шаблону."""
    if not value:
        return None
    match = VOLUME_RE.search(value)
    if not match:
        return None
    number = match.group(1).replace(",", ".")
    try:
        return float(number)
    except ValueError:
        return None


def extract_abv_percent(value: Optional[str]) -> Optional[float]:
    """Извлечь крепость в процентах."""
    if not value:
        return None
    match = ABV_RE.search(value)
    if not match:
        return None
    number = match.group(1).replace(",", ".")
    try:
        return float(number)
    except ValueError:
        return None


def extract_price_value(value: Optional[str]) -> Optional[float]:
    """Получить числовое значение цены."""
    if not value:
        return None
    match = PRICE_RE.search(value)
    if not match:
        return None
    digits = match.group(1).replace(" ", "").replace("\u00a0", "")
    try:
        return float(digits)
    except ValueError:
        return None


def split_multiline(value: Optional[str]) -> List[str]:
    """Разбить строку по переводам строки/разделителям на список значений."""
    if not value:
        return []
    normalized = value.replace("\r", "\n").replace("\u00a0", " ")
    parts: Iterable[str] = (
        piece.strip()
        for piece in re.split(r"[\n;,]", normalized)
    )
    return [part for part in parts if part]
