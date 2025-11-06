"""Тестирование нормализатора карточек."""

from __future__ import annotations

import asyncio

from app.config import Settings
from app.models import ProductLink
from app.normalizer import ProductNormalizer
from app.parser.service import ProductPageParser

from .test_parser import load_fixture


def test_normalize_product_without_llm() -> None:
    settings = Settings()
    parser = ProductPageParser(settings)
    normalizer = ProductNormalizer(settings, llm_client=None)

    html = load_fixture("product_sample.html")
    link = ProductLink(
        url="https://winediscovery.ru/katalog/tovar/sample/",
        source_page_url="https://winediscovery.ru/katalog/krepkie_napitki/",
        page_number=1,
        position=1,
    )

    raw_product = parser._parse_html(html, link)  # type: ignore[attr-defined]
    normalized = asyncio.run(normalizer.normalize(raw_product))

    assert normalized.price_value == 9999.0
    assert normalized.price_currency == "RUB"
    assert normalized.volume_l == 0.7
    assert normalized.abv_percent == 40.0
    assert normalized.availability is True
    assert normalized.grapes == ["Уни Блан", "Фоль Бланш", "Коломбар"]
    assert normalized.producer == "Sample Producer"
