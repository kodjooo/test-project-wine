"""Загрузка изображений товара на FreeImage.host с кешированием по SHA-256."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import httpx

from app.config import Settings
from app.models import ProductNormalized
from app.state import StateRepository

SUPPORTED_SCHEMES = {"http", "https"}


@dataclass(slots=True)
class MediaUploadResult:
    """Результат обработки изображения карточки."""

    sha256: Optional[str]
    direct_url: Optional[str]
    viewer_url: Optional[str]
    thumb_url: Optional[str]
    original_url: Optional[str]
    uploaded: bool
    cached: bool


class MediaUploader:
    """Загружает изображения через FreeImage.host и кеширует по SHA-256."""

    def __init__(self, settings: Settings, state: StateRepository) -> None:
        self._settings = settings
        self._state = state
        timeout = httpx.Timeout(
            connect=self._settings.freeimage_connect_timeout,
            read=self._settings.freeimage_read_timeout,
            write=self._settings.freeimage_read_timeout,
            pool=self._settings.freeimage_connect_timeout,
        )
        self._http_client = httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": settings.choice_user_agent()},
        )

    async def aclose(self) -> None:
        await self._http_client.aclose()

    async def ensure_image(self, product: ProductNormalized) -> MediaUploadResult:
        """Загрузить изображение и вернуть информацию о нём."""
        original_url = product.hero_image_url
        if not original_url or not self._is_supported_scheme(original_url):
            return MediaUploadResult(
                sha256=None,
                direct_url=None,
                viewer_url=None,
                thumb_url=None,
                original_url=original_url,
                uploaded=False,
                cached=False,
            )

        # Проверяем по оригинальному URL
        cached_original = self._state.get_image_by_original(original_url)
        if cached_original:
            return MediaUploadResult(
                sha256=cached_original.sha256,
                direct_url=cached_original.direct_url,
                viewer_url=cached_original.viewer_url,
                thumb_url=cached_original.thumb_url,
                original_url=original_url,
                uploaded=False,
                cached=True,
            )

        # Пытаемся загрузить напрямую по URL
        upload_response = await self._upload_via_url(original_url)
        image_bytes: Optional[bytes] = None
        image_sha: Optional[str] = None

        if upload_response is None:
            # Фолбэк: скачиваем изображение и загружаем бинарно
            image_bytes = await self._download(original_url)
            if image_bytes is None:
                return MediaUploadResult(
                    sha256=None,
                    direct_url=None,
                    viewer_url=None,
                    thumb_url=None,
                    original_url=original_url,
                    uploaded=False,
                    cached=False,
                )
            image_sha = self._sha256(image_bytes)
            cached = self._state.get_image(image_sha)
            if cached:
                # Обновляем связь оригинального URL с уже загруженным изображением
                self._state.save_image(
                    sha256=image_sha,
                    direct_url=cached.direct_url,
                    viewer_url=cached.viewer_url,
                    thumb_url=cached.thumb_url,
                    original_url=original_url,
                )
                return MediaUploadResult(
                    sha256=image_sha,
                    direct_url=cached.direct_url,
                    viewer_url=cached.viewer_url,
                    thumb_url=cached.thumb_url,
                    original_url=original_url,
                    uploaded=False,
                    cached=True,
                )
            upload_response = await self._upload_via_bytes(image_bytes)
            if upload_response is None:
                return MediaUploadResult(
                    sha256=image_sha,
                    direct_url=None,
                    viewer_url=None,
                    thumb_url=None,
                    original_url=original_url,
                    uploaded=False,
                    cached=False,
                )
        else:
            # Загружено по URL — вычислим SHA по скачанному контенту (с direct_url или оригинала)
            image_bytes = await self._download(upload_response["direct_url"])
            if image_bytes is None:
                image_bytes = await self._download(original_url)
            if image_bytes is not None:
                image_sha = self._sha256(image_bytes)

        direct_url = upload_response.get("direct_url")
        viewer_url = upload_response.get("viewer_url")
        thumb_url = upload_response.get("thumb_url")

        if image_bytes is not None and image_sha is None:
            image_sha = self._sha256(image_bytes)

        if image_sha:
            self._state.save_image(
                sha256=image_sha,
                direct_url=direct_url,
                viewer_url=viewer_url,
                thumb_url=thumb_url,
                original_url=original_url,
            )

        return MediaUploadResult(
            sha256=image_sha,
            direct_url=direct_url,
            viewer_url=viewer_url,
            thumb_url=thumb_url,
            original_url=original_url,
            uploaded=True,
            cached=False,
        )

    async def _upload_via_url(self, image_url: str) -> Optional[dict]:
        payload = {
            "key": self._settings.freeimage_api_key,
            "action": "upload",
            "format": "json",
            "source": image_url,
        }
        return await self._post_to_freeimage(data=payload)

    async def _upload_via_bytes(self, image_bytes: bytes) -> Optional[dict]:
        payload = {
            "key": self._settings.freeimage_api_key,
            "action": "upload",
            "format": "json",
        }
        files = {
            "source": ("image.jpg", image_bytes, "application/octet-stream"),
        }
        return await self._post_to_freeimage(data=payload, files=files)

    async def _post_to_freeimage(
        self,
        *,
        data: dict,
        files: Optional[dict] = None,
    ) -> Optional[dict]:
        if not self._settings.freeimage_api_key:
            return None

        last_error: Optional[str] = None
        for attempt in range(self._settings.freeimage_max_retries + 1):
            try:
                response = await self._http_client.post(
                    self._settings.freeimage_api_endpoint,
                    data=data,
                    files=files,
                )
                json_payload = self._parse_response(response)
                if json_payload is not None:
                    return json_payload
                last_error = response.text
            except httpx.HTTPError as exc:
                last_error = str(exc)

            await asyncio.sleep(min(2 ** attempt, 5))

        if last_error:
            raise RuntimeError(
                f"FreeImage upload failed after retries: {last_error}"
            )
        return None

    def _parse_response(self, response: httpx.Response) -> Optional[dict]:
        if response.status_code != 200:
            return None
        try:
            payload = response.json()
        except json.JSONDecodeError:
            return None
        success = payload.get("success") or {}
        if isinstance(success, dict):
            code = success.get("code")
            if code != 200:
                return None
        image_info = payload.get("image") or {}
        direct_url = image_info.get("url")
        if not direct_url:
            return None
        return {
            "direct_url": direct_url,
            "viewer_url": image_info.get("url_viewer"),
            "thumb_url": (image_info.get("thumb") or {}).get("url"),
        }

    async def _download(self, url: str) -> Optional[bytes]:
        if not url or not self._is_supported_scheme(url):
            return None
        try:
            response = await self._http_client.get(url, follow_redirects=True)
            response.raise_for_status()
            return response.content
        except httpx.HTTPError:
            return None

    def _sha256(self, data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _is_supported_scheme(self, url: str) -> bool:
        parsed = urlparse(url)
        return parsed.scheme.lower() in SUPPORTED_SCHEMES
