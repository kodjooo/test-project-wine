"""Загрузка изображений товара на FreeImage.host с кешированием по SHA-256."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import httpx
import logging

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
        self._logger = logging.getLogger(__name__)
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
            self._logger.info(
                "Изображение пропущено: неподдерживаемый URL %s", original_url
            )
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
            self._logger.info(
                "Изображение взято из кеша по оригинальному URL: %s", original_url
            )
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
            self._logger.info(
                "Не удалось загрузить изображение по URL, пробуем скачать: %s",
                original_url,
            )
            # Фолбэк: скачиваем изображение и загружаем бинарно
            image_bytes = await self._download(original_url)
            if image_bytes is None:
                self._logger.warning(
                    "Не удалось скачать изображение для %s", original_url
                )
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
                self._logger.info(
                    "Найдено изображение с тем же SHA-256, переиспользуем запись."
                )
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
                self._logger.warning(
                    "Не удалось загрузить изображение бинарно для %s", original_url
                )
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
            self._logger.info(
                "Изображение успешно загружено по URL: %s", original_url
            )
            # Загружено по URL — вычислим SHA по скачанному контенту (с direct_url или оригинала)
            image_bytes = await self._download(upload_response["direct_url"])
            if image_bytes is None:
                self._logger.warning(
                    "Не удалось скачать direct_url %s, пробуем оригинал %s",
                    upload_response.get("direct_url"),
                    original_url,
                )
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
            self._logger.info(
                "Сохранена информация об изображении: sha=%s, direct_url=%s",
                image_sha,
                direct_url,
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
            self._logger.info(
                "Пропуск загрузки изображения: отсутствует FREEIMAGE_API_KEY"
            )
            return None

        last_error: Optional[str] = None
        for attempt in range(self._settings.freeimage_max_retries + 1):
            try:
                self._logger.debug(
                    "Запрос к FreeImage (попытка %s): %s",
                    attempt + 1,
                    self._settings.freeimage_api_endpoint,
                )
                response = await self._http_client.post(
                    self._settings.freeimage_api_endpoint,
                    data=data,
                    files=files,
                )
                json_payload = self._parse_response(response)
                if json_payload is not None:
                    self._logger.debug("FreeImage ответ успешно распарсен")
                    return json_payload
                last_error = response.text
                self._logger.warning(
                    "FreeImage вернул неожиданный ответ (попытка %s): %s",
                    attempt + 1,
                    last_error,
                )
            except httpx.HTTPError as exc:
                last_error = str(exc)
                self._logger.warning(
                    "Ошибка запроса к FreeImage (попытка %s): %s",
                    attempt + 1,
                    exc,
                )

            await asyncio.sleep(min(2 ** attempt, 5))

        if last_error:
            raise RuntimeError(
                f"FreeImage upload failed after retries: {last_error}"
            )
        return None

    def _parse_response(self, response: httpx.Response) -> Optional[dict]:
        if response.status_code != 200:
            self._logger.warning(
                "FreeImage вернул статус %s: %s",
                response.status_code,
                response.text,
            )
            return None
        try:
            payload = response.json()
        except json.JSONDecodeError:
            self._logger.warning("FreeImage вернул не-JSON ответ: %s", response.text)
            return None
        success = payload.get("success") or {}
        if isinstance(success, dict):
            code = success.get("code")
            if code != 200:
                self._logger.warning(
                    "FreeImage ответ без кода успеха: %s", payload
                )
                return None
        image_info = payload.get("image") or {}
        direct_url = image_info.get("url")
        if not direct_url:
            self._logger.warning(
                "FreeImage ответ без direct_url: %s", payload
            )
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
