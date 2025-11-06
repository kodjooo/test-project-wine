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
    "url": "https://iili.io/xxxxxx.jpg",
    "url_viewer": "https://freeimage.host/xxxxxx",
    "thumb": { "url": "https://iili.io/xxxxxt.jpg" }
  },
  "success": { "code": 200 }
}
```

- В таблицу записывается только `IMAGE_DIRECT_URL`; столбец `IMAGE_CELL` содержит `=IMAGE(IMAGE_DIRECT_URL)` для предпросмотра.
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
### Развёртывание на удалённом сервере

1. **Подготовьте хост.**
   - Установите Docker и Docker Compose (или Docker Compose V2, входящий в Docker CLI).
   - Откройте исходящие HTTPS-подключения (для сайта-источника, FreeImage.host и Google APIs).
   - Создайте системного пользователя, от которого будет запускаться пайплайн; убедитесь, что у него есть права на Docker.

2. **Склонируйте репозиторий и подготовьте конфигурацию.**
   ```bash
   git clone git@github.com:kodjooo/test-project-wine.git
   cd test-project-wine
   cp .env.example .env
   ```
   Заполните `.env`:
   - `CATEGORY_URL` — актуальный URL категории.
   - `GSHEET_ID`, `GSHEET_TAB`, `GOOGLE_SA_JSON` — доступ к Google Sheets.
   - `FREEIMAGE_API_KEY` — ключ FreeImage.host; при необходимости измените таймауты/endpoint.
   - При использовании прокси заполните `USE_PROXY` и URL.

3. **Разместите секреты.**
   ```bash
   mkdir -p secrets state
   scp ./local/path/google-credentials.json user@server:/path/to/test-project-wine/secrets/sa.json
   ```
   (Имя файла в `.env` должно совпадать с фактическим.)

4. **Соберите и запустите контейнер.**
   ```bash
   docker compose build            # либо docker compose pull, если образ публикуется отдельно
   docker compose up -d scraper
   ```
   При первом запуске Playwright скачает браузеры; дождитесь завершения.

5. **Контроль и обслуживание.**
   - Просмотр логов: `docker compose logs -f scraper`.
   - Остановка пайплайна: `docker compose down`.
   - Плановые обновления: `git pull && docker compose build && docker compose up -d`.
   - Ротация state: каталог `state/` смонтирован как volume, включите его в резервное копирование.

6. **Мониторинг/трекинг ошибок.**
   - В Google Sheets колонка `STATUS` покажет `error` при неудачной загрузке FreeImage — смотрите `ERROR_MSG`.
   - В логах присутствуют счётчики `inserted/updated/skipped` и подробности по сетевым ошибкам.

7. **Безопасность.**
   - Убедитесь, что `.env` и `secrets/` недоступны извне (используйте `chmod 600` при необходимости).
   - Разделите доступ к FreeImage.host-ключу и Google Sheets-аккаунту между ответственными лицами.

## Ключевые пути

- `app/main.py` — точка входа пайплайна.
- `docs/requirements.md` — бизнес-требования.
- `docs/architecture.md` — архитектурные детали и этапы.
- `tests/` — автотесты.

## Ограничения

- При отсутствии Google Sheets/FreeImage/LLM секретов этапы выгрузки и нормализации работают в деградированном режиме без ошибок.
- Для долгого хранения состояния убедитесь, что каталог `state/` подключён как volume и входит в резервное копирование.
