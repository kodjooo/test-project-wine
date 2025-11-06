"""Запись и обновление данных в Google Sheets."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import gspread
from google.oauth2 import service_account
from gspread.exceptions import WorksheetNotFound
from gspread.utils import rowcol_to_a1

from app.config import Settings
from app.state import StateRepository

SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SHEET_COLUMNS: List[str] = [
    "TIMESTAMP_UTC",
    "SOURCE_CATEGORY_URL",
    "PAGE_NUM",
    "PRODUCT_URL",
    "PRODUCT_ID",
    "TITLE",
    "PRICE_VALUE",
    "PRICE_CURRENCY",
    "COUNTRY",
    "VOLUME_L",
    "ABV_PERCENT",
    "AGE_YEARS",
    "BRAND",
    "PRODUCER",
    "SKU",
    "TASTING_NOTES",
    "GASTRONOMY",
    "GRAPES_JSON",
    "MATURATION",
    "AWARDS",
    "GIFT_PACKAGING",
    "BREADCRUMBS",
    "IMAGE_ORIGINAL_URL",
    "IMAGE_DIRECT_URL",
    "IMAGE_VIEWER_URL",
    "IMAGE_THUMB_URL",
    "IMAGE_SHA256",
    "IMAGE_CELL",
    "STATUS",
    "ERROR_MSG",
]


@dataclass(slots=True)
class SheetRecord:
    """Данные для записи строки в Google Sheets."""

    unique_key: str
    values: Dict[str, Optional[str]]

    def to_row(self) -> List[Optional[str]]:
        return [self.values.get(column, "") for column in SHEET_COLUMNS]


class SheetsWriter:
    """Обновляет Google Sheets, выполняя upsert-записи по PRODUCT_ID."""

    def __init__(self, settings: Settings, state: StateRepository) -> None:
        self._settings = settings
        self._state = state
        self._enabled = self._is_enabled()
        self._client: Optional[gspread.Client] = None
        self._worksheet = None

    async def upsert(self, record: SheetRecord) -> str:
        """Добавить или обновить строку и вернуть статус new/updated/skipped."""
        if not self._enabled:
            return "skipped"

        worksheet = await self._get_worksheet()
        if worksheet is None:
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
            return "updated"

        await asyncio.to_thread(
            worksheet.append_row,
            row_values,
            value_input_option="USER_ENTERED",
        )
        return "new"

    def build_record(
        self,
        *,
        product_id: str,
        category_url: str,
        page_number: Optional[int],
        product_url: str,
        title: Optional[str],
        price_value: Optional[float],
        price_currency: Optional[str],
        country: Optional[str],
        volume_l: Optional[float],
        abv_percent: Optional[float],
        age_years: Optional[int],
        brand: Optional[str],
        producer: Optional[str],
        sku: Optional[str],
        tasting_notes: Optional[str],
        gastronomy: Optional[str],
        grapes: List[str],
        maturation: Optional[str],
        awards: Optional[str],
        gift_packaging: Optional[str],
        breadcrumbs: List[str],
        image_original_url: Optional[str],
        image_direct_url: Optional[str],
        image_viewer_url: Optional[str],
        image_thumb_url: Optional[str],
        image_sha256: Optional[str],
        status: str,
        error_msg: Optional[str] = None,
    ) -> SheetRecord:
        """Подготовить запись для Google Sheets."""
        values: Dict[str, Optional[str]] = {
            "TIMESTAMP_UTC": self._utc_now(),
            "SOURCE_CATEGORY_URL": category_url,
            "PAGE_NUM": str(page_number) if page_number is not None else "",
            "PRODUCT_URL": product_url,
            "PRODUCT_ID": product_id,
            "TITLE": title or "",
            "PRICE_VALUE": self._format_number(price_value),
            "PRICE_CURRENCY": price_currency or "",
            "COUNTRY": country or "",
            "VOLUME_L": self._format_number(volume_l),
            "ABV_PERCENT": self._format_number(abv_percent),
            "AGE_YEARS": self._format_number(age_years),
            "BRAND": brand or "",
            "PRODUCER": producer or "",
            "SKU": sku or "",
            "TASTING_NOTES": tasting_notes or "",
            "GASTRONOMY": gastronomy or "",
            "GRAPES_JSON": json.dumps(grapes, ensure_ascii=False),
            "MATURATION": maturation or "",
            "AWARDS": awards or "",
            "GIFT_PACKAGING": gift_packaging or "",
            "BREADCRUMBS": " > ".join(breadcrumbs),
            "IMAGE_ORIGINAL_URL": image_original_url or "",
            "IMAGE_DIRECT_URL": image_direct_url or "",
            "IMAGE_VIEWER_URL": image_viewer_url or "",
            "IMAGE_THUMB_URL": image_thumb_url or "",
            "IMAGE_SHA256": image_sha256 or "",
            "IMAGE_CELL": f"=IMAGE(\"{image_direct_url}\")" if image_direct_url else "",
            "STATUS": status,
            "ERROR_MSG": error_msg or "",
        }
        return SheetRecord(unique_key=product_id, values=values)

    def _ensure_header(self, worksheet) -> None:
        current_header = worksheet.row_values(1)
        if current_header == SHEET_COLUMNS:
            return
        if current_header:
            # Дополним существующими колонками, чтобы не потерять данные.
            worksheet.update(
                f"A1:{col_to_letter(len(SHEET_COLUMNS))}1",
                [SHEET_COLUMNS],
                value_input_option="RAW",
            )
        else:
            worksheet.append_row(SHEET_COLUMNS, value_input_option="RAW")

    def _find_row_index(self, worksheet, unique_key: str) -> Optional[int]:
        product_id_col = SHEET_COLUMNS.index("PRODUCT_ID") + 1
        try:
            column_values = worksheet.col_values(product_id_col)
        except gspread.exceptions.APIError:
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
            return None
        try:
            spreadsheet = await asyncio.to_thread(
                client.open_by_key, self._settings.gsheet_id
            )
            self._worksheet = await asyncio.to_thread(
                spreadsheet.worksheet, self._settings.gsheet_tab
            )
        except (WorksheetNotFound, gspread.SpreadsheetNotFound, gspread.exceptions.APIError):
            return None
        return self._worksheet

    async def _get_client(self) -> Optional[gspread.Client]:
        if self._client is not None:
            return self._client
        try:
            credentials = await asyncio.to_thread(self._load_credentials)
            if credentials is None:
                return None
            self._client = await asyncio.to_thread(
                gspread.authorize,
                credentials,
            )
        except (OSError, ValueError, gspread.exceptions.APIError):
            return None
        return self._client

    def _load_credentials(self):
        return service_account.Credentials.from_service_account_file(
            self._settings.google_sa_json,
            scopes=SHEETS_SCOPES,
        )

    def _is_enabled(self) -> bool:
        if not self._settings.gsheet_id or not self._settings.google_sa_json:
            return False
        return Path(self._settings.google_sa_json).exists()

    def _format_number(self, value: Optional[float]) -> str:
        if value is None:
            return ""
        return f"{value:.2f}".rstrip("0").rstrip(".")

    def _utc_now(self) -> str:
        return datetime.now(timezone.utc).isoformat()
