<div align="center">

<img src="logo.png" alt="Логотип ReSave" width="180">

# ReSave

### Скачивайте видео, музыку и фото прямо в Telegram

Отправьте ссылку, выберите формат — всё остальное бот сделает сам.

<p>
  <a href="https://t.me/ReSafeBot"><img src="https://img.shields.io/badge/Telegram-Open_%40ReSafeBot-26A5E4?style=for-the-badge&logo=telegram&logoColor=white" alt="Открыть ReSave в Telegram"></a>
</p>

<p>
  <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/aiogram-3.31%2B-2CA5E0?style=flat-square" alt="aiogram 3.31+">
  <img src="https://img.shields.io/badge/yt--dlp-2026.8-FF0000?style=flat-square" alt="yt-dlp 2026.8">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-4CAF50?style=flat-square" alt="Apache License 2.0"></a>
</p>

[Открыть бота](https://t.me/ReSafeBot) · [Возможности](#возможности) · [Запуск](#запуск-своей-копии) · [Конфигурация](#конфигурация)

</div>

---

## Как пользоваться

1. Откройте [@ReSafeBot](https://t.me/ReSafeBot).
2. Отправьте ссылку на публикацию или видео.
3. Выберите качество, MP3, GIF, субтитры или превью.
4. Получите готовый файл в Telegram.

В группе достаточно отправить ссылку — бот автоматически поставит видео до 720p в очередь.

## Возможности

| | Что умеет ReSave |
|---|---|
| 🎬 | Скачивает видео с выбором доступного разрешения или максимального качества |
| 🎵 | Извлекает аудио в MP3 |
| ✨ | Создаёт GIF из роликов продолжительностью до 30 секунд |
| 🖼️ | Загружает превью и TikTok-публикации с фотографиями |
| 📝 | Отправляет обычные и автоматически созданные субтитры |
| 📚 | Добавляет плейлисты в очередь с настраиваемым лимитом |
| 👥 | Работает в личных чатах, группах и супергруппах |
| 📊 | Ведёт личную и общую статистику загрузок в SQLite |
| 📦 | Поддерживает файлы до 2 000 MB через локальный Telegram Bot API |

ReSave работает с **YouTube, TikTok, Instagram, X/Twitter, Facebook, Vimeo, Twitch, Reddit** и другими источниками, которые поддерживает `yt-dlp`. Доступность  сайта зависит от его текущих ограничений ну и для закрытых публикаций могут потребоваться куки.

## Команды

| Команда | Назначение |
|---|---|
| `/start` | Открыть главное меню |
| `/help` | Показать инструкцию |
| `/status` | Посмотреть активные загрузки |
| `/cancel` | Отменить свои загрузки в текущем чате |
| `/stats` | Открыть личную статистику |
| `/admin` | Открыть панель администратора |
| `/broadcast` | Создать рассылку пользователям |
| `/stats_global` | Посмотреть общую статистику |

Административные команды доступны только пользователям из `ADMIN_IDS`.

## Установка

```bash
git clone https://github.com/ReNothingg/ReSave.git
cd ReSave
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

## Конфигурация

Все настройки задаются через `.env`. Готовый шаблон находится в [`.env.example`](.env.example).

| Переменная | По умолчанию | Назначение |
|---|---:|---|
| `BOT_TOKEN` | — | обязательный токен Telegram-бота |
| `ADMIN_IDS` | — | Telegram ID администраторов через запятую |
| `TEMP_DIR` | `temp_downloads` | директория временных загрузок |
| `STATS_DB_PATH` | `database.db` | путь к SQLite-базе статистики |
| `COOKIES_FILE` | `cookies.txt` | cookies для источников с ограниченным доступом |
| `LOG_FILE` | `bot.log` | файл логов |
| `LOG_LEVEL` | `INFO` | уровень логирования |
| `MAX_CONCURRENT_DOWNLOADS` | `2` | количество параллельных воркеров |
| `MAX_QUEUE_SIZE` | `100` | общий размер очереди |
| `MAX_TASKS_PER_USER` | `10` | лимит активных задач одного пользователя |
| `MAX_PLAYLIST_ITEMS` | `25` | максимальное число элементов плейлиста |
| `MAX_FILE_SIZE` | лимит Bot API | максимальный размер скачиваемого файла |
| `SEND_AS_DOC_LIMIT` | лимит Bot API | порог отправки медиа как документа |
| `SELECTION_TTL_SECONDS` | `900` | срок действия кнопок выбора формата |
| `DOWNLOAD_TIMEOUT_SECONDS` | `1800` | общий дедлайн отдельного процесса |
| `DOWNLOAD_STALL_TIMEOUT_SECONDS` | `180` | допустимое время без прогресса |
| `DOWNLOAD_RATE_LIMIT_BYTES` | `0` | ограничение скорости; `0` — без ограничения |
| `PROGRESS_UPDATE_SECONDS` | `3` | частота обновления прогресса |
| `BOT_API_BASE_URL` | — | адрес локального Telegram Bot API |
| `BOT_API_IS_LOCAL` | `false` | включить локальный режим Bot API |
| `BOT_API_USE_FILE_URI` | `false` | передавать локальному API путь вместо файла |

Относительные пути вычисляются от корня проекта, поэтому бот можно запускать из любой рабочей директории.

## Файлы до 2 GB

Облачный Telegram Bot API ограничивает размер загружаемых ботом файлов. Для больших файлов ReSave умеет работать с локальным Bot API.

Добавьте в `.env`:

```env
BOT_API_BASE_URL=http://127.0.0.1:8081
BOT_API_IS_LOCAL=true
BOT_API_USE_FILE_URI=false

TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=your-api-hash

MAX_FILE_SIZE=2097152000
SEND_AS_DOC_LIMIT=2097152000
```

Запустите локальный API и самого бота:

```bash
docker compose up -d telegram-bot-api
python main.py
```

При использовании Docker оставьте `BOT_API_USE_FILE_URI=false`: контейнер не видит абсолютные пути хоста, поэтому файл должен передаваться multipart-запросом. Значение `true` подходит только тогда, когда бот и нативный `telegram-bot-api` используют общую файловую систему.


## Лицензия

Проект распространяется по лицензии [Apache License 2.0](LICENSE).

---

<div align="center">

**[@ReSafeBot](https://t.me/ReSafeBot)**

</div>
