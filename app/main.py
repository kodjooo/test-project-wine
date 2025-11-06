"""Основная точка входа приложения."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, List, Optional

from playwright.async_api import BrowserContext, async_playwright

from app.config import Settings, get_settings
from app.crawler import CategoryCrawler
from app.media import MediaUploader, MediaUploadResult
from app.models import ProductNormalized
from app.normalizer import ProductNormalizer
from app.parser import ProductPageParser
from app.sheets import SheetsWriter
from app.state import StateRepository
from app.utils import product_etag

LOGGER = logging.getLogger(__name__)


async def run() -> List[ProductNormalized]:
    """Запустить пайплайн сбора карточек и вернуть нормализованные данные."""
    settings = get_settings()
    configure_logging()

    crawler = CategoryCrawler(settings)
    parser = ProductPageParser(settings)
    normalizer = ProductNormalizer(settings)
    state = StateRepository(settings.state_db_path)
    media_uploader = MediaUploader(settings, state)
    sheets_writer = SheetsWriter(settings, state)

    normalized_products: List[ProductNormalized] = []
    inserted = 0
    updated = 0
    skipped = 0
    last_position = await sheets_writer.get_last_position()
    current_position = 0
    LOGGER.info(
        "Продолжаем обработку с позиции %s",
        last_position + 1,
    )

    async with _launch_browser(settings) as context:
        try:
            async for category_page in crawler.crawl(context):
                LOGGER.info(
                    "Страница %s: найдено %s карточек",
                    category_page.page_number,
                    len(category_page.product_links),
                )
                for product_link in category_page.product_links:
                    current_position += 1
                    if current_position <= last_position:
                        LOGGER.info(
                            "Пропуск карточки %s (позиция %s уже обработана)",
                            product_link.url,
                            current_position,
                        )
                        continue
                    LOGGER.info(
                        "Начата обработка карточки: %s (страница %s, позиция %s)",
                        product_link.url,
                        product_link.page_number,
                        current_position,
                    )
                    product = await parser.parse(context, product_link)
                    LOGGER.info("Парсинг завершён: %s", product.product_url)
                    normalized = await normalizer.normalize(product)
                    LOGGER.info("Нормализация завершена: %s", normalized.product_url)
                    normalized_products.append(normalized)

                    etag_hash = product_etag(normalized)
                    product_record = state.get_product(normalized.product_url)
                    product_id = normalized.product_id or normalized.product_url

                    need_update = (
                        product_record is None
                        or product_record.etag_hash != etag_hash
                    )

                    image_sha = product_record.image_sha256 if product_record else None
                    image_direct_url: Optional[str] = None
                    image_viewer_url: Optional[str] = None
                    image_thumb_url: Optional[str] = None
                    media_error: Optional[str] = None

                    if need_update:
                        LOGGER.info(
                            "Карточка %s требует обновления (etag изменился или отсутствует)",
                            normalized.product_url,
                        )
                        try:
                            media_result = await media_uploader.ensure_image(normalized)
                            LOGGER.info(
                                "Обработка изображения завершена для %s: sha=%s, direct_url=%s",
                                normalized.product_url,
                                media_result.sha256,
                                media_result.direct_url,
                            )
                        except Exception as exc:
                            media_error = str(exc)
                            LOGGER.exception(
                                "Не удалось обработать изображение для %s: %s",
                                normalized.product_url,
                                exc,
                            )
                            media_result = MediaUploadResult(
                                sha256=None,
                                direct_url=None,
                                viewer_url=None,
                                thumb_url=None,
                                original_url=normalized.hero_image_url,
                                uploaded=False,
                                cached=False,
                            )

                        if media_result.sha256:
                            image_sha = media_result.sha256
                            cached_image = state.get_image(media_result.sha256)
                        else:
                            cached_image = (
                                state.get_image(image_sha) if image_sha else None
                            )
                        if media_result.direct_url:
                            image_direct_url = media_result.direct_url
                            image_viewer_url = media_result.viewer_url
                            image_thumb_url = media_result.thumb_url
                        elif cached_image:
                            image_direct_url = cached_image.direct_url
                            image_viewer_url = cached_image.viewer_url
                            image_thumb_url = cached_image.thumb_url

                        normalized.image_direct_url = image_direct_url
                        normalized.image_viewer_url = image_viewer_url
                        normalized.image_thumb_url = image_thumb_url
                        normalized.image_sha256 = image_sha

                        target_status = "new" if product_record is None else "updated"
                        if not image_direct_url:
                            if media_error is None:
                                media_error = (
                                    "FreeImage upload skipped (missing API key or "
                                    "empty response)."
                                )
                            target_status = "error"
                        record = sheets_writer.build_record(
                            product_url=normalized.product_url,
                            title=normalized.title,
                            price_value=normalized.price_value,
                            country=normalized.country,
                            volume_l=normalized.volume_l,
                            abv_percent=normalized.abv_percent,
                            age_years=normalized.age_years,
                            brand=normalized.brand,
                            producer=normalized.producer,
                            tasting_notes=normalized.tasting_notes,
                            gastronomy=normalized.gastronomy,
                            grapes=normalized.grapes,
                            maturation=normalized.maturation,
                            gift_packaging=normalized.gift_packaging,
                            position=current_position,
                            image_direct_url=image_direct_url,
                            status=target_status,
                            error_msg=media_error,
                        )
                        status = await sheets_writer.upsert(record)
                        LOGGER.info(
                            "Запись в Google Sheets для %s завершена со статусом %s",
                            normalized.product_url,
                            status,
                        )
                        if status == "new":
                            inserted += 1
                        elif status == "updated":
                            updated += 1
                        else:
                            skipped += 1
                    else:
                        skipped += 1

                    state.upsert_product(
                        product_url=normalized.product_url,
                        product_id=product_id,
                        etag_hash=etag_hash,
                        image_sha256=image_sha,
                    )
                    LOGGER.info("Сохранено состояние для %s", normalized.product_url)
        finally:
            await media_uploader.aclose()
            state.close()

    LOGGER.info(
        "Crawler: %s страниц, %s уникальных карточек",
        crawler.metrics.pages_processed,
        crawler.metrics.unique_products,
    )
    LOGGER.info(
        "Parser: %s карточек, ошибок: %s",
        parser.metrics.products_parsed,
        parser.metrics.failures,
    )
    LOGGER.info(
        "Normalizer: %s карточек, LLM вызовов: %s, сбоев: %s",
        normalizer.metrics.items_processed,
        normalizer.metrics.llm_calls,
        normalizer.metrics.llm_failures,
    )
    LOGGER.info(
        "Sheets: inserted=%s updated=%s skipped=%s",
        inserted,
        updated,
        skipped,
    )

    return normalized_products


def configure_logging() -> None:
    """Базовая настройка логирования."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


@asynccontextmanager
async def _launch_browser(settings: Settings) -> AsyncIterator[BrowserContext]:
    """Запустить браузер с учётом настроек и отдать контекст."""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=settings.headless)
        context_kwargs: Dict[str, object] = {
            "user_agent": settings.choice_user_agent(),
        }

        proxy_config = _build_proxy_config(settings)
        if proxy_config:
            context_kwargs["proxy"] = proxy_config

        context = await browser.new_context(**context_kwargs)
        try:
            yield context
        finally:
            await context.close()
            await browser.close()


def _build_proxy_config(settings: Settings) -> Optional[Dict[str, str]]:
    """Подготовить конфиг прокси, если он требуется."""
    if not settings.use_proxy:
        return None
    server = settings.http_proxy or settings.https_proxy
    if not server:
        return None
    return {"server": server}


def main() -> None:
    """Запустить асинхронный сценарий."""
    asyncio.run(run())


if __name__ == "__main__":
    main()
