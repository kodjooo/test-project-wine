1) Цель
Собрать все карточки из категории https://winediscovery.ru/katalog/krepkie_napitki/filtr/drinktype-konyak/ (включая пагинацию), зайти в каждую, извлечь данные полей, загрузить главное изображение на FreeImage.host и записать строку в Google Sheets с формулой =IMAGE(). Запуск — в Docker.

2) Объём и особенности целевого сайта
— Категория содержит список карточек и пагинацию (числовые страницы). На карточке товара доступны: заголовок (h1), артикул (текст вида «Артикул: ...»), страна (ссылка около заголовка), объём (например «0.7 л»), крепость (например «40 %»), цена (руб.), наличие («Товар в наличии»), разделы с текстом: «Дегустационные характеристики», «Гастрономия», «Сортовой состав» (список сортов), «Способ выдержки», «Награды и оценки товара», «Производитель», иногда «Подарочная упаковка». Главное изображение доступно в блоке медиа/галереи на странице товара.
— Есть всплывающее подтверждение возраста. Его нужно закрывать (клик «Мне исполнилось 18 лет») или устанавливать cookie/локальное состояние через Playwright.

3) Роли и компоненты
A. Crawler (обход списка)
  • Загружает начальную категорию, собирает ссылки карточек (href вида /katalog/tovar/..).
  • Пагинация: если есть ссылки на «2», «3», … — проходить по ним. Паттерн URL может быть вида ?PAGEN_1=2&PARAM=…; шаблон следующей страницы брать из href первой найденной ссылки на следующую страницу.
  • Ограничение частоты запросов (REQUEST_DELAY_MS), ретраи (tenacity), User‑Agent ротация, опционально прокси.

B. Parser (страница товара)
  • Извлекает поля по селекторам/эвристикам (см. п. 5). Если поле не найдено (сложная верстка), отдаёт HTML‑фрагмент ИИ‑агенту на мягкий разбор.
  • Преобразует цену в число и валюту (RUB), объём — в литры (float), крепость — в проценты (float).
  • Выделяет артикул (SKU) по тексту «Артикул: …» (регекс).

C. Normalizer (ИИ‑агент)
  • Нормализует значения: цена/валюта, объём (литры), крепость (%ABV), страна (ISO‑название), бренд/производитель (строка), наличие → true/false/unknown.
  • Из разделов формирует структурированные поля: tasting_notes, gastronomy, grapes (массив), maturation, awards, gift_packaging. Никаких домыслов — только извлечённый текст.
  • Опционально извлекает «возраст/год» (например «5 YO», «5 лет» или год урожая в заголовке/описании).

D. Media Uploader
  • Пытается загрузить главное изображение (первая картинка галереи товара) напрямую по исходному URL через FreeImage.host; при отказе скачивает и отправляет бинарно.
  • Возвращает direct url, viewer url и thumb url, кеширует связку SHA‑256 → ссылки в SQLite.

E. Sheets Writer
  • Upsert по уникальному ключу (PRODUCT_ID, если есть; иначе хэш PRODUCT_URL).
  • Вставляет строку; в колонку IMAGE_CELL пишет формулу =IMAGE(IMAGE_DIRECT_URL).
  • При повторном запуске обновляет только изменившиеся поля; изображение не перезагружает, если хэш не изменился.

F. State & Dedup
  • SQLite в томе контейнера: таблицы visited_urls(product_url, updated_at, etag_hash), image_hashes(sha256 → direct_url/viewer_url/thumb_url).

G. Telemetry
  • Счётчики: pages, products_total, inserted, updated, skipped, errors. Итоговый отчёт в лог и (опционально) веб‑хук Telegram.

4) Конфигурация (ENV + JSON)
.env
  CATEGORY_URL=https://winediscovery.ru/katalog/krepkie_napitki/filtr/drinktype-konyak/
  GSHEET_ID=1...your...
  GSHEET_TAB=Products
  GOOGLE_SA_JSON=/secrets/sa.json
  FREEIMAGE_API_KEY=<api_key>
  FREEIMAGE_API_ENDPOINT=https://freeimage.host/api/1/upload
  FREEIMAGE_CONNECT_TIMEOUT=15
  FREEIMAGE_READ_TIMEOUT=60
  FREEIMAGE_MAX_RETRIES=3
  HEADLESS=true
  REQUEST_DELAY_MS=1200
  MAX_CONCURRENCY=3
  USE_PROXY=false
  HTTP_PROXY=
  HTTPS_PROXY=

config.json (пример — при необходимости подправить после инспекции DOM):
{
  "pagination": {
    "strategy": "href_pattern",               // читать href у ссылок страниц и переходить по следующему номеру
    "page_link_selector": "a[href*='PAGEN_1=']", 
    "max_pages": 0
  },
  "list_page": {
    "product_link_selector": "a[href^='/katalog/tovar/']",
    "price_selector": ".card .price, .product-price, .price" 
  },
  "product_page": {
    "title_selector": "h1",
    "sku_selector_like": "text()='Артикул:'",                // поиск узла с текстом и следующего за ним значения (регекс /Артикул:\s*(.+)/)
    "country_near_title_selector": "a[href*='/katalog/'][rel!='nofollow']",
    "volume_selector_like": "text()*=' л'",
    "abv_selector_like": "text()*=' %'",
    "price_selector": ".price, .product-price, [class*='price']",
    "availability_selector_like": "text()*='в наличии'",
    "brand_selector_like": "text()*='##', 'Производитель', ссылки около заголовка",
    "sections": {
      "tasting_notes_heading": "//*[contains(., 'Дегустационные характеристики')]",
      "gastronomy_heading":    "//*[contains(., 'Гастрономия')]",
      "grapes_heading":        "//*[contains(., 'Сортовой состав')]",
      "maturation_heading":    "//*[contains(., 'Способ выдержки')]",
      "awards_heading":        "//*[contains(., 'Награды')]",
      "producer_heading":      "//*[contains(., 'Производитель')]",
      "gift_packaging_heading":"//*[contains(., 'Подарочная упаковка')]"
    },
    "image_selector": ".product img, .gallery img, img[src*='/upload/']"
  },
  "upsert": {
    "unique_key": "PRODUCT_ID",
    "fallback_unique_key": "PRODUCT_URL"
  },
  "llm": {
    "enabled": true,
    "provider": "OpenAI",
    "model": "gpt-4o-mini",
    "prompts": {
      "normalize_price": "Верни JSON {price_value:number, currency:string} из строки цены: {{text}}",
      "extract_sections": "Из HTML‑фрагмента под заголовком «{{section}}» извлеки чистый текст; верни JSON {text:string, list?:string[]}",
      "parse_volume_abv": "Из текста {{text}} достань объём в литрах и крепость в %; верни JSON {volume_l:number|null, abv:number|null}"
    }
  }
}

5) Поля для Google Sheets (лист GSHEET_TAB)
TIMESTAMP_UTC, SOURCE_CATEGORY_URL, PAGE_NUM, PRODUCT_URL, PRODUCT_ID, TITLE, PRICE_VALUE, PRICE_CURRENCY,
COUNTRY, VOLUME_L, ABV_PERCENT, AGE_YEARS, BRAND, PRODUCER, SKU,
TASTING_NOTES, GASTRONOMY, GRAPES_JSON, MATURATION, AWARDS, GIFT_PACKAGING,
BREADCRUMBS, IMAGE_ORIGINAL_URL, IMAGE_DIRECT_URL, IMAGE_VIEWER_URL, IMAGE_THUMB_URL, IMAGE_SHA256, IMAGE_CELL, STATUS, ERROR_MSG.

Правила:
— PRODUCT_ID: если на странице нет явного SKU/ID, используем sha256(PRODUCT_URL).
— PRICE_CURRENCY всегда «RUB» (если не найдено иного).
— GRAPES_JSON: массив строк (JSON), например ["Уни Блан","Коломбар"].
— IMAGE_CELL: формула вида =IMAGE(IMAGE_DIRECT_URL).

6) Алгоритм
1. Старт: проверить robots.txt вручную и лимиты. Инициализировать Playwright (Chromium) в HEADLESS режиме. Открыть CATEGORY_URL. Закрыть оверлей возраста (клик по тексту «Мне исполнилось 18 лет») либо установить cookie, если известно.
2. Листинг: собрать ссылки на карточки через product_link_selector. Сохранять href абсолютным URL.
3. Для каждой карточки:
   3.1. Открыть страницу. Подождать появления заголовка (title_selector).
   3.2. Спарсить: TITLE, SKU (регекс «Артикул: (.+)»), COUNTRY (ссылка около заголовка/хлебные крошки), VOLUME_L/ABV_PERCENT (по селекторам или LLM из «инфо‑плашки»), PRICE_VALUE/PRICE_CURRENCY, AVAILABILITY («в наличии» → true), BRAND/PRODUCER (блок «Производитель» и/или ссылочные элементы рядом с h1), AGE_YEARS (эвристика/LLM по заголовку или «Способ выдержки»), разделы (tasting_notes, gastronomy, grapes → список, maturation, awards, gift_packaging).
   3.3. Найти главное изображение: image_selector → первая подходящая <img>. Получить абсолютный URL (учесть data-src/srcset). При необходимости скачать и посчитать SHA‑256; если уже есть такой хэш — переиспользовать прежние ссылки FreeImage.host.
   3.4. Загрузить изображение на FreeImage.host (сначала передав исходный URL, при ошибке — бинарный файл) и получить прямую ссылку.
   3.5. Подготовить запись для Sheets. Upsert по PRODUCT_ID (или хэш URL). IMAGE_CELL = =IMAGE(IMAGE_DIRECT_URL). STATUS = new | updated.
4. Пагинация: находить ссылки на страницы (page_link_selector). Для текущей страницы определить «следующую» как ближайшее число > текущего; если нет — завершить.
5. Итог: вывести отчёт (pages, products_total, inserted, updated, errors).

7) ИИ‑часть и эвристики
— Цена: привести «10 667 руб.» → {PRICE_VALUE: 10667, PRICE_CURRENCY: "RUB"}.
— Объём: строки вида «0.5 л», «1 л», «1.5 л» → VOLUME_L: 0.5, 1.0, 1.5.
— Крепость: «40 %», «43 %» → ABV_PERCENT: 40, 43.
— Возраст: «5 YO», «5 лет», «1991 г.» → AGE_YEARS: 5 или null; винтаж‑год можно писать отдельным полем, если нужен.
— Разделы: распознать заголовки по точному тексту и взять следующий узел/блок до следующего заголовка; очищать HTML → в текст. «Сортовой состав» разбирать построчно в массив.
— Наличие: «Товар в наличии» → true; прочие варианты → false/unknown.

8) Обработка ошибок и устойчивость
— Ретраи (экспоненциальные) для сетевых/временных ошибок и 5xx.
— Таймауты на загрузку страницы/изображения.
— Случайный User‑Agent и задержки между карточками.
— Лимиты FreeImage.host: ретраи, паузы и информативные сообщения об ошибках при окончательной неудаче загрузки.
— Идемпотентность: при повторном запуске не создавать дубликаты.

9) Docker
Dockerfile (схема):
  FROM mcr.microsoft.com/playwright/python:v1.45.0-jammy
  COPY requirements.txt .
  RUN pip install -r requirements.txt
  COPY . /app
  ENV PYTHONUNBUFFERED=1
  WORKDIR /app
  ENTRYPOINT ["python","-m","app.main"]

docker-compose.yml:
  services:
    scraper:
      build: .
      env_file: .env
      volumes:
        - ./secrets:/secrets:ro
        - ./state:/app/state
      restart: unless-stopped

10) Приёмка
— Обойти все страницы категории (минимум 2+ страницы) и собрать ≥ 30 карточек.
— В Google Sheets появились строки с корректными полями, в колонке IMAGE_CELL видны картинки.
— Повторный запуск обновляет изменившиеся поля без дубликатов.
— Логи содержат сводку и не менее 90% карточек завершились без ошибок.
