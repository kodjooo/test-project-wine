"""Хелперы для расчёта контрольных сумм."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict

from app.models import ProductNormalized


def product_etag(product: ProductNormalized) -> str:
    """Вычислить контрольную сумму карточки по основным полям."""
    payload: Dict[str, Any] = {
        "product_url": product.product_url,
        "product_id": product.product_id,
        "title": product.title,
        "price_value": product.price_value,
        "price_currency": product.price_currency,
        "country": product.country,
        "volume_l": product.volume_l,
        "abv_percent": product.abv_percent,
        "availability": product.availability,
        "age_years": product.age_years,
        "brand": product.brand,
        "producer": product.producer,
        "sku": product.sku,
        "tasting_notes": product.tasting_notes,
        "gastronomy": product.gastronomy,
        "grapes": product.grapes,
        "maturation": product.maturation,
        "awards": product.awards,
        "gift_packaging": product.gift_packaging,
        "breadcrumbs": product.breadcrumbs,
        "image_url": product.hero_image_url,
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
