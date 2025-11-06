"""Запись и обновление данных в Google Sheets."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import gspread
from google.oauth2 import service_account
from gspread.exceptions import WorksheetNotFound
from gspread.utils import rowcol_to_a1
import logging

from app.config import Settings
from app.state import StateRepository

SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SHEET_COLUMNS: List[str] = [
    "PRODUCT_URL",
    "POSITION",
    "TITLE",
    "PRICE_VALUE",
    "COUNTRY",
    "VOLUME_L",
    "ABV_PERCENT",
    "AGE_YEARS",
    "BRAND",
    "PRODUCER",
    "TASTING_NOTES",
    "GASTRONOMY",
    "GRAPES_JSON",
    "MATURATION",
    "GIFT_PACKAGING",
    "IMAGE_DIRECT_URL",
    "IMAGE_CELL",
    "STATUS",
    "ERROR_MSG",
]


def _column_letter(index: int) -> str:
    """Преобразовать индекс колонки (1-based) в буквенное представление A..Z."""
    if index < 1:
        raise ValueError("Column index должен быть >= 1")
    result = []
    while index:
        index, remainder = divmod(index - 1, 26)
        result.append(chr(65 + remainder))
    return "".join(reversed(result))


@dataclass(slots=True)
class SheetRecord:
    """Данные для записи строки в Google Sheets."""

    unique_key: str
    values: Dict[str, Optional[str]]

    def to_row(self) -> List[Optional[str]]:
        return [self.values.get(column, "") for column in SHEET_COLUMNS]


class SheetsWriter:
    """Обновляет Google Sheets, выполняя upsert-записи по PRODUCT_URL."""

    def __init__(self, settings: Settings, state: StateRepository) -> None:
        self._settings = settings
        self._state = state
        self._enabled = self._is_enabled()
        self._client: Optional[gspread.Client] = None
        self._worksheet = None
        self._logger = logging.getLogger(__name__)

    async def upsert(self, record: SheetRecord) -> str:
        """Добавить или обновить строку и вернуть статус new/updated/skipped."""
        if not self._enabled:
            self._logger.info(
                "Пропуск записи в Sheets: сервис отключён или отсутствуют креды."
            )
            return "skipped"

        worksheet = await self._get_worksheet()
        if worksheet is None:
            self._logger.warning(
                "Не удалось получить рабочий лист Google Sheets, запись пропущена."
            )
            return "skipped"

        await asyncio.to_thread(self._ensure_header, worksheet)

        row_values = record.to_row()
        existing_row = await asyncio.to_thread(
            self._find_row_index, worksheet, record.unique_key
        )

        if existing_row:
            await asyncio.to_thread(
                self._update_row, worksheet, existing_row, row_values
            )
            self._logger.info(
                "Строка Sheets обновлена (row=%s, key=%s)",
                existing_row,
                record.unique_key,
            )
            return "updated"

        await asyncio.to_thread(
            worksheet.append_row,
            row_values,
            value_input_option="USER_ENTERED",
        )
        self._logger.info("Добавлена новая строка в Sheets (key=%s)", record.unique_key)
        return "new"

    def build_record(
        self,
        *,
        product_url: str,
        position: int,
        title: Optional[str],
        price_value: Optional[float],
        country: Optional[str],
        volume_l: Optional[float],
        abv_percent: Optional[float],
        age_years: Optional[int],
        brand: Optional[str],
        producer: Optional[str],
        tasting_notes: Optional[str],
        gastronomy: Optional[str],
        grapes: List[str],
        maturation: Optional[str],
        gift_packaging: Optional[str],
        image_direct_url: Optional[str],
        status: str,
        error_msg: Optional[str] = None,
    ) -> SheetRecord:
        """Подготовить запись для Google Sheets."""
        values: Dict[str, Optional[str]] = {
            "PRODUCT_URL": product_url,
            "POSITION": str(position),
            "TITLE": title or "",
            "PRICE_VALUE": self._format_number(price_value),
            "COUNTRY": country or "",
            "VOLUME_L": self._format_number(volume_l),
            "ABV_PERCENT": self._format_number(abv_percent),
            "AGE_YEARS": self._format_number(age_years),
            "BRAND": brand or "",
            "PRODUCER": producer or "",
            "TASTING_NOTES": tasting_notes or "",
            "GASTRONOMY": gastronomy or "",
            "GRAPES_JSON": json.dumps(grapes, ensure_ascii=False),
            "MATURATION": maturation or "",
            "GIFT_PACKAGING": gift_packaging or "",
            "IMAGE_DIRECT_URL": image_direct_url or "",
            "IMAGE_CELL": f"=IMAGE(\"{image_direct_url}\")" if image_direct_url else "",
            "STATUS": status,
            "ERROR_MSG": error_msg or "",
        }
        return SheetRecord(unique_key=product_url, values=values)

    async def get_last_position(self) -> int:
        """Получить максимальную позицию товара из таблицы (для продолжения обработки)."""
        if not self._enabled:
            self._logger.info("Sheets отключён — продолжаем с первой позиции.")
            return 0
        worksheet = await self._get_worksheet()
        if worksheet is None:
            self._logger.warning("Worksheet недоступен, продолжаем с первой позиции.")
            return 0
        await asyncio.to_thread(self._ensure_header, worksheet)
        try:
            col_idx = SHEET_COLUMNS.index("POSITION") + 1
            column_values = await asyncio.to_thread(
                worksheet.col_values, col_idx
            )
        except gspread.exceptions.APIError as exc:
            self._logger.warning(
                "Не удалось получить колонку POSITION из Sheets: %s", exc
            )
            return 0

        max_position = 0
        for value in column_values[1:]:
            if not value:
                continue
            try:
                position = int(value)
            except ValueError:
                continue
            if position > max_position:
                max_position = position
        self._logger.info("Последняя обработанная позиция в Sheets: %s", max_position)
        return max_position

    def _ensure_header(self, worksheet) -> None:
        current_header = worksheet.row_values(1)
        if current_header == SHEET_COLUMNS:
            return
        if current_header:
            # Дополним существующими колонками, чтобы не потерять данные.
            worksheet.update(
                f"A1:{_column_letter(len(SHEET_COLUMNS))}1",
                [SHEET_COLUMNS],
                value_input_option="RAW",
            )
        else:
            worksheet.append_row(SHEET_COLUMNS, value_input_option="RAW")
        self._logger.info("Заголовок Google Sheets синхронизирован с текущей схемой.")

    def _find_row_index(self, worksheet, unique_key: str) -> Optional[int]:
        product_id_col = SHEET_COLUMNS.index("PRODUCT_URL") + 1
        try:
            column_values = worksheet.col_values(product_id_col)
        except gspread.exceptions.APIError as exc:
            self._logger.warning(
                "Не удалось получить столбец PRODUCT_URL из Sheets: %s", exc
            )
            return None

        for index, value in enumerate(column_values[1:], start=2):
            if value == unique_key:
                return index
        return None

    def _update_row(self, worksheet, row_index: int, values: List[Optional[str]]) -> None:
        last_cell = rowcol_to_a1(row_index, len(SHEET_COLUMNS))
        last_col = "".join(filter(str.isalpha, last_cell))
        range_name = f"A{row_index}:{last_col}{row_index}"
        worksheet.update(range_name, [values], value_input_option="USER_ENTERED")

    async def _get_worksheet(self):
        if self._worksheet is not None:
            return self._worksheet
        client = await self._get_client()
        if client is None:
            self._logger.warning("Клиент Google Sheets недоступен, worksheet не получен.")
            return None
        try:
            spreadsheet = await asyncio.to_thread(
                client.open_by_key, self._settings.gsheet_id
            )
            self._worksheet = await asyncio.to_thread(
                spreadsheet.worksheet, self._settings.gsheet_tab
            )
            self._logger.info(
                "Получен worksheet %s из таблицы %s",
                self._settings.gsheet_tab,
                self._settings.gsheet_id,
            )
        except (WorksheetNotFound, gspread.SpreadsheetNotFound, gspread.exceptions.APIError) as exc:
            self._logger.warning("Не удалось открыть лист Google Sheets: %s", exc)
            return None
        return self._worksheet

    async def _get_client(self) -> Optional[gspread.Client]:
        if self._client is not None:
            return self._client
        try:
            credentials = await asyncio.to_thread(self._load_credentials)
            if credentials is None:
                self._logger.warning("Не удалось загрузить креды сервисного аккаунта.")
                return None
            self._client = await asyncio.to_thread(
                gspread.authorize,
                credentials,
            )
            self._logger.info("Авторизация в Google Sheets выполнена успешно.")
        except (OSError, ValueError, gspread.exceptions.APIError) as exc:
            self._logger.warning("Авторизация Google Sheets не удалась: %s", exc)
            return None
        return self._client

    def _load_credentials(self):
        self._logger.debug(
            "Чтение файла сервисного аккаунта: %s", self._settings.google_sa_json
        )
        return service_account.Credentials.from_service_account_file(
            self._settings.google_sa_json,
            scopes=SHEETS_SCOPES,
        )

    def _is_enabled(self) -> bool:
        if not self._settings.gsheet_id or not self._settings.google_sa_json:
            return False
        enabled = Path(self._settings.google_sa_json).exists()
        if not enabled:
            self._logger.warning(
                "Файл сервисного аккаунта не найден по пути %s",
                self._settings.google_sa_json,
            )
        return enabled

    def _format_number(self, value: Optional[float]) -> str:
        if value is None:
            return ""
        return f"{value:.2f}".rstrip("0").rstrip(".")
