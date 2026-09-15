from __future__ import annotations

from html import escape

from ..core.models import DownloadTask, TaskStatus
from ..core.user_stats import UserStats
from .presentation import MessageContent, clip, format_duration, panel, progress_bar
from .theme import emoji, footer


def card(title: str, body: str, fallback: list[str], *, icon: str) -> MessageContent:
    plain = panel(title, fallback, icon=icon)
    rich = (
        f"<h2>{emoji(icon)} {escape(clip(title, 200))}</h2>{body}<hr/><footer>{footer()}</footer>"
    )
    return MessageContent(plain.html, rich)


def help_card(*, upload_limit: int, playlist_limit: int, user_limit: int) -> MessageContent:
    steps = [
        "Пришлите ссылку на видео или публикацию.",
        "Выберите качество, MP3 или другой доступный формат.",
        "Получите файл в этом чате.",
    ]
    formats = [
        ("video", "Видео", "Выбор разрешения"),
        ("audio", "MP3", "Аудио из ролика"),
        ("video", "GIF", "Для видео до 30 секунд"),
        ("subtitle", "Субтитры", "Если доступны у источника"),
        ("downloads", "Фото", "Альбомы TikTok и обложки"),
    ]
    limits = [
        f"Файл — до {upload_limit / 1048576:g} МБ.",
        f"Ваш лимит задач: {user_limit}. Видео из плейлиста: до {playlist_limit}.",
        "В группах видео скачивается автоматически, с ориентиром на 720p.",
        "Закрытые или удалённые публикации могут быть недоступны.",
    ]
    body = "<ol>" + "".join(f"<li>{escape(step)}</li>" for step in steps) + "</ol>"
    body += "<h3>Выберите, что сохранить</h3><table>"
    body += (
        "".join(
            f"<tr><td>{emoji(icon)} <b>{label}</b></td><td>{detail}</td></tr>"
            for icon, label, detail in formats
        )
        + "</table>"
    )
    body += "<details><summary>Лимиты и работа в группах</summary><ul>"
    body += "".join(f"<li>{escape(line)}</li>" for line in limits) + "</ul></details>"
    body += "<p><code>/status</code> — загрузки<br><code>/cancel</code> — отмена до отправки</p>"
    fallback = steps + [""] + [f"{label} — {detail}" for _, label, detail in formats]
    fallback += ["", *limits, "", "/status — загрузки", "/cancel — отмена до отправки"]
    return card("Как пользоваться", body, fallback, icon="help")


def stats_card(value: UserStats) -> MessageContent:
    volume = f"{value.total_size_mb:.1f} МБ"
    categories = [
        ("video", "Видео", value.total_videos),
        ("audio", "Аудио", value.total_audios),
        ("downloads", "Другие файлы", value.total_other_downloads),
    ]
    body = (
        "<table><tr>"
        f"<td>Скачано<br><b>{value.downloads_count}</b></td>"
        f"<td>Общий объём<br><b>{volume}</b></td>"
        "</tr></table>"
    )
    if not value.downloads_count:
        body += "<blockquote>Пока нет скачанных файлов.<br>Пришлите первую ссылку.</blockquote>"
    else:
        body += (
            "<h3>По форматам</h3><table>"
            + "".join(
                f"<tr><td>{emoji(icon)} {label}</td><td><b>{count}</b></td></tr>"
                for icon, label, count in categories
            )
            + "</table>"
        )
    attempts = value.downloads_count + value.failed_downloads
    reliability = [f"Неудачных загрузок: {value.failed_downloads}"]
    if attempts:
        reliability.append(f"Успешных: {value.downloads_count / attempts:.0%}")
    body += "<details><summary>Все попытки</summary>"
    body += "".join(f"<p>{escape(line)}</p>" for line in reliability) + "</details>"
    fallback = [f"Скачано: {value.downloads_count}", f"Общий объём: {volume}"]
    fallback += [f"{label}: {count}" for _, label, count in categories] + reliability
    return card("Ваша статистика", body, fallback, icon="stats")


def task_progress(task: DownloadTask) -> str:
    if task.status == TaskStatus.DOWNLOADING and task.progress > 0:
        return progress_bar(task.progress)
    return ""


def downloads_card(tasks: list[DownloadTask]) -> MessageContent:
    if not tasks:
        return card(
            "Загрузки",
            "<blockquote>Сейчас очередь пуста.</blockquote><p>Пришлите ссылку, чтобы скачать файл.</p>",
            ["Сейчас очередь пуста.", "Пришлите ссылку, чтобы скачать файл."],
            icon="downloads",
        )
    active = sum(task.status != TaskStatus.PENDING for task in tasks)
    queued = len(tasks) - active
    body = f"<p><b>В работе: {active}</b> · В очереди: {queued}</p>"
    fallback = [f"В работе: {active} · В очереди: {queued}", ""]
    for index, task in enumerate(tasks[:10], 1):
        title = escape(clip(task.title, 100))
        phase = escape(clip(task.phase, 100))
        progress = task_progress(task)
        body += f"<h3>{index}. {title}</h3><p><mark>{phase}</mark></p>"
        if progress:
            body += f"<p><code>{progress}</code></p>"
        fallback.extend([clip(task.title, 100), task.phase, progress, ""])
    if len(tasks) > 10:
        body += f"<p>Ещё задач: {len(tasks) - 10}</p>"
        fallback.append(f"Ещё задач: {len(tasks) - 10}")
    body += "<details><summary>Об отмене</summary><p>Можно отменить очередь и скачивание. Отправку файла в Telegram остановить уже нельзя.</p></details>"
    return card("Загрузки", body, fallback, icon="downloads")


def progress_card(task: DownloadTask) -> MessageContent:
    stages = ["Скачивание", "Обработка", "Отправка"]
    current = (
        2
        if task.status == TaskStatus.UPLOADING
        else (1 if task.status == TaskStatus.PROCESSING or task.phase == "Обработка" else 0)
    )
    body = (
        "<p>"
        + " → ".join(
            f"<b>{label}</b>" if index == current else label for index, label in enumerate(stages)
        )
        + "</p>"
    )
    body += f"<blockquote>{escape(clip(task.phase, 120))}</blockquote>"
    fallback = [task.phase]
    progress = task_progress(task) if current == 0 else ""
    if progress:
        body += f"<p><code>{progress}</code></p>"
        fallback.append(progress)
    metrics = []
    if current == 0 and task.speed:
        metrics.append(("Скорость", clip(task.speed, 50)))
    if current == 0 and task.eta:
        metrics.append(("Осталось", format_duration(task.eta)))
    if metrics:
        body += (
            "<table><tr>"
            + "".join(f"<td>{label}<br><b>{escape(value)}</b></td>" for label, value in metrics)
            + "</tr></table>"
        )
        fallback.extend(f"{label}: {value}" for label, value in metrics)
    return card(task.title, body, fallback, icon="downloads")
