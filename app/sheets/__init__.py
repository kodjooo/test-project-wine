"""Интерфейс записи данных в Google Sheets."""

from .service import SheetRecord, SheetsWriter

__all__ = ["SheetsWriter", "SheetRecord"]
