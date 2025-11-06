"""Парсинг карточек товара и извлечение ключевых полей."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional
from urllib.parse import urljoin

from playwright.async_api import BrowserContext, Page
from selectolax.parser import HTMLParser

from app.config import Settings
from app.models import ProductLink, ProductRaw, ProductSection
from app.playwright_helpers import close_age_confirmation
from app.utils import (
    clean_text,
    extract_abv_percent,
    extract_float_with_unit,
    extract_price_value,
    normalize_whitespace,
    split_multiline,
)

LOGGER = logging.getLogger(__name__)

SKU_REGEX = re.compile(r"Артикул:\s*([A-Za-z0-9\-_/]+)", re.IGNORECASE)

SECTION_KEYS = {
    "дегустационные характеристики": "tasting_notes",
    "гастрономия": "gastronomy",
    "сортовой состав": "grapes",
    "способ выдержки": "maturation",
    "награды и оценки товара": "awards",
    "производитель": "producer",
    "подарочная упаковка": "gift_packaging",
}

IMAGE_SELECTOR = ".product__content-img img, .product__gallery img, img[src*='/upload/']"


@dataclass(slots=True)
class ProductParserMetrics:
    """Метрики работы парсера товаров."""

    products_parsed: int = 0
    failures: int = 0


class ProductPageParser:
    """Загружает страницы товара и извлекает данные."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.metrics = ProductParserMetrics()

    async def parse(self, context: BrowserContext, link: ProductLink) -> ProductRaw:
        """Загрузить страницу и вернуть сырые данные карточки."""
        page = await context.new_page()

        try:
            await page.goto(
                link.url,
                wait_until="networkidle",
                timeout=self._settings.navigation_timeout_ms,
            )
            await close_age_confirmation(page)
            await page.wait_for_selector("h1", timeout=self._settings.navigation_timeout_ms)
            html = await page.content()

            product = self._parse_html(html, link)
            self.metrics.products_parsed += 1
            return product
        except Exception:
            self.metrics.failures += 1
            LOGGER.exception("Failed to parse product page: %s", link.url)
            raise
        finally:
            try:
                await page.wait_for_timeout(self._settings.request_delay_ms)
            finally:
                await page.close()

    def _parse_html(self, html: str, link: ProductLink) -> ProductRaw:
        tree = HTMLParser(html)

        title = clean_text(self._text_or_none(tree.css_first("h1")))
        sku_text = clean_text(self._text_or_none(tree.css_first(".product__id")))
        sku = self._extract_sku(sku_text)
        product_id = self._extract_product_id(tree)

        brand = clean_text(self._text_or_none(tree.css_first(".product__titles-name")))
        country = self._extract_country(tree)

        breadcrumbs = self._extract_breadcrumbs(tree)

        facts_texts = [
            clean_text(node.text(strip=True))
            for node in tree.css(".product__facts-item")
            if node
        ]
        volume_text = self._first_matching(facts_texts, "л")
        abv_text = self._first_matching(facts_texts, "%")

        price_text = clean_text(
            self._text_or_none(tree.css_first(".product__buy-box-price"))
        )
        availability_text = clean_text(
            self._text_or_none(tree.css_first(".product__buy-box-footer"))
        )

        sections = self._extract_sections(tree)
        grapes_section = sections.get("grapes")
        grapes = grapes_section.items if grapes_section else []

        producer = self._derive_producer(sections)

        image_urls = self._extract_images(tree, link.url)

        return ProductRaw(
            product_url=link.url,
            source_page_url=link.source_page_url,
            page_number=link.page_number,
            title=title,
            sku=sku,
            product_id=product_id or sku,
            country=country,
            brand=brand,
            producer=producer,
            breadcrumbs=breadcrumbs,
            price_text=price_text,
            price_value=extract_price_value(price_text),
            price_currency="RUB" if price_text else None,
            volume_text=volume_text,
            volume_l=extract_float_with_unit(volume_text),
            abv_text=abv_text,
            abv_percent=extract_abv_percent(abv_text),
            availability_text=availability_text,
            grapes=grapes,
            sections=sections,
            image_urls=image_urls,
            hero_image_url=image_urls[0] if image_urls else None,
            raw_html=html,
        )

    def _extract_country(self, tree: HTMLParser) -> Optional[str]:
        region_node = tree.css_first(".product__titles-region a")
        if region_node:
            return clean_text(region_node.text(strip=True))
        return None

    def _extract_breadcrumbs(self, tree: HTMLParser) -> List[str]:
        crumbs: List[str] = []
        for node in tree.css(".ui-breadcrumbs__item"):
            text = clean_text(node.text(strip=True))
            if text:
                crumbs.append(text)
        return crumbs

    def _extract_sections(self, tree: HTMLParser) -> Dict[str, ProductSection]:
        sections: Dict[str, ProductSection] = {}
        for heading in tree.css("h4"):
            title_raw = self._text_or_none(heading)
            title_raw = clean_text(title_raw)
            if not title_raw:
                continue

            normalized = title_raw.lower().rstrip(":")
            key = self._match_section_key(normalized)
            if not key:
                continue

            content_html, content_text, raw_text = self._collect_section_content(heading)
            section = ProductSection(
                title=title_raw.rstrip(":"),
                text=content_text,
                html=content_html,
                raw_text=raw_text,
            )
            if key == "grapes":
                section.items = split_multiline(raw_text)
            sections[key] = section
        return sections

    def _collect_section_content(self, heading_node) -> tuple[str, str, str]:
        html_parts: List[str] = []
        text_parts: List[str] = []
        raw_text_parts: List[str] = []
        node = heading_node.next

        while node is not None and getattr(node, "tag", None) is not None:
            if node.tag == "h4":
                break
            html_parts.append(node.html or "")
            raw_fragment = node.text(separator="\n", strip=True)
            if raw_fragment:
                raw_text_parts.append(raw_fragment)
            text_value = clean_text(raw_fragment)
            if text_value:
                text_parts.append(text_value)
            node = node.next

        combined_text = "\n".join(text_parts).strip()
        combined_html = "".join(html_parts).strip()
        combined_raw = "\n".join(raw_text_parts).strip()
        return combined_html, combined_text, combined_raw

    def _derive_producer(self, sections: Dict[str, ProductSection]) -> Optional[str]:
        section = sections.get("producer")
        if not section:
            return None
        if section.items:
            return section.items[0]
        return clean_text(section.text)

    def _extract_images(self, tree: HTMLParser, base_url: str) -> List[str]:
        urls: List[str] = []
        for node in tree.css(IMAGE_SELECTOR):
            src = node.attributes.get("src")
            if src:
                absolute = urljoin(base_url, src)
                if absolute not in urls:
                    urls.append(absolute)
            srcset = node.attributes.get("srcset")
            if srcset:
                for candidate in self._parse_srcset(srcset):
                    absolute = urljoin(base_url, candidate)
                    if absolute not in urls:
                        urls.append(absolute)
        return urls

    def _parse_srcset(self, srcset: str) -> List[str]:
        candidates = []
        for chunk in srcset.split(","):
            part = chunk.strip().split(" ")[0]
            if part:
                candidates.append(part)
        return candidates

    def _match_section_key(self, normalized_title: str) -> Optional[str]:
        for pattern, key in SECTION_KEYS.items():
            if normalized_title.startswith(pattern):
                return key
        return None

    def _text_or_none(self, node) -> Optional[str]:
        if node is None:
            return None
        return node.text(strip=True)

    def _extract_sku(self, sku_text: Optional[str]) -> Optional[str]:
        if not sku_text:
            return None
        match = SKU_REGEX.search(sku_text)
        if match:
            return match.group(1)
        return sku_text.replace("Артикул:", "").strip()

    def _extract_product_id(self, tree: HTMLParser) -> Optional[str]:
        node = tree.css_first("[data-product-id]")
        if not node:
            return None
        data_attr = node.attributes.get("data-product-id")
        return clean_text(data_attr)

    def _first_matching(self, texts: List[Optional[str]], marker: str) -> Optional[str]:
        for value in texts:
            if not value:
                continue
            if marker.lower() in value.lower():
                return value
        return None
