# Контент-пайплайн для winediscovery.ru

Сервис собирает карточки коньяков с сайта [winediscovery.ru](https://winediscovery.ru) и выгружает данные в Google Sheets, одновременно загружая изображения товаров на FreeImage.host. Пайплайн полностью работает внутри Docker.

## Архитектура

- Playwright-краулер обходит список товаров, учитывая пагинацию и проверку возраста.
- Парсер извлекает данные карточки (заголовок, характеристики, разделы, изображение).
- Нормализатор приводит значения к стандартизованному виду, при необходимости обращается к LLM.
- MediaUploader загружает изображение на FreeImage.host (сначала по URL, при необходимости — бинарно) и кеширует SHA-256 вместе с прямой/viewer ссылкой.
- SheetsWriter выполняет upsert строк в Google Sheets, формирует формулу `=IMAGE()` и статусы `new`/`updated`.
- StateRepository (SQLite) хранит контрольные суммы карточек и изображений, обеспечивает идемпотентность.

Подробности см. в `docs/architecture.md`.

## Подготовка окружения

1. Скопируйте `.env.example` в `.env` и заполните значения:
   - `CATEGORY_URL` — стартовая категория.
   - `GSHEET_ID`, `GSHEET_TAB` — таблица и лист для выгрузки.
   - `GOOGLE_SA_JSON` — путь к JSON сервисного аккаунта (файл поместить в `./secrets/sa.json`).
   - `FREEIMAGE_API_KEY` + таймауты — параметры доступа к FreeImage.host (ключ выдаёт администратор аккаунта).
   - `OPENAI_API_KEY` (опционально) — для LLM-нормализации.

2. Создайте каталоги:
   ```bash
   mkdir -p secrets state
   ```
   Поместите сервисный JSON в `secrets/sa.json`.

### Настройка загрузки изображений

- Зарегистрируйтесь на [FreeImage.host](https://freeimage.host/) и получите API‑ключ в личном кабинете (`FREEIMAGE_API_KEY`).
- По умолчанию используется endpoint `https://freeimage.host/api/1/upload`; при других регионах скорректируйте `FREEIMAGE_API_ENDPOINT`.
- Таймауты `FREEIMAGE_CONNECT_TIMEOUT` и `FREEIMAGE_READ_TIMEOUT` задаются в секундах, `FREEIMAGE_MAX_RETRIES` — количество повторов при ошибках API.
- Пайплайн сначала пробует «сквозную» загрузку по исходному URL, затем (при отказе) качает файл и отправляет бинарно.
- Структура ответа, которую мы сохраняем:

```json
{
  "image": {
    "url": "https://iili.io/xxxxxx.jpg",           // IMAGE_DIRECT_URL
    "url_viewer": "https://freeimage.host/xxxxxx", // IMAGE_VIEWER_URL
    "thumb": { "url": "https://iili.io/xxxxxt.jpg" }
  },
  "success": { "code": 200 }
}
```

- В таблицу записываются `IMAGE_DIRECT_URL`, `IMAGE_VIEWER_URL`, `IMAGE_THUMB_URL`, `IMAGE_SHA256`, а формула `IMAGE_CELL` имеет вид `=IMAGE(IMAGE_DIRECT_URL)`.
- Кеш по SHA‑256 позволяет не загружать повторно одинаковые изображения; при повторном запуске сохраняется прежний direct URL.

## Сборка и запуск (Docker Desktop)

```bash
docker compose build
docker compose run --rm scraper
```

Первый запуск соберёт образ, установит зависимости и выполнит скрейп.

## Тесты

```bash
docker compose run --rm scraper pytest
```

Запускает модульные тесты парсера, нормализатора и слоя состояния в контейнере.

## Развёртывание на сервере

1. Установите Docker и Docker Compose.
2. Склонируйте репозиторий, заполните `.env`, копируйте секреты и API-ключ FreeImage.host.
3. Запустите:
   ```bash
   docker compose pull   # если используете опубликованный образ, иначе build
   docker compose up -d scraper
   ```
4. Логи смотреть через `docker compose logs -f scraper`.

## Ключевые пути

- `app/main.py` — точка входа пайплайна.
- `docs/requirements.md` — бизнес-требования.
- `docs/architecture.md` — архитектурные детали и этапы.
- `tests/` — автотесты.

## Ограничения

- При отсутствии Google Sheets/FreeImage/LLM секретов этапы выгрузки и нормализации работают в деградированном режиме без ошибок.
- Для долгого хранения состояния убедитесь, что каталог `state/` подключён как volume и входит в резервное копирование.
