"""Сервис нормализации данных карточки товара."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from app.config import Settings
from app.llm import LLMClient, LLMUnavailableError
from app.models import ProductNormalized, ProductRaw, ProductSection
from app.utils import clean_text, extract_abv_percent, extract_float_with_unit, extract_price_value

LOGGER = logging.getLogger(__name__)

AGE_REGEX = re.compile(r"(\d{1,3})\s*(?:yo|y\.o\.|год(?:а|ов)?|лет)", re.IGNORECASE)


@dataclass(slots=True)
class NormalizerMetrics:
    """Статистика по нормализации карточек."""

    items_processed: int = 0
    llm_calls: int = 0
    llm_failures: int = 0


class ProductNormalizer:
    """Нормализует данные карточки, при необходимости обращаясь к LLM."""

    def __init__(self, settings: Settings, llm_client: Optional[LLMClient] = None) -> None:
        self._settings = settings
        self._llm_client = llm_client or (
            LLMClient(settings) if settings.openai_api_key else None
        )
        self.metrics = NormalizerMetrics()

    async def normalize(self, raw: ProductRaw) -> ProductNormalized:
        """Привести сырые данные к унифицированному виду."""
        price_value, price_currency = await self._normalize_price(raw)
        volume_l, abv_percent = await self._normalize_volume_abv(raw)

        age_years = self._extract_age(raw)
        availability = self._normalize_availability(raw.availability_text)

        sections = await self._normalize_sections(raw.sections)

        grapes_list = raw.grapes
        maybe_grapes = sections.get("grapes_list")
        if isinstance(maybe_grapes, list) and maybe_grapes:
            grapes_list = [item for item in maybe_grapes if item]

        producer_candidate = sections.get("producer")
        producer_value = clean_text(raw.producer)
        if isinstance(producer_candidate, str) and producer_candidate:
            producer_value = clean_text(producer_candidate)

        tasting_value = sections.get("tasting_notes")
        tasting_notes = clean_text(tasting_value) if isinstance(tasting_value, str) else None

        gastronomy_value = sections.get("gastronomy")
        gastronomy = clean_text(gastronomy_value) if isinstance(gastronomy_value, str) else None

        maturation_value = sections.get("maturation")
        maturation = clean_text(maturation_value) if isinstance(maturation_value, str) else None

        awards_value = sections.get("awards")
        awards = clean_text(awards_value) if isinstance(awards_value, str) else None

        gift_packaging_value = sections.get("gift_packaging")
        gift_packaging = (
            clean_text(gift_packaging_value)
            if isinstance(gift_packaging_value, str)
            else None
        )

        normalized = ProductNormalized(
            product_url=raw.product_url,
            source_page_url=raw.source_page_url,
            page_number=raw.page_number,
            product_id=raw.product_id or self._fallback_product_id(raw.product_url),
            title=clean_text(raw.title),
            sku=clean_text(raw.sku),
            country=clean_text(raw.country),
            brand=clean_text(raw.brand),
            producer=producer_value,
            price_value=price_value,
            price_currency=price_currency,
            volume_l=volume_l,
            abv_percent=abv_percent,
            age_years=age_years,
            availability=availability,
            tasting_notes=tasting_notes,
            gastronomy=gastronomy,
            grapes=grapes_list,
            maturation=maturation,
            awards=awards,
            gift_packaging=gift_packaging,
            breadcrumbs=raw.breadcrumbs,
            image_urls=raw.image_urls,
            hero_image_url=raw.hero_image_url,
            raw_sections=raw.sections,
            raw=raw,
        )

        self.metrics.items_processed += 1
        return normalized

    async def _normalize_price(self, raw: ProductRaw) -> Tuple[Optional[float], Optional[str]]:
        price_value = raw.price_value
        price_currency = raw.price_currency

        if price_value is None and raw.price_text:
            price_value = extract_price_value(raw.price_text)
        if price_currency is None and price_value is not None:
            price_currency = "RUB"

        if price_value is None and raw.price_text:
            llm_payload = await self._maybe_call_llm("price", raw.price_text)
            if llm_payload:
                price_value = self._safe_float(llm_payload.get("price_value"))
                price_currency = clean_text(llm_payload.get("currency"))

        return price_value, price_currency

    async def _normalize_volume_abv(self, raw: ProductRaw) -> Tuple[Optional[float], Optional[float]]:
        volume_l = raw.volume_l or extract_float_with_unit(raw.volume_text)
        abv_percent = raw.abv_percent or extract_abv_percent(raw.abv_text)

        if (volume_l is None or abv_percent is None) and (
            raw.volume_text or raw.abv_text
        ):
            source_text = " ".join(
                filter(None, [raw.volume_text or "", raw.abv_text or ""])
            )
            llm_payload = await self._maybe_call_llm("volume_abv", source_text)
            if llm_payload:
                volume_l = volume_l or self._safe_float(llm_payload.get("volume_l"))
                abv_percent = abv_percent or self._safe_float(llm_payload.get("abv"))

        return volume_l, abv_percent

    def _extract_age(self, raw: ProductRaw) -> Optional[int]:
        candidates = [
            raw.title or "",
            raw.sections.get("maturation").text if raw.sections.get("maturation") else "",
        ]
        for text in candidates:
            if not text:
                continue
            match = AGE_REGEX.search(text)
            if match:
                try:
                    return int(match.group(1))
                except ValueError:
                    continue
        return None

    def _normalize_availability(self, availability_text: Optional[str]) -> Optional[bool]:
        if not availability_text:
            return None
        lowered = availability_text.lower()
        if "в наличии" in lowered:
            return True
        if "нет в наличии" in lowered or "ожидается" in lowered or "под заказ" in lowered:
            return False
        return None

    async def _normalize_sections(self, sections: Dict[str, ProductSection]) -> Dict[str, object]:
        result: Dict[str, object] = {}

        tasting_text, _ = await self._section_with_llm(sections.get("tasting_notes"))
        result["tasting_notes"] = tasting_text

        gastronomy_text, _ = await self._section_with_llm(sections.get("gastronomy"))
        result["gastronomy"] = gastronomy_text

        grapes_text, grapes_items = await self._section_with_llm(sections.get("grapes"))
        result["grapes"] = grapes_text
        result["grapes_list"] = grapes_items

        maturation_text, _ = await self._section_with_llm(sections.get("maturation"))
        result["maturation"] = maturation_text

        awards_text, _ = await self._section_with_llm(sections.get("awards"))
        result["awards"] = awards_text

        producer_text, producer_items = await self._section_with_llm(sections.get("producer"))
        if producer_items:
            result["producer"] = producer_items[0]
        else:
            result["producer"] = producer_text

        gift_packaging_text, _ = await self._section_with_llm(sections.get("gift_packaging"))
        result["gift_packaging"] = gift_packaging_text

        return result

    async def _section_with_llm(
        self, section: Optional[ProductSection]
    ) -> Tuple[Optional[str], list[str]]:
        if not section:
            return None, []
        text = clean_text(section.text)
        items = [item for item in section.items if item]
        if text or items:
            return text, items
        if section.html:
            llm_payload = await self._maybe_call_llm(
                "section", {"title": section.title, "html": section.html}
            )
            if llm_payload:
                text = clean_text(llm_payload.get("text"))
                items_list = llm_payload.get("list") or []
                clean_items: list[str] = []
                if isinstance(items_list, list):
                    for item in items_list:
                        normalized = clean_text(str(item))
                        if normalized:
                            clean_items.append(normalized)
                return text, clean_items
        return text, items

    def _section_text(self, section: Optional[ProductSection]) -> Optional[str]:
        if not section:
            return None
        return clean_text(section.text)

    async def _maybe_call_llm(
        self, mode: str, data: object
    ) -> Optional[Dict[str, object]]:
        if not self._llm_client:
            return None
        try:
            if mode == "price":
                payload = await self._llm_client.normalize_price(str(data))
            elif mode == "volume_abv":
                payload = await self._llm_client.parse_volume_abv(str(data))
            elif mode == "section":
                assert isinstance(data, dict)
                payload = await self._llm_client.extract_section(
                    section=str(data.get("title", "")),
                    html=str(data.get("html", "")),
                )
            else:
                return None
            self.metrics.llm_calls += 1
            return payload
        except LLMUnavailableError as exc:
            self.metrics.llm_failures += 1
            LOGGER.debug("LLM недоступен (%s): %s", mode, exc)
            return None

    def _safe_float(self, value: object) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    def _fallback_product_id(self, url: str) -> str:
        """Fallback ID на основе URL (sha256)."""
        import hashlib

        return hashlib.sha256(url.encode("utf-8")).hexdigest()
