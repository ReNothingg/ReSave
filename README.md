<div align="center">

<img src="logo.png" alt="ReSave" width="112">

# ReSave

**Видео, музыка и фото — по ссылке в Telegram.**

<p>
  <a href="https://t.me/ReSafeBot"><img src="https://img.shields.io/badge/Открыть_бота-@ReSafeBot-d60000?style=for-the-badge&logo=telegram&logoColor=white" alt="Открыть @ReSafeBot"></a>
</p>

<p>
  <img src="https://img.shields.io/badge/Python-3.11+-31363f?style=flat-square&logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/aiogram-3-31363f?style=flat-square" alt="aiogram 3">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache_2.0-31363f?style=flat-square" alt="Apache 2.0"></a>
</p>

[Возможности](#возможности) · [Запуск](#запуск) · [Настройки](#настройки)

</div>

---

### Ссылка → формат → файл

Пришлите ссылку на публикацию, выберите качество или формат и получите файл в том же чате.

## Возможности

| Формат | Возможности |
| :--- | :--- |
| **Видео** | Выбор разрешения или лучшего качества |
| **Аудио** | Извлечение MP3 |
| **Фото** | Обложки видео и фотоальбомы TikTok |
| **GIF** | Конвертация роликов до 30 секунд |
| **Субтитры** | Обычные и автоматически созданные |
| **Плейлисты** | Добавление видео в очередь с лимитом |

**YouTube · TikTok · Instagram · X / Twitter · Vimeo · Reddit**

И другие источники, поддерживаемые yt-dlp. Доступность зависит от сайта и прав доступа к публикации.

В личном чате доступен выбор формата. В группах бот скачивает автоматически, с ориентиром на 720p.

<details>
<summary><strong>Команды</strong></summary>

| Команда | Действие |
| :--- | :--- |
| `/start` | Главное меню |
| `/help` | Инструкция и лимиты |
| `/status` | Текущие загрузки |
| `/cancel` | Отмена проверки ссылки и загрузок до отправки |
| `/stats` | Личная статистика |
| `/admin` | Управление ботом |
| `/broadcast` | Рассылка с подтверждением и остановкой |
| `/stats_global` | Общая статистика |

Последние три команды доступны администраторам в личном чате.

</details>

## Запуск

**Нужны:** Python 3.11+, FFmpeg и ffprobe.

```bash
git clone https://github.com/ReNothingg/ReSave.git
cd ReSave

python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

Вставьте токен бота в `.env`:

```env
BOT_TOKEN=ваш_токен
```

Запустите:

```bash
.venv/bin/python main.py
```

## Настройки

Все параметры — в [`.env.example`](.env.example). Относительные пути считаются от корня проекта.

| Параметр | Назначение |
| :--- | :--- |
| `ADMIN_IDS` | ID администраторов через запятую |
| `MAX_CONCURRENT_DOWNLOADS` | Параллельные загрузки · по умолчанию **2** |
| `MAX_TASKS_PER_USER` | Задачи одного пользователя · **10** |
| `MAX_PLAYLIST_ITEMS` | Видео из одного плейлиста · **25** |
| `MAX_FILE_SIZE` | Максимальный размер файла в байтах |
| `COOKIES_FILE` | Файл cookies для публикаций с ограниченным доступом |

Лимит очереди пользователя действует и на плейлисты. Фактический размер файла ограничен выбранным Bot API.

<details>
<summary><strong>Большие файлы: локальный Bot API</strong></summary>

Для файлов больше облачного лимита используется локальный Telegram Bot API.

Добавьте в `.env`:

```env
BOT_API_BASE_URL=http://127.0.0.1:8081
BOT_API_IS_LOCAL=true
BOT_API_USE_FILE_URI=false

TELEGRAM_API_ID=ваш_api_id
TELEGRAM_API_HASH=ваш_api_hash

MAX_FILE_SIZE=2097152000
SEND_AS_DOC_LIMIT=2097152000
```

Запустите API через Docker, затем бота:

```bash
docker compose up -d telegram-bot-api
.venv/bin/python main.py
```

`BOT_API_USE_FILE_URI=true` нужен только при общей файловой системе бота и API. Для Docker оставьте `false`.

</details>

<details>
<summary><strong>Cookies и хранение данных</strong></summary>

- Cookies в формате Netscape — `cookies.txt`. Бот использует временные копии.
- Статистика — `database.db` (SQLite).
- Временные файлы — `temp_downloads/`.
- Очередь и меню хранятся в памяти: после перезапуска незавершённые запросы нужно отправить снова.

</details>

<details>
<summary><strong>Для разработки</strong></summary>

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m ruff check .
.venv/bin/python -m compileall -q main.py config.py src
```

| Путь | Ответственность |
| :--- | :--- |
| `main.py` | Запуск и сборка сервисов |
| `src/handlers/` | Команды, меню и административные действия |
| `src/core/` | Очередь, скачивание, обработка и отправка |
| `src/utils/` | Форматирование текста и работа с файлами |

Основные зависимости закреплены в `requirements.txt`. После их обновления проверяйте скачивание с нужных сайтов.

</details>

---

<div align="center">

[Открыть бота](https://t.me/ReSafeBot) · [Apache 2.0](LICENSE)

</div>
