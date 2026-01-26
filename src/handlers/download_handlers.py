import os
import logging
import threading
from telebot import types
import config
from ..utils.safe_formatter_new import get_safe_formatter
from ..utils.markdown_escape import escape_markdown

from ..core.models import InlineQuery

logger = logging.getLogger(__name__)


_download_manager = None


def set_download_manager(manager):
    global _download_manager
    _download_manager = manager


def get_download_manager():
    return _download_manager


def register_download_handlers(bot):
    from ..core.video_info import fetch_video_info
    from ..utils.file_utils import sanitize_filename
    from uuid import uuid4
    import hashlib
    import time
    import yt_dlp
    from pathlib import Path


    video_info_cache = {}
    inline_queries = {}
    inline_cache_lock = threading.Lock()

    @bot.inline_handler(func=lambda query: True)
    def inline_query_handler(inline_query):
        try:
            query_text = inline_query.query.strip()
            query_id = inline_query.id
            user_id = inline_query.from_user.id

            if not query_text:
                result = types.InlineQueryResultArticle(
                    id='help',
                    title='ReSave - Скачать видео',
                    description='Введите ссылку на видео после @ReSafeBot',
                    input_message_content=types.InputTextMessageContent(
                        'ReSave поможет скачать видео быстро и просто!\n\n'
                        '1. Введите @ReSafeBot\n'
                        '2. Вставьте ссылку на видео\n'
                        '3. Дождитесь загрузки (до 15 сек)\n'
                        '4. Отправьте видео в чат\n\n'
                        '@ReSafeBot'
                    ),
                    thumbnail_url='https://raw.githubusercontent.com/ReNothingg/ReNothingg/refs/heads/main/main.jpg'
                )
                bot.answer_inline_query(query_id, [result], cache_time=1)
                return

            if not query_text.startswith(('http://', 'https://')):
                bot.answer_inline_query(query_id, [], cache_time=1)
                return


            for q in inline_queries.values():
                if q.url == query_text and q.status == "ready" and q.file_id:
                    title = q.info.get("title", "Видео")
                    result = types.InlineQueryResultCachedVideo(
                        id=f'cached_{uuid4().hex[:8]}',
                        video_file_id=q.file_id,
                        title=title,
                        description='Готово к отправке (из кэша)',
                        caption=f"{title}\n\n@ReSafeBot"
                    )
                    bot.answer_inline_query(
                        query_id,
                        [result],
                        cache_time=300,
                        is_personal=True
                    )
                    return


            def quick_download():
                try:
                    ydl_opts = {
                        "quiet": True,
                        "no_warnings": True,
                        "extract_flat": False,
                        "skip_download": True,
                        "socket_timeout": 5,
                        "cookiefile": os.path.abspath("cookies.txt"),
                        "nocheckcertificate": True,
                    }

                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(query_text, download=False)

                    if not info:
                        return None, None

                    title = info.get("title", "video")
                    duration = info.get("duration", 0)

                    if duration > 600:
                        return "too_long", info

                    timestamp = int(time.time())
                    output_path = os.path.join(config.TEMP_DIR, f"inline_{user_id}_{timestamp}_{title}")

                    ydl_params = {
                        "format": "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best",
                        "outtmpl": f"{output_path}.%(ext)s",
                        "quiet": True,
                        "no_warnings": True,
                        "noplaylist": True,
                        "merge_output_format": "mp4",
                        "http_chunk_size": 10485760,
                        "cookiefile": os.path.abspath("cookies.txt"),
                        "socket_timeout": 10,
                        "retries": 1,
                        "fragment_retries": 1,
                        "nocheckcertificate": True,
                    }

                    if os.path.exists(os.path.abspath("ffmpeg")):
                        ydl_params["ffmpeg_location"] = os.path.abspath("ffmpeg")

                    download_complete = threading.Event()
                    download_result = {"file_path": None, "error": None}

                    def download_thread():
                        try:
                            with yt_dlp.YoutubeDL(ydl_params) as ydl:
                                ydl.download([query_text])

                            downloaded_files = list(Path(config.TEMP_DIR).glob(f"inline_{user_id}_{timestamp}_*"))
                            if downloaded_files:
                                download_result["file_path"] = str(downloaded_files[0])
                        except Exception as e:
                            download_result["error"] = str(e)
                        finally:
                            download_complete.set()

                    thread = threading.Thread(target=download_thread)
                    thread.daemon = True
                    thread.start()

                    if not download_complete.wait(timeout=15):
                        return "timeout", info

                    if download_result["error"]:
                        return None, None

                    file_path = download_result["file_path"]
                    if not file_path or not os.path.exists(file_path):
                        return None, None

                    file_size = os.path.getsize(file_path)
                    if file_size > 48 * 1024 * 1024:
                        os.remove(file_path)
                        return "too_large", info

                    try:
                        from ..utils.message_templates import MessageTemplate
                        with open(file_path, 'rb') as f:

                            caption = MessageTemplate.format_inline_caption(title, query_text)
                            message = bot.send_video(
                                user_id,
                                f,
                                caption=caption,
                                parse_mode='Markdown',
                                supports_streaming=True,
                                timeout=60
                            )
                            file_id = message.video.file_id

                            try:
                                bot.delete_message(user_id, message.message_id)
                            except:
                                pass

                        os.remove(file_path)

                        result_id = f'video_{uuid4().hex[:8]}'
                        with inline_cache_lock:
                            inline_queries[result_id] = InlineQuery(
                                query_id=query_id,
                                url=query_text,
                                user_id=user_id,
                                result_id=result_id,
                                info=info,
                                file_id=file_id,
                                timestamp=time.time(),
                                status="ready"
                            )

                        return file_id, info

                    except Exception as e:
                        logger.error(f"Ошибка при загрузке: {e}")
                        if os.path.exists(file_path):
                            os.remove(file_path)
                        return None, None

                except Exception as e:
                    logger.error(f"Ошибка quick_download: {e}")
                    return None, None

            file_id, info = quick_download()

            if file_id and file_id not in ["timeout", "too_long", "too_large"]:
                from ..utils.message_templates import MessageTemplate
                title = info.get("title", "Video")
                caption = MessageTemplate.format_inline_caption(title, query_text)
                result = types.InlineQueryResultCachedVideo(
                    id=f'video_{uuid4().hex[:8]}',
                    video_file_id=file_id,
                    title=title,
                    description='720p Ready to send',
                    caption=caption
                )
                bot.answer_inline_query(
                    query_id,
                    [result],
                    cache_time=300,
                    is_personal=True
                )
            elif file_id == "timeout":
                title = info.get("title", "Video") if info else "Video"
                result = types.InlineQueryResultArticle(
                    id=f'pending_{uuid4().hex[:8]}',
                    title='Loading...',
                    description=f'{title} - click to open bot',
                    input_message_content=types.InputTextMessageContent(
                        f'Video "{title}" is loading.\n\n'
                        f'Open @ReSafeBot and send: {query_text}\n\n'
                        f'@ReSafeBot'
                    ),
                    thumbnail_url='https://raw.githubusercontent.com/ReNothingg/ReNothingg/refs/heads/main/main.jpg'
                )
                bot.answer_inline_query(
                    query_id,
                    [result],
                    cache_time=1,
                    is_personal=True,
                    switch_pm_text="Open ReSave",
                    switch_pm_parameter="start"
                )
            else:
                result = types.InlineQueryResultArticle(
                    id=f'error_{uuid4().hex[:8]}',
                    title='Error loading',
                    description='Check link or use bot directly',
                    input_message_content=types.InputTextMessageContent(
                        f'Failed to load video.\n\n'
                        f'Try sending link to @ReSafeBot:\n{query_text}\n\n'
                        f'@ReSafeBot'
                    ),
                    thumbnail_url='https://raw.githubusercontent.com/ReNothingg/ReNothingg/refs/heads/main/main.jpg'
                )
                bot.answer_inline_query(
                    query_id,
                    [result],
                    cache_time=1,
                    is_personal=True,
                    switch_pm_text="Open ReSave",
                    switch_pm_parameter="start"
                )

        except Exception as e:
            logger.exception(f"Critical inline error: {e}")
            bot.answer_inline_query(inline_query.id, [], cache_time=1)

    @bot.message_handler(func=lambda message: message.text and not message.text.strip().startswith('/'))
    def handle_url(message):
        from ..utils.url_validator import get_url_validator
        from ..utils.ui_manager import get_ui_manager
        from ..utils.error_handler import get_error_handler

        url_validator = get_url_validator()
        ui_manager = get_ui_manager()
        error_handler = get_error_handler()

        text = message.text.strip()


        is_valid, corrected_url, metadata = url_validator.validate(text)

        if not is_valid:
            if message.chat.type == 'private':

                suggestions = url_validator.suggest_fixes(text)

                if suggestions:
                    suggestion_text = "Может быть, вы имели в виду?\n\n"
                    for suggestion in suggestions:
                        suggestion_text += f"• {suggestion['reason']}\n  {suggestion['url']}\n\n"
                    bot.reply_to(message,
                        f"❌ Это не похоже на ссылку\n\n{suggestion_text}"
                        f"Пожалуйста, отправьте корректную ссылку или выберите вариант выше.")
                else:
                    bot.reply_to(message,
                        "❌ Не удаётся распознать ссылку\n\n"
                        "Убедитесь, что:\n"
                        "• Ссылка полная (начинается с http:// или https://)\n"
                        "• Видеоплатформа поддерживается\n"
                        "• Нет лишних пробелов")
            return

        url = corrected_url or text


        if metadata.get("fixed"):
            logger.info(f"Ссылка исправлена: {metadata.get('fix_type')}")
            status_msg = bot.reply_to(message,
                f"✅ Ссылка исправлена (добавлен https://)\n\n"
                f"🔍 Ищу информацию о видео... Подождите секунду! ⏳")


        if message.chat.type in ['group', 'supergroup']:
            logger.info(f"Получена ссылка в группе {message.chat.id}: {url}")
            thread = threading.Thread(target=handle_group_download, args=(url, message, bot))
            thread.start()
            return


        if metadata.get("fixed"):
            pass
        else:
            status_msg = bot.reply_to(message, "🔍 Ищу информацию о видео... Подождите секунду! ⏳")

        thread = threading.Thread(
            target=extract_video_info,
            args=(bot, message, url, status_msg, video_info_cache)
        )
        thread.start()

    @bot.callback_query_handler(func=lambda call: call.data.startswith('dl_'))
    def handle_download(call):
        parts = call.data.split('_')
        action = parts[1]
        msg_id = int(parts[-1])

        if msg_id not in video_info_cache:
            bot.answer_callback_query(call.id, "❌ Информация устарела. Отправьте ссылку заново! 🔄")
            return

        download_info = video_info_cache[msg_id]
        bot.answer_callback_query(call.id, "✅ Начинаю скачивание! Подождите... ⏳")
        bot.edit_message_text("📥 Добавляю в очередь...", call.message.chat.id, call.message.message_id)

        format_param = f"best[height<={int(parts[2])}]" if action == "res" else None


        url = download_info['url']
        action_type = action

        if "tiktok.com" in url and "/photo/" in url:
            action_type = "tiktok_photo"

        _download_manager.add_task(
            url=url,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            info=download_info['info'],
            action=action_type,
            format_param=format_param
        )
        if msg_id in video_info_cache:
            del video_info_cache[msg_id]

    @bot.callback_query_handler(func=lambda call: call.data == "cancel_all_downloads")
    def handle_cancel_all_downloads(call):
        with _download_manager.lock:
            user_tasks = {k: v for k, v in _download_manager.tasks.items()
                         if v.chat_id == call.message.chat.id and v.status in ["downloading", "pending"]}

        if not user_tasks:
            bot.answer_callback_query(call.id, "😌 Нет активных загрузок для отмены.")
            return

        cancelled_count = 0
        for task_id in user_tasks:
            if _download_manager.cancel_task(task_id):
                cancelled_count += 1

        bot.answer_callback_query(call.id, f"✅ Отменено {cancelled_count} загрузок!")
        bot.edit_message_text(
            f"✅ Отменено {cancelled_count} загрузок! Готов к новым задачам. 🚀",
            call.message.chat.id,
            call.message.message_id
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith('cancel_'))
    def handle_cancel(call):
        bot.answer_callback_query(call.id, "✅ Запрос отменён! 😊")
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            bot.edit_message_text("❌ Запрос отменён.", call.message.chat.id, call.message.message_id)


def handle_group_download(url, message, bot):
    """Обрабатывает ссылку из группы"""
    try:
        import yt_dlp

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "socket_timeout": 30,
            "cookiefile": os.path.abspath("cookies.txt"),
            "nocheckcertificate": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if not info:
            logger.warning(f"Не удалось получить информацию для {url} в группе {message.chat.id}")
            return

        logger.info(f"Начинаю автоматическую загрузку для {url} в группе {message.chat.id}")
        _download_manager.add_task(
            url=url,
            chat_id=message.chat.id,
            message_id=message.message_id,
            info=info,
            action="medium",
            reply_to_id=message.message_id,
            silent_mode=True
        )

    except Exception as e:
        logger.error(f"Ошибка при автоматической обработке ссылки из группы {message.chat.id}: {e}")


def extract_video_info(bot, message, url, status_msg, cache):
    """Получение информации о видео"""
    try:
        from ..core.video_info import fetch_video_info
        from ..utils.file_utils import sanitize_filename
        from ..core.tiktok_photo_handler import TikTokPhotoHandler


        if "tiktok.com" in url and "/photo/" in url:
            logger.info(f"Обнаружено TikTok фото, скачиваю сразу...")

            bot.edit_message_text(
                "🖼️ Обнаружено TikTok фото! Начинаю скачивание... ⏳",
                message.chat.id,
                status_msg.message_id
            )


            _download_manager.add_task(
                url=url,
                chat_id=message.chat.id,
                message_id=status_msg.message_id,
                info={'title': 'TikTok Photo'},
                action='tiktok_photo',
                reply_to_id=message.message_id,
                silent_mode=False
            )
            return

        info = fetch_video_info(url)
        if not info:
            bot.edit_message_text("❌ Не удалось получить информацию о видео.", message.chat.id, status_msg.message_id)
            return

        if info.get('_type') == 'playlist':

            bot.edit_message_text("🎶 Плейлисты находятся в разработке.", message.chat.id, status_msg.message_id)
            return

        formats = info.get("formats", [])
        title = info.get("title", "video")
        duration = info.get("duration")
        subtitles = info.get('subtitles', {})
        auto_captions = info.get('automatic_captions', {})
        has_subtitles = bool(subtitles or auto_captions)

        resolutions = {}
        for f in formats:
            height = f.get("height")
            if height and f.get("vcodec", "none") != "none":
                if height not in resolutions or (f.get("filesize") or 0) > (resolutions[height].get('filesize') or 0):
                    resolutions[height] = f

        cache[message.message_id] = {
            'url': url,
            'info': info,
            'resolutions': resolutions,
            'chat_id': message.chat.id,
            'has_subtitles': has_subtitles
        }

        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(types.InlineKeyboardButton("🎬 Лучшее качество (авто)", callback_data=f"dl_best_{message.message_id}"))
        markup.add(types.InlineKeyboardButton("📹 Среднее качество (720p)", callback_data=f"dl_medium_{message.message_id}"))
        markup.add(types.InlineKeyboardButton("📱 Низкое качество (480p)", callback_data=f"dl_low_{message.message_id}"))
        markup.add(types.InlineKeyboardButton("🎵 Только аудио (MP3)", callback_data=f"dl_audio_{message.message_id}"))

        if duration and duration <= 30:
            markup.add(types.InlineKeyboardButton("✨ Создать GIF", callback_data=f"dl_gif_{message.message_id}"))


        if has_subtitles:
            markup.add(types.InlineKeyboardButton("📝 Скачать субтитры (.srt)", callback_data=f"dl_subtitles_{message.message_id}"))

        if info.get("thumbnail"):
            markup.add(types.InlineKeyboardButton("🖼️ Скачать превью (без сжатия)", callback_data=f"dl_thumbnail_{message.message_id}"))

        sorted_resolutions = sorted(resolutions.keys(), reverse=True)
        resolution_buttons = []
        for height in sorted_resolutions[:3]:
            fmt_info = resolutions[height]
            size_mb_str = f" (~{fmt_info.get('filesize', 0) / (1024*1024):.1f}MB)" if fmt_info.get('filesize') else ""
            resolution_buttons.append(types.InlineKeyboardButton(f"🎥 {height}p{size_mb_str}", callback_data=f"dl_res_{height}_{message.message_id}"))

        if resolution_buttons:
            markup.row(*resolution_buttons)
        markup.add(types.InlineKeyboardButton("❌ Отмена", callback_data=f"cancel_{message.message_id}"))


        message_text = f"📹 {title} ✨\n\n"

        if info.get('uploader'):
            message_text += f"👤 {info.get('uploader', '')}\n"

        if duration:
            minutes, seconds = divmod(int(duration), 60)
            message_text += f"⏱️ {minutes:02d}:{seconds:02d}\n"

        message_text += "\nВыберите качество для скачивания: 🎛️\n"


        get_safe_formatter().safe_edit_message_text(
            bot,
            message_text,
            message.chat.id,
            status_msg.message_id,
            reply_markup=markup
        )

    except Exception as e:
        error_msg = str(e)
        if "This video is unavailable" in error_msg:
            error_text = "❌ Это видео недоступно."
        elif "Private video" in error_msg:
            error_text = "❌ Это приватное видео."
        else:
            error_text = f"❌ Ошибка: {error_msg}"
        bot.edit_message_text(error_text, message.chat.id, status_msg.message_id)
        logger.error(f"Ошибка при получении информации о видео {url}: {e}")
