# ReSave

<div align="center">
  <img src="logo.png" alt="ReSave logo" width="180" />

  <h3>Telegram-бот для скачивания видео и медиаконтента</h3>

  <p>
    <a href="https://t.me/ReSafeBot"><b>Открыть бота: @ReSafeBot</b></a>
  </p>

  <p>
    <img src="https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white" alt="Python 3.9+" />
    <img src="https://img.shields.io/badge/Telegram-Bot-26A5E4?logo=telegram&logoColor=white" alt="Telegram Bot" />
    <img src="https://img.shields.io/badge/Powered%20by-yt--dlp-ff4e45" alt="yt-dlp" />
  </p>
</div>

## Что умеет бот

- Скачивает видео по ссылке с популярных платформ через `yt-dlp`.
- Работает в `inline`-режиме (можно отправлять ссылку прямо из поля ввода).
- Поддерживает работу в группах.
- Грузит видео в фоне и отправляет результат по готовности.
- Использует очередь и ограничения на пользователя.
- Хранит статистику в SQLite и автоматически мигрирует старый `user_stats.json`.
- Поддерживает админ-команды и базовую статистику.

## Быстрый старт

### Установка зависимостей

```bash
pip install -r requirements.txt
```

### Настройка `.env`

Скопируйте `.env.example` в `.env` и заполните нужные значения:

```env
BOT_TOKEN=ваш_токен_бота
ADMIN_IDS=123456789
VIP_USERS=
```

Для отправки файлов больше стандартного лимита Bot API поднимите локальный
`telegram-bot-api` и укажите его адрес. `TELEGRAM_API_ID` и
`TELEGRAM_API_HASH` создаются на https://my.telegram.org/apps.

```env
BOT_API_BASE_URL=http://127.0.0.1:8081
BOT_API_IS_LOCAL=true
MAX_FILE_SIZE=2097152000
SEND_AS_DOC_LIMIT=2097152000
TELEGRAM_API_ID=ваш_api_id
TELEGRAM_API_HASH=ваш_api_hash
```

Запуск локального Bot API через Docker:

```bash
docker compose up -d telegram-bot-api
docker compose logs -f telegram-bot-api
```

После этого запускайте бота обычной командой:

```bash
python main.py
```

### Запуск без локального Bot API на alwaysdata

Docker не обязателен для работы бота. Но для отправки файлов больше облачного
лимита Telegram нужен локальный `telegram-bot-api`. Если на сервере нет
локального `telegram-bot-api`, оставьте `BOT_API_BASE_URL` пустым или удалите
эту переменную из `.env`.

Минимальный `.env` для Python-only запуска:

```env
BOT_TOKEN=ваш_токен_бота
ADMIN_IDS=123456789
VIP_USERS=
MAX_FILE_SIZE=52428800
SEND_AS_DOC_LIMIT=52428800
```

Команда для вкладки Service на alwaysdata:

```bash
bash -c 'export PATH=$HOME/.local/bin:$HOME/ffmpeg/ffmpeg-7.0.2-amd64-static:$PATH && cd /home/renothing/ReSave && python main.py'
```

В таком режиме бот работает через облачный Telegram Bot API. Это полностью
Python-запуск, но отправка файлов ограничена примерно 50 MB. Собственный
FastAPI/Flask API можно добавить для внешних запросов к вашему сервису, но он
не заменит локальный `telegram-bot-api` и не снимет лимит Telegram на загрузку
больших файлов.

### Локальный Bot API без Docker на alwaysdata

Чтобы отправлять файлы до 2000 MB без Docker, установите бинарник
`telegram-bot-api` в домашнюю директорию, например в
`$HOME/.local/bin/telegram-bot-api`, и запускайте его вместе с ботом одним
Service-процессом.

Собрать Linux-бинарник на Mac можно через Docker:

```bash
bash scripts/build_telegram_bot_api_linux_amd64.sh
```

Готовый файл появится здесь:

```bash
dist/telegram-bot-api-linux-amd64
```

Загрузите его на сервер:

```bash
scp dist/telegram-bot-api-linux-amd64 renothing@ssh-renothing.alwaysdata.net:/home/renothing/.local/bin/telegram-bot-api
ssh renothing@ssh-renothing.alwaysdata.net 'chmod +x /home/renothing/.local/bin/telegram-bot-api'
```

В `.env` нужны:

```env
BOT_TOKEN=ваш_токен_бота
ADMIN_IDS=123456789
VIP_USERS=
TELEGRAM_API_ID=ваш_api_id
TELEGRAM_API_HASH=ваш_api_hash
MAX_FILE_SIZE=2097152000
SEND_AS_DOC_LIMIT=2097152000
MAX_CONCURRENT_DOWNLOADS=1
MAX_DOWNLOADS_PER_USER=1
INLINE_DOWNLOAD_ENABLED=false
INLINE_DOWNLOAD_TIMEOUT=120
```

Команда для вкладки Service:

```bash
bash /home/renothing/ReSave/scripts/run_alwaysdata_local_bot_api.sh
```

По умолчанию скрипт ожидает бинарник здесь:

```bash
/home/renothing/.local/bin/telegram-bot-api
```

Если путь другой, задайте его перед запуском:

```bash
bash -c 'export BOT_API_BIN=$HOME/telegram-bot-api/bin/telegram-bot-api && bash /home/renothing/ReSave/scripts/run_alwaysdata_local_bot_api.sh'
```

Скрипт поднимает Bot API на `127.0.0.1:8081`, поэтому он доступен только боту
внутри этого же Service-процесса. Это безопаснее, чем открывать порт сервиса
наружу.

### Cookies для `yt-dlp` (опционально)

Если нужны авторизованные источники, добавьте cookies в `cookies.txt`.

### Запуск

```bash
python main.py
```

## Конфигурация

Основные параметры находятся в `config.py`:

| Параметр | Назначение |
|---|---|
| `BOT_TOKEN` | Токен Telegram-бота (читается из `.env`) |
| `ADMIN_IDS`, `VIP_USERS` | ID админов и VIP-пользователей |
| `TEMP_DIR` | Временная директория для загрузок |
| `STATS_DB_PATH` | SQLite-файл со статистикой |
| `MAX_CONCURRENT_DOWNLOADS` | Лимит одновременных загрузок |
| `MAX_DOWNLOADS_PER_USER` | Лимит активных загрузок на пользователя |
| `MAX_FILE_SIZE`, `SEND_AS_DOC_LIMIT` | Ограничения по размеру и порог отправки как документа |
| `BOT_API_BASE_URL`, `BOT_API_IS_LOCAL` | Адрес локального Bot API для отправки файлов до 2000 MB |
| `INLINE_DOWNLOAD_ENABLED`, `INLINE_DOWNLOAD_TIMEOUT` | Inline-загрузка и таймаут ожидания результата в секундах |
| `MAX_VIDEO_DURATION_FREE`, `MAX_VIDEO_DURATION_PREMIUM` | Лимит длительности для free/premium |
| `MAX_PLAYLIST_ITEMS_FREE`, `MAX_PLAYLIST_ITEMS_PREMIUM` | Лимит элементов плейлиста для free/premium |
| `LOG_LEVEL` | Уровень логирования (`INFO`, `DEBUG`, ...) |

## Структура проекта

- `main.py` - точка входа.
- `src/` - основная логика приложения.
- `tests/` - автоматические тесты.
- `temp_downloads/` - временные загруженные файлы.
- `cookies.txt` - cookies для `yt-dlp`.
- `database.db` - SQLite-база со статистикой.
- `bot.log` - локальные логи.

## Проверка проекта

```bash
python -m unittest discover -s tests -v
```

В репозитории также настроен GitHub Actions workflow `CI`, который компилирует исходники и запускает тесты на каждый push и pull request.

## Запуск как systemd-сервис (Linux)

```bash
sudo systemctl start resave
sudo systemctl status resave
```

Полезные команды:

```bash
sudo systemctl restart resave
journalctl -u resave -f
sudo systemctl enable resave
```

## Примечания

- Если `ffmpeg` не установлен, часть медиавозможностей может быть недоступна.
- Бот больше не устанавливает зависимости на лету: перед запуском нужно явно выполнить `pip install -r requirements.txt`.
- Обычные плейлисты ставятся в очередь автоматически в среднем качестве, если пользователь укладывается в лимиты.
