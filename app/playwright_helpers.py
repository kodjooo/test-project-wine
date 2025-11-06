"""Вспомогательные функции для работы с Playwright."""

from __future__ import annotations

from playwright.async_api import Locator, Page


AGE_CONFIRM_SELECTORS = [
    "button:has-text('Мне исполнилось 18 лет')",
    "a:has-text('Мне исполнилось 18 лет')",
    "[data-modal-id='age-confirm'] button.ui-button",
]


async def close_age_confirmation(page: Page) -> None:
    """Закрыть модальное окно подтверждения возраста, если оно появилось."""
    for selector in AGE_CONFIRM_SELECTORS:
        try:
            locator: Locator = page.locator(selector)
            if await locator.first.is_visible(timeout=500):
                await locator.first.click()
                await page.wait_for_timeout(200)
                return
        except Exception:
            # Игнорируем любые ошибки, модалка просто не появилась.
            continue
