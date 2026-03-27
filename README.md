# ReSave

<div align="center">
  <img src="logo.png" alt="ReSave logo" width="180" />

  <h3>Telegram-бот для скачивания видео и медиаконтента</h3>

  <p>
    <a href="https://t.me/ReSafeBot"><b>Открыть бота: @ReSafeBot</b></a>
  </p>

  <p>
    <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white" alt="Python 3.10+" />
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
- Поддерживает админ-команды и базовую статистику.

## Быстрый старт

### Установка зависимостей

```bash
pip install -r requirements.txt
```

### Настройка `.env`

Создайте файл `.env` в корне проекта:

```env
BOT_TOKEN=ваш_токен_бота
```

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
| `TEMP_DIR` | Временная директория для загрузок |
| `MAX_CONCURRENT_DOWNLOADS` | Лимит одновременных загрузок |
| `MAX_DOWNLOADS_PER_USER` | Лимит активных загрузок на пользователя |
| `MAX_FILE_SIZE`, `MAX_VIDEO_DURATION`, `MAX_PLAYLIST_ITEMS` | Ограничения free/premium |
| `ADMIN_IDS`, `VIP_USERS` | ID админов и VIP-пользователей |

## Структура проекта

- `main.py` - точка входа.
- `src/` - основная логика приложения.
- `temp_downloads/` - временные загруженные файлы.
- `cookies.txt` - cookies для `yt-dlp`.
- `bot.log` - локальные логи.

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
- При первом запуске бот может доустановить недостающие Python-пакеты.