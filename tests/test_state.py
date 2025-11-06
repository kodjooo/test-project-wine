"""Тесты слоя состояния на SQLite."""

from __future__ import annotations

from pathlib import Path

from app.state import StateRepository


def test_state_repository_upsert_and_retrieve(tmp_path) -> None:
    db_path = tmp_path / "state.db"
    repo = StateRepository(db_path)

    url = "https://example.com/product"
    product_id = "SKU-1"
    etag = "etag123"
    image_sha = "imgsha"

    repo.upsert_product(url, product_id, etag, image_sha)

    record = repo.get_product(url)
    assert record is not None
    assert record.product_id == product_id
    assert record.etag_hash == etag
    assert record.image_sha256 == image_sha

    repo.save_image(
        image_sha,
        "https://img.direct/123",
        "https://viewer.freeimage/123",
        "https://thumb.freeimage/123",
        url,
    )
    image_record = repo.get_image(image_sha)
    assert image_record is not None
    assert image_record.direct_url == "https://img.direct/123"
    assert image_record.viewer_url == "https://viewer.freeimage/123"
    assert image_record.thumb_url == "https://thumb.freeimage/123"

    by_original = repo.get_image_by_original(url)
    assert by_original is not None
    assert by_original.direct_url == "https://img.direct/123"

    repo.close()
    assert Path(db_path).exists()
