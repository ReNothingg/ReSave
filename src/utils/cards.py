from __future__ import annotations

import time
from html import escape

from ..core.models import DownloadTask, TaskStatus
from ..core.user_stats import UserStats
from .presentation import (
    MessageContent,
    clip,
    format_duration,
    format_size,
    format_speed,
    panel,
    parse_iso,
    progress_bar,
    smooth_bar,
    tg_time,
)
from .theme import emoji, footer

LEVELS = (
    (0, "Новичок", "sparkles"),
    (10, "Любитель", "star"),
    (50, "Профи", "fire"),
    (200, "Мастер", "trophy"),
    (1000, "Легенда", "crown"),
)


def card(
    title: str,
    body: str,
    fallback: list[str],
    *,
    icon: str,
    lite: str | None = None,
) -> MessageContent:
    plain = panel(title, fallback, icon=icon)
    heading = f"<h3>{emoji(icon)} {escape(clip(title, 200))}</h3>"
    tail = f"<footer>{footer()}</footer>"
    return MessageContent(
        plain.html,
        f"{heading}{body}{tail}",
        f"{heading}{lite}{tail}" if lite is not None else None,
    )


def metrics_table(rows: list[tuple[str, str]]) -> str:
    if not rows:
        return ""
    return (
        "<table compact>"
        + "".join(
            f"<tr><td>{escape(label)}</td><td><b>{escape(value)}</b></td></tr>"
            for label, value in rows
        )
        + "</table>"
    )


def checklist(items: list[tuple[str, bool]]) -> str:
    return (
        "<ul>"
        + "".join(
            f'<li><input type="checkbox"{" checked" if done else ""}>{escape(text)}</li>'
            for text, done in items
        )
        + "</ul>"
    )


def level_for(count: int) -> tuple[int, str, str, int | None]:
    index = max(i for i, (threshold, _, _) in enumerate(LEVELS) if count >= threshold)
    threshold, name, icon = LEVELS[index]
    upcoming = LEVELS[index + 1][0] if index + 1 < len(LEVELS) else None
    return threshold, name, icon, upcoming


def help_card(*, upload_limit: int, playlist_limit: int, user_limit: int) -> MessageContent:
    steps = [
        "Пришлите ссылку на видео, пост или трек.",
        "Посмотрите превью и выберите качество, MP3 или другой формат.",
        "Получите файл прямо в этот чат.",
    ]
    formats = [
        ("video", "Видео", "Любое доступное разрешение"),
        ("audio", "MP3", "Только звук из ролика"),
        ("camera", "GIF", "Для роликов до 30 секунд"),
        ("subtitle", "Субтитры", "Обычные и автоматические"),
        ("picture", "Фото", "Альбомы TikTok и обложки"),
    ]
    limits = [
        f"Размер файла — до {upload_limit / 1048576:g} МБ.",
        f"Одновременно — до {user_limit} задач, из плейлиста — до {playlist_limit} видео.",
        "В группах видео скачивается автоматически, примерно в 720p.",
        "Закрытые, удалённые и платные публикации недоступны.",
    ]
    faq = [
        (
            "Файл слишком большой",
            "Выберите качество пониже или MP3. Если нужное качество не влезает, я сам попробую ниже.",
        ),
        (
            "Ссылка не открывается",
            "Пришлите ссылку на саму публикацию. Профили и главные страницы не подходят.",
        ),
        (
            "Можно отменить загрузку?",
            "Да, командой /cancel — пока файл не начал отправляться в Telegram.",
        ),
    ]
    body = "<ol>" + "".join(f"<li>{escape(step)}</li>" for step in steps) + "</ol>"
    body += f"<h4>{emoji('sparkles')} Форматы</h4><table compact>"
    body += "".join(
        f"<tr><td>{emoji(icon)} <b>{label}</b></td><td>{detail}</td></tr>"
        for icon, label, detail in formats
    )
    body += "</table>"
    body += f"<h4>{emoji('idea')} Частые вопросы</h4>"
    body += "".join(
        f"<details><summary>{escape(question)}</summary><p>{escape(answer)}</p></details>"
        for question, answer in faq
    )
    body += "<details><summary>Лимиты</summary><ul>"
    body += "".join(f"<li>{escape(line)}</li>" for line in limits) + "</ul></details>"
    body += (
        "<p><code>/status</code> — мои загрузки · <code>/stats</code> — статистика · "
        "<code>/cancel</code> — отмена</p>"
    )
    fallback = steps + [""] + [f"{label} — {detail}" for _, label, detail in formats]
    fallback += ["", *limits, "", "/status · /stats · /cancel"]
    return card("Как пользоваться", body, fallback, icon="help")


def stats_card(value: UserStats) -> MessageContent:
    count = value.downloads_count
    threshold, level, level_icon, upcoming = level_for(count)
    volume = (
        f"{value.total_size_mb / 1024:.2f} ГБ"
        if value.total_size_mb >= 1024
        else f"{value.total_size_mb:.1f} МБ"
    )
    body = (
        "<table compact><tr>"
        f"<td>{emoji('download')} Скачано<br><b>{count}</b></td>"
        f"<td>{emoji('chart')} Объём<br><b>{volume}</b></td>"
        f"<td>{emoji(level_icon)} Уровень<br><b>{level}</b></td>"
        "</tr></table>"
    )
    fallback = [f"Скачано: {count}", f"Объём: {volume}", f"Уровень: {level}"]
    if upcoming:
        share = (count - threshold) / (upcoming - threshold)
        bar = progress_bar(share, width=10)
        body += f"<p>До следующего уровня — {upcoming - count}<br><code>{bar}</code></p>"
        fallback.append(f"До следующего уровня: {upcoming - count}")
    if not count:
        body += (
            f"<blockquote>{emoji('wave')} Пока пусто. Пришлите первую ссылку — "
            "и здесь появится статистика.</blockquote>"
        )
        return card("Ваша статистика", body, fallback, icon="stats")
    categories = [
        ("video", "Видео", value.total_videos),
        ("audio", "Музыка", value.total_audios),
        ("picture", "Другое", value.total_other_downloads),
    ]
    body += "<table compact>" + "".join(
        f"<tr><td>{emoji(icon)} {label}</td><td><b>{amount}</b></td></tr>"
        for icon, label, amount in categories
    )
    body += "</table>"
    first = parse_iso(value.first_download_date)
    last = parse_iso(value.last_download_date)
    if first:
        body += f"<p>С нами с {tg_time(first, 'D')} · последняя загрузка {tg_time(last, 'r')}</p>"
    attempts = count + value.failed_downloads
    if attempts:
        body += (
            "<details><summary>Надёжность</summary>"
            f"<p>Успешных загрузок: <b>{count / attempts:.0%}</b> из {attempts} попыток.</p>"
            "</details>"
        )
    fallback += [f"{label}: {amount}" for _, label, amount in categories]
    return card("Ваша статистика", body, fallback, icon="stats")


def task_progress(task: DownloadTask) -> str:
    if task.status == TaskStatus.DOWNLOADING and task.progress > 0:
        return progress_bar(task.progress)
    return ""


STATUS_LABELS = {
    TaskStatus.PENDING: ("timer", "в очереди"),
    TaskStatus.DOWNLOADING: ("download", "скачиваю"),
    TaskStatus.PROCESSING: ("settings", "обрабатываю"),
    TaskStatus.UPLOADING: ("upload", "отправляю"),
}


def downloads_card(tasks: list[DownloadTask]) -> MessageContent:
    if not tasks:
        return card(
            "Загрузки",
            f"<blockquote>{emoji('check')} Очередь пуста.</blockquote>"
            "<p>Пришлите ссылку, чтобы скачать файл.</p>",
            ["Очередь пуста.", "Пришлите ссылку, чтобы скачать файл."],
            icon="downloads",
        )
    active = sum(task.status != TaskStatus.PENDING for task in tasks)
    queued = len(tasks) - active
    body = metrics_table([("В работе", str(active)), ("В очереди", str(queued))])
    fallback = [f"В работе: {active} · В очереди: {queued}", ""]
    for task in tasks[:10]:
        icon, label = STATUS_LABELS.get(task.status, ("downloads", task.phase))
        title = escape(clip(task.title, 80))
        progress = task_progress(task)
        body += f"<p>{emoji(icon)} <b>{title}</b><br><i>{label}</i>"
        if progress:
            body += f"<br><code>{progress}</code>"
        body += "</p>"
        fallback.extend([clip(task.title, 80), label, progress, ""])
    if len(tasks) > 10:
        body += f"<p>И ещё {len(tasks) - 10}…</p>"
        fallback.append(f"И ещё {len(tasks) - 10}")
    body += "<details><summary>Об отмене</summary><p>Отменить можно очередь и скачивание. Файл, который уже отправляется в Telegram, остановить нельзя.</p></details>"
    return card("Загрузки", body, fallback, icon="downloads")


STREAM_NAMES = {"audio": "звук", "video": "видео", "media": "файл"}


def _stream_label(task: DownloadTask) -> str:
    raw = task.stream or "media"
    kind, _, quality = raw.partition(" ")
    name = STREAM_NAMES.get(kind, kind)
    label = f"{name} {quality}".strip()
    if task.streams > 1:
        label += f" · {min(task.stream_index, task.streams)} из {task.streams}"
    return label


def _stages(task: DownloadTask, stage: int) -> list[tuple[str, bool]]:
    if stage == 0 and task.streams > 1:
        download = f"Скачиваю {_stream_label(task)}"
    else:
        download = "Скачиваю"
    if stage == 1:
        processing = task.phase
    else:
        processing = "Склеиваю и конвертирую" if task.streams > 1 else "Обрабатываю"
    names = [download, processing, "Отправляю в Telegram"]
    return [(name, index < stage) for index, name in enumerate(names)]


def _download_section(task: DownloadTask) -> tuple[str, list[str]]:
    if task.progress <= 0 and not task.downloaded_bytes:
        if task.oversized:
            tried = ", ".join(task.oversized[:4])
            text = f"{tried} — больше лимита, подбираю качество ниже…"
            return f"<p><i>{escape(text)}</i></p>", [text]
        return "<p><i>Подключаюсь к источнику…</i></p>", ["Подключаюсь к источнику…"]
    percent = f"{min(task.progress, 0.99):.0%}"
    body = f"<p><code>{smooth_bar(task.progress)}</code> <b>{percent}</b></p>"
    rows = []
    if task.total_bytes:
        rows.append(
            ("Скачано", f"{format_size(task.downloaded_bytes)} из {format_size(task.total_bytes)}")
        )
    elif task.downloaded_bytes:
        rows.append(("Скачано", format_size(task.downloaded_bytes)))
    if task.speed:
        rows.append(("Скорость", format_speed(task.speed)))
    if task.eta:
        rows.append(("Осталось", f"≈ {format_duration(task.eta)}"))
    if task.fragments and not task.total_bytes:
        rows.append(("Фрагменты", f"{task.fragments[0]} из {task.fragments[1]}"))
    body += metrics_table(rows)
    lines = [f"{percent}"] + [f"{label}: {value}" for label, value in rows]
    if task.oversized:
        tried = ", ".join(task.oversized[:4])
        body += f"<blockquote>{emoji('warning')} {escape(tried)} — больше лимита, качаю ниже</blockquote>"
        lines.append(f"{tried} — больше лимита, качаю ниже")
    return body, lines


def progress_card(task: DownloadTask) -> MessageContent:
    if task.status == TaskStatus.PENDING:
        place = f"{task.queue_position}-я" if task.queue_position else "скоро"
        body = (
            f"<blockquote>{emoji('timer')} Очередь: <b>{place}</b>. "
            "Начну, как только освободится место.</blockquote>"
        )
        return card(clip(task.title, 120), body, [f"Очередь: {place}"], icon="timer")
    stage = (
        2
        if task.status == TaskStatus.UPLOADING
        else (1 if task.status == TaskStatus.PROCESSING or task.phase == "Обработка" else 0)
    )
    body = checklist(_stages(task, stage))
    fallback = [task.phase]
    if stage == 0:
        section, lines = _download_section(task)
        body += section
        fallback += lines
    else:
        elapsed = format_duration(time.time() - task.phase_started_at)
        body += f"<p><code>{smooth_bar(1.0 if stage == 2 else 0.99)}</code></p>"
        body += f"<p><i>{escape(clip(task.phase, 120))}…</i> · прошло {elapsed}</p>"
        fallback.append(f"Прошло: {elapsed}")
    icons = {0: "download", 1: "settings", 2: "upload"}
    return card(clip(task.title, 120), body, fallback, icon=icons[stage])
