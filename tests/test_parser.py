"""Тестирование парсинга карточки товара."""

from __future__ import annotations

from pathlib import Path

from app.config import Settings
from app.models import ProductLink
from app.parser.service import ProductPageParser

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def test_parse_html_extracts_core_fields() -> None:
    settings = Settings()
    parser = ProductPageParser(settings)
    html = load_fixture("product_sample.html")

    link = ProductLink(
        url="https://winediscovery.ru/katalog/tovar/sample/",
        source_page_url="https://winediscovery.ru/katalog/krepkie_napitki/",
        page_number=1,
        position=1,
    )

    product = parser._parse_html(html, link)  # type: ignore[attr-defined]

    assert product.title == "Коньяк SAMPLE XO"
    assert product.sku == "SAMPLE-001"
    assert product.product_id == "SAMPLE-001"
    assert product.brand == "Sample Brand"
    assert product.country == "Франция"
    assert product.volume_l == 0.7
    assert product.abv_percent == 40.0
    assert product.price_value == 9999.0
    assert product.price_currency == "RUB"
    assert product.availability_text == "Товар в наличии"
    assert product.hero_image_url.endswith("/upload/sample/sample@2048.jpg")
    assert product.producer == "Sample Producer"
    assert product.grapes == ["Уни Блан", "Фоль Бланш", "Коломбар"]

    tasting_notes = product.sections["tasting_notes"].text
    assert "аромат ванили" in tasting_notes.lower()
