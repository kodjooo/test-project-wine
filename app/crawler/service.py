"""Реализация обхода страниц категории."""

from __future__ import annotations

from collections import deque
import logging
from dataclasses import dataclass
from typing import AsyncIterator, Deque, List, Optional, Set
from urllib.parse import parse_qs, urljoin, urlparse

from playwright.async_api import BrowserContext, Page

from app.config import Settings
from app.models import CategoryPageResult, ProductLink
from app.playwright_helpers import close_age_confirmation

PRODUCT_LINK_SELECTOR = "a[href^='/katalog/tovar/']"
PAGINATION_LINK_SELECTOR = "a[href*='PAGEN_1=']"

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class CategoryCrawlerMetrics:
    """Показатели работы краулера."""

    pages_processed: int = 0
    product_links_found: int = 0
    unique_products: int = 0


class CategoryCrawler:
    """Обходит страницы категории и возвращает найденные карточки."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.metrics = CategoryCrawlerMetrics()
        self._unique_product_urls: Set[str] = set()

    async def crawl(self, context: BrowserContext) -> AsyncIterator[CategoryPageResult]:
        """Асинхронно обойти все доступные страницы и вернуть результаты."""
        page = await context.new_page()
        visited: Set[str] = set()
        queued: Set[str] = set()
        queue: Deque[str] = deque()
        queue.append(self._settings.category_url)
        queued.add(self._settings.category_url)
        fallback_page_counter = 0

        try:
            while queue:
                target_url = queue.popleft()
                queued.discard(target_url)

                if target_url in visited:
                    continue

                LOGGER.info("Краулер: переход на страницу %s", target_url)
                await page.goto(
                    target_url,
                    wait_until="networkidle",
                    timeout=self._settings.navigation_timeout_ms,
                )
                await close_age_confirmation(page)
                await self._ensure_product_links_visible(page)

                fallback_page_counter += 1
                current_page_number = self._extract_page_number(
                    page.url
                ) or fallback_page_counter

                product_links = await self._collect_product_links(
                    page, current_page_number
                )
                raw_html = await page.content()

                discovered_pages = await self._collect_pagination_links(
                    page, current_page_number
                )

                for new_page_url in discovered_pages:
                    if new_page_url in visited or new_page_url in queued:
                        continue
                    queue.append(new_page_url)
                    queued.add(new_page_url)

                visited.add(page.url)
                self._update_metrics(product_links)

                yield CategoryPageResult(
                    url=page.url,
                    page_number=current_page_number,
                    product_links=product_links,
                    discovered_page_urls=discovered_pages,
                    raw_html=raw_html,
                )

                await page.wait_for_timeout(self._settings.request_delay_ms)
        finally:
            await page.close()

    async def _ensure_product_links_visible(self, page: Page) -> None:
        """Ожидать появления карточек товаров на странице."""
        await page.wait_for_selector(
            PRODUCT_LINK_SELECTOR,
            timeout=self._settings.navigation_timeout_ms,
        )

    async def _collect_product_links(
        self,
        page: Page,
        page_number: Optional[int],
    ) -> List[ProductLink]:
        """Сохранить ссылки на карточки с учётом позиции."""
        hrefs = await page.eval_on_selector_all(
            PRODUCT_LINK_SELECTOR,
            "elements => elements.map(el => el.getAttribute('href'))",
        )
        links: List[ProductLink] = []
        seen: Set[str] = set()
        duplicates: List[str] = []

        for position, raw_href in enumerate(hrefs, start=1):
            if not raw_href:
                continue
            absolute_url = urljoin(page.url, raw_href)
            if absolute_url in seen:
                duplicates.append(absolute_url)
                continue
            seen.add(absolute_url)
            links.append(
                ProductLink(
                    url=absolute_url,
                    source_page_url=page.url,
                    page_number=page_number,
                    position=position,
                )
            )
        duplicates_count = len(duplicates)
        if duplicates_count:
            sample = ", ".join(sorted(set(duplicates))[:3])
            LOGGER.info(
                "Краулер: на странице %s (%s) отфильтровано %s дубликатов, примеры: %s",
                page_number if page_number is not None else "N/A",
                page.url,
                duplicates_count,
                sample,
            )
        return links

    async def _collect_pagination_links(
        self,
        page: Page,
        current_page: Optional[int],
    ) -> List[str]:
        """Собрать ссылки на следующие страницы."""
        hrefs = await page.eval_on_selector_all(
            PAGINATION_LINK_SELECTOR,
            "elements => elements.map(el => el.href)",
        )
        candidate_urls: List[str] = []
        seen: Set[str] = set()

        for raw_href in hrefs:
            if not raw_href:
                continue
            absolute_url = urljoin(page.url, raw_href)
            if absolute_url in seen:
                continue
            seen.add(absolute_url)
            page_number = self._extract_page_number(absolute_url)
            if current_page and page_number and page_number <= current_page:
                continue
            candidate_urls.append(absolute_url)

        candidate_urls.sort(
            key=lambda url: self._extract_page_number(url) or float("inf")
        )
        return candidate_urls

    def _extract_page_number(self, url: str) -> Optional[int]:
        """Получить номер страницы из URL по параметру PAGEN_1."""
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        values = query.get("PAGEN_1")
        if not values:
            if parsed.path.rstrip("/").endswith("drinktype-konyak"):
                return 1
            return None
        try:
            return int(values[0])
        except (ValueError, TypeError):
            return None

    def _update_metrics(self, product_links: List[ProductLink]) -> None:
        """Обновить счётчики по результатам страницы."""
        self.metrics.pages_processed += 1
        self.metrics.product_links_found += len(product_links)
        for link in product_links:
            if link.url not in self._unique_product_urls:
                self._unique_product_urls.add(link.url)
        self.metrics.unique_products = len(self._unique_product_urls)
