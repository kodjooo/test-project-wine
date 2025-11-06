"""Общие структуры данных для пайплайна сбора карточек."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass(slots=True)
class ProductLink:
    """Описание ссылки на карточку товара, найденной на странице категории."""

    url: str
    source_page_url: str
    page_number: Optional[int]
    position: int


@dataclass(slots=True)
class CategoryPageResult:
    """Результат обхода одной страницы категории."""

    url: str
    page_number: Optional[int]
    product_links: List[ProductLink]
    discovered_page_urls: List[str]
    raw_html: str


@dataclass(slots=True)
class ProductSection:
    """Текстовый блок с дополнительной информацией о товаре."""

    title: str
    text: str
    html: str
    raw_text: str = ""
    items: List[str] = field(default_factory=list)


@dataclass(slots=True)
class ProductRaw:
    """Сырые данные карточки до нормализации."""

    product_url: str
    source_page_url: str
    page_number: Optional[int]

    title: Optional[str]
    sku: Optional[str]
    product_id: Optional[str]
    country: Optional[str]
    brand: Optional[str]
    producer: Optional[str]

    breadcrumbs: List[str] = field(default_factory=list)

    price_text: Optional[str] = None
    price_value: Optional[float] = None
    price_currency: Optional[str] = None

    volume_text: Optional[str] = None
    volume_l: Optional[float] = None

    abv_text: Optional[str] = None
    abv_percent: Optional[float] = None

    availability_text: Optional[str] = None

    grapes: List[str] = field(default_factory=list)
    sections: Dict[str, ProductSection] = field(default_factory=dict)

    image_urls: List[str] = field(default_factory=list)
    hero_image_url: Optional[str] = None

    raw_html: str = ""


@dataclass(slots=True)
class ProductNormalized:
    """Нормализованные данные карточки, готовые к выгрузке."""

    product_url: str
    source_page_url: str
    page_number: Optional[int]

    product_id: Optional[str]
    title: Optional[str]
    sku: Optional[str]
    country: Optional[str]
    brand: Optional[str]
    producer: Optional[str]

    price_value: Optional[float]
    price_currency: Optional[str]
    volume_l: Optional[float]
    abv_percent: Optional[float]
    age_years: Optional[int]
    availability: Optional[bool]

    tasting_notes: Optional[str]
    gastronomy: Optional[str]
    grapes: List[str]
    maturation: Optional[str]
    awards: Optional[str]
    gift_packaging: Optional[str]

    breadcrumbs: List[str]
    image_urls: List[str]
    hero_image_url: Optional[str]
    image_direct_url: Optional[str] = None
    image_viewer_url: Optional[str] = None
    image_thumb_url: Optional[str] = None
    image_sha256: Optional[str] = None

    raw_sections: Dict[str, ProductSection] = field(default_factory=dict)
    raw: Optional[ProductRaw] = None
