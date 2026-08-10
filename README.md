# ReSave

Асинхронный Telegram-бот для скачивания видео, аудио и связанных медиа через `yt-dlp`.

## Возможности

- видео с выбором доступного разрешения;
- MP3, GIF до 30 секунд, субтитры и превью;
- TikTok photo posts через `gallery-dl`;
- автоматическая загрузка ссылок в группах (до 720p);
- ограниченная очередь, несколько воркеров, прогресс и отмена;
- плейлисты с настраиваемым лимитом элементов;
- локальный Telegram Bot API для файлов до 2 GB;
- SQLite-статистика и административная рассылка;
- автоматический fallback с видео/аудио на документ, если Telegram отвергает формат.

## Архитектура

```text
aiogram routers
    │
    ├── VideoInfoService ── yt-dlp metadata
    │
    └── DownloadManager ── bounded asyncio.Queue
            │
            └── MediaPipeline
                  ├── MediaDownloader ── yt-dlp in worker threads
                  ├── ffmpeg / ffprobe
                  ├── TelegramGateway ── retries + local/cloud Bot API
                  └── UserStatsManager ── SQLite (WAL)
```

Хендлеры отвечают только за Telegram-сценарии. Очередь, загрузка, конвертация, отправка и хранение статистики разделены. Блокирующие операции `yt-dlp` и `gallery-dl` не выполняются в event loop.

## Требования

- Python 3.11+;
- FFmpeg и ffprobe в `PATH`;
- для TikTok photo posts — установленный из `requirements.txt` `gallery-dl`;
- локальный `telegram-bot-api` — только если нужны файлы больше облачного лимита.

## Установка

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Минимальная конфигурация:

```env
BOT_TOKEN=123456:telegram-bot-token
ADMIN_IDS=123456789
```

Запуск:

```bash
.venv/bin/python main.py
```

В проекте используется lock-файл: второй экземпляр бота в той же директории не запустится.

## Конфигурация

| Переменная | Значение по умолчанию | Назначение |
|---|---:|---|
| `BOT_TOKEN` | — | обязательный токен бота |
| `ADMIN_IDS` | — | Telegram ID администраторов через запятую |
| `TEMP_DIR` | `temp_downloads` | рабочие директории загрузок |
| `STATS_DB_PATH` | `database.db` | SQLite-база статистики |
| `COOKIES_FILE` | `cookies.txt` | cookies для закрытых/ограниченных источников |
| `MAX_CONCURRENT_DOWNLOADS` | `2` | число воркеров очереди |
| `MAX_QUEUE_SIZE` | `100` | общий предел очереди |
| `MAX_TASKS_PER_USER` | `10` | предел активных задач пользователя |
| `MAX_PLAYLIST_ITEMS` | `25` | верхний предел плейлиста; дополнительно действует лимит задач пользователя |
| `MAX_FILE_SIZE` | лимит Bot API | технический предел скачиваемого файла |
| `SEND_AS_DOC_LIMIT` | лимит Bot API | после этого размера отправлять как документ |
| `DOWNLOAD_TIMEOUT_SECONDS` | `1800` | общий дедлайн загрузки по progress hook |
| `DOWNLOAD_STALL_TIMEOUT_SECONDS` | `180` | допустимое время без прогресса |
| `DOWNLOAD_RATE_LIMIT_BYTES` | `0` | ограничение скорости `yt-dlp`; `0` — без ограничения |
| `PROGRESS_UPDATE_SECONDS` | `3` | частота обновления статуса |
| `BOT_API_BASE_URL` | — | адрес локального Bot API |
| `BOT_API_IS_LOCAL` | `true` при заданном URL | включить локальный режим и лимит Bot API до 2 GB |
| `BOT_API_USE_FILE_URI` | `false` | передавать путь вместо multipart; только для общей файловой системы |
| `LOG_LEVEL` | `INFO` | уровень логов |
| `LOG_FILE` | `bot.log` | файл логов |

Относительные пути вычисляются от корня проекта, поэтому запуск из другой рабочей директории безопасен.

## Локальный Telegram Bot API

Для файлов больше облачного лимита:

```env
BOT_API_BASE_URL=http://127.0.0.1:8081
BOT_API_IS_LOCAL=true
BOT_API_USE_FILE_URI=false
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=your-api-hash
MAX_FILE_SIZE=2097152000
SEND_AS_DOC_LIMIT=2097152000
```

Запуск через Docker:

```bash
docker compose up -d telegram-bot-api
.venv/bin/python main.py
```

При транспортной ошибке локального API файлы до 50 MB автоматически повторно отправляются через облачный Bot API.

Для Docker оставьте `BOT_API_USE_FILE_URI=false`: контейнер Bot API не видит абсолютные пути хоста, поэтому бот передаёт файл multipart-запросом. Значение `true` подходит только нативному Bot API на том же сервере и с общей файловой системой; alwaysdata wrapper включает его автоматически.

Для alwaysdata без Docker можно собрать бинарник и использовать сервисный wrapper:

```bash
bash scripts/build_telegram_bot_api_linux_amd64.sh
bash scripts/run_alwaysdata_local_bot_api.sh
```

Путь к проекту, Python и бинарнику задаются переменными `APP_DIR`, `PYTHON_BIN` и `BOT_API_BIN`.

## Проверки

```bash
pip install -r requirements-dev.txt
ruff check .
ruff format --check .
pytest -q
```

Тесты не обращаются к Telegram или внешним видеосервисам: сетевые клиенты и `yt-dlp` заменяются тестовыми объектами.

## Команды бота

- `/start`, `/help` — справка;
- `/status` — активные задачи пользователя;
- `/cancel` — отмена своих задач в текущем чате;
- `/stats` — личная статистика;
- `/admin`, `/broadcast`, `/stats_global` — функции администратора.

## Безопасность и эксплуатация

- принимаются только HTTP(S)-ссылки без credentials;
- loopback, локальные IP и hostnames, которые DNS разрешает во внутреннюю сеть, отклоняются до `yt-dlp`;
- callback выбора формата привязан одновременно к пользователю и чату и истекает по TTL;
- HTML из заголовков и URL экранируется;
- очередь и число задач пользователя ограничены;
- временные файлы изолированы по UUID задачи и удаляются после завершения;
- cookies, `.env`, база, логи и временные файлы исключены из Git.

## Обновление yt-dlp

Видеосервисы регулярно меняют протоколы. Если источник внезапно перестал работать, сначала обновите зависимости:

```bash
pip install -U yt-dlp gallery-dl
```
