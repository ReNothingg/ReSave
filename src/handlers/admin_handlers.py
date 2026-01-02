"""
- /admin - статистика всех пользователей, управление БД
- /broadcast - отправка уведомлений всем юзерам
- /stats_global - глобальная статистика бота
"""

import logging
import json
from telebot import types
from datetime import datetime
import config
from ..utils.markdown_escape import escape_markdown

logger = logging.getLogger(__name__)


def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


def register_admin_handlers(bot):
    from ..utils.ui_manager import get_ui_manager
    from ..core.user_stats import get_stats_manager

    ui_manager = get_ui_manager()
    stats_manager = get_stats_manager()


    @bot.message_handler(commands=['admin'])
    def admin_command(message):
        if not is_admin(message.chat.id):
            return

        all_stats = stats_manager.get_all_stats()

        admin_text = "🔐 *Панель администратора*\n\n"
        admin_text += f"👥 *Активных пользователей:* {len(all_stats)}\n"


        total_downloads = sum(s.downloads_count for s in all_stats.values())
        total_videos = sum(s.total_videos for s in all_stats.values())
        total_audios = sum(s.total_audios for s in all_stats.values())
        total_failed = sum(s.failed_downloads for s in all_stats.values())
        total_size = sum(s.total_size_mb for s in all_stats.values())

        admin_text += f"📥 *Всего загрузок:* {total_downloads}\n"
        admin_text += f"🎬 *Видео загружено:* {total_videos}\n"
        admin_text += f"🎵 *Аудио загружено:* {total_audios}\n"
        admin_text += f"❌ *Ошибок:* {total_failed}\n"
        admin_text += f"💾 *Общий размер:* {total_size:.1f} MB\n\n"

        admin_text += "Выберите действие:"

        keyboard = types.InlineKeyboardMarkup()
        keyboard.row(
            types.InlineKeyboardButton("📊 Глобальная статистика", callback_data="admin_stats_global"),
            types.InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast")
        )
        keyboard.row(
            types.InlineKeyboardButton("👥 Список пользователей", callback_data="admin_user_list"),
            types.InlineKeyboardButton("🗑️ Очистить БД", callback_data="admin_clear_db")
        )

        bot.reply_to(message, admin_text, parse_mode='Markdown', reply_markup=keyboard)


    @bot.message_handler(commands=['broadcast'])
    def broadcast_command(message):
        """Команда для отправки уведомлений всем пользователям"""
        if not is_admin(message.chat.id):
            return

        sent_msg = bot.reply_to(
            message,
            "📢 *Режим рассылки активирован*\n\n"
            "Отправьте сообщение, которое должно быть отправлено всем пользователям:\n"
            "_(Текст, фото, видео - всё поддерживается)_",
            parse_mode='Markdown'
        )

        bot.register_next_step_handler(sent_msg, lambda msg: process_broadcast(msg, bot, stats_manager))

    def process_broadcast(message, bot, stats_manager):
        try:
            if not is_admin(message.chat.id):
                return

            all_stats = stats_manager.get_all_stats()
            user_ids = list(all_stats.keys())

            if not user_ids:
                bot.reply_to(message, "⚠️ Нет пользователей для рассылки!")
                return


            confirm_text = (
                f"📢 *Подтверждение рассылки*\n\n"
                f"Сообщение будет отправлено *{len(user_ids)} пользователям*.\n\n"
                f"Продолжить?"
            )

            keyboard = types.InlineKeyboardMarkup()
            keyboard.row(
                types.InlineKeyboardButton("✅ Да, отправить", callback_data="broadcast_confirm"),
                types.InlineKeyboardButton("❌ Отменить", callback_data="broadcast_cancel")
            )


            if not hasattr(bot, 'broadcast_cache'):
                bot.broadcast_cache = {}

            broadcast_id = message.message_id
            bot.broadcast_cache[broadcast_id] = {
                'message': message,
                'user_ids': user_ids,
                'total': len(user_ids),
                'sent': 0,
                'failed': 0,
                'admin_id': message.chat.id
            }

            confirm_msg = bot.reply_to(message, confirm_text, parse_mode='Markdown', reply_markup=keyboard)
            bot.broadcast_cache[broadcast_id]['confirm_msg_id'] = confirm_msg.message_id

        except Exception as e:
            logger.error(f"Ошибка в process_broadcast: {e}")
            bot.reply_to(message, f"❌ Ошибка: {str(e)}")


    @bot.message_handler(commands=['stats_global'])
    def stats_global_command(message):
        if not is_admin(message.chat.id):
            return

        all_stats = stats_manager.get_all_stats()


        total_users = len(all_stats)
        total_downloads = sum(s.downloads_count for s in all_stats.values())
        total_videos = sum(s.total_videos for s in all_stats.values())
        total_audios = sum(s.total_audios for s in all_stats.values())
        total_failed = sum(s.failed_downloads for s in all_stats.values())
        total_size = sum(s.total_size_mb for s in all_stats.values())


        avg_downloads = total_downloads / total_users if total_users > 0 else 0
        avg_size = total_size / total_downloads if total_downloads > 0 else 0


        active_users = sum(1 for s in all_stats.values() if s.downloads_count > 0)
        success_rate = ((total_videos + total_audios) / (total_downloads + total_failed) * 100
                       if (total_downloads + total_failed) > 0 else 0)

        stats_text = "📊 *Глобальная статистика ReSave*\n\n"

        stats_text += "━━━ 👥 *Пользователи* ━━━\n"
        stats_text += f"Всего пользователей: `{total_users}`\n"
        stats_text += f"Активных пользователей: `{active_users}`\n"
        stats_text += f"Процент активности: `{(active_users/total_users*100):.1f}%`\n\n"

        stats_text += "━━━ 📥 *Загрузки* ━━━\n"
        stats_text += f"Всего загрузок: `{total_downloads}`\n"
        stats_text += f"Видео загружено: `{total_videos}`\n"
        stats_text += f"Аудио загружено: `{total_audios}`\n"
        stats_text += f"Неудачных загрузок: `{total_failed}`\n"
        stats_text += f"Успешность: `{success_rate:.1f}%`\n\n"

        stats_text += "━━━ 💾 *Размеры* ━━━\n"
        stats_text += f"Общий размер: `{total_size:.1f} MB` (`{total_size/1024:.2f} GB`)\n"
        stats_text += f"Средний размер файла: `{avg_size:.2f} MB`\n"
        stats_text += f"Среднее загрузок на пользователя: `{avg_downloads:.1f}`\n\n"

        stats_text += "━━━ 📈 *Детальные метрики* ━━━\n"


        top_users = sorted(all_stats.items(), key=lambda x: x[1].downloads_count, reverse=True)[:5]
        stats_text += "Топ-5 активных пользователей:\n"
        for i, (user_id, stats) in enumerate(top_users, 1):
            stats_text += f"`{i}.` ID: `{user_id}` | Загрузок: `{stats.downloads_count}`\n"

        bot.reply_to(message, stats_text, parse_mode='Markdown')


    @bot.callback_query_handler(func=lambda call: call.data == "admin_stats_global")
    def callback_admin_stats_global(call):
        """Callback для глобальной статистики из админ-панели"""
        if not is_admin(call.from_user.id):
            return

        try:
            all_stats = stats_manager.get_all_stats()

            total_users = len(all_stats)
            total_downloads = sum(s.downloads_count for s in all_stats.values())
            total_videos = sum(s.total_videos for s in all_stats.values())
            total_audios = sum(s.total_audios for s in all_stats.values())
            total_failed = sum(s.failed_downloads for s in all_stats.values())
            total_size = sum(s.total_size_mb for s in all_stats.values())

            active_users = sum(1 for s in all_stats.values() if s.downloads_count > 0)
            success_rate = ((total_videos + total_audios) / (total_downloads + total_failed) * 100
                           if (total_downloads + total_failed) > 0 else 0)

            stats_text = "📊 *Глобальная статистика ReSave*\n\n"
            stats_text += f"👥 *Пользователей:* `{total_users}` (активных: `{active_users}`)\n"
            stats_text += f"📥 *Загрузок:* `{total_downloads}` (видео: `{total_videos}`, аудио: `{total_audios}`)\n"
            stats_text += f"❌ *Ошибок:* `{total_failed}`\n"
            stats_text += f"💾 *Размер:* `{total_size:.1f} MB`\n"
            stats_text += f"✅ *Успешность:* `{success_rate:.1f}%`"

            keyboard = types.InlineKeyboardMarkup()
            keyboard.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="admin_back"))

            bot.edit_message_text(
                stats_text,
                call.message.chat.id,
                call.message.message_id,
                parse_mode='Markdown',
                reply_markup=keyboard
            )
        except Exception as e:
            logger.error(f"Ошибка в callback_admin_stats_global: {e}")
            try:
                bot.send_message(call.message.chat.id, f"❌ Ошибка: {str(e)}")
            except:
                pass

    @bot.callback_query_handler(func=lambda call: call.data == "admin_broadcast")
    def callback_admin_broadcast(call):
        if not is_admin(call.from_user.id):
            return

        msg = bot.send_message(
            call.message.chat.id,
            "📢 *Режим рассылки активирован*\n\n"
            "Отправьте сообщение для рассылки:"
        )

        bot.register_next_step_handler(msg, process_broadcast, bot, stats_manager)

    @bot.callback_query_handler(func=lambda call: call.data == "admin_user_list")
    def callback_admin_user_list(call):
        if not is_admin(call.from_user.id):
            return

        all_stats = stats_manager.get_all_stats()
        user_list = sorted(all_stats.items(), key=lambda x: x[1].downloads_count, reverse=True)

        users_text = "👥 *Список пользователей*\n\n"

        if not user_list:
            users_text += "Нет пользователей в БД"
        else:
            for user_id, stats in user_list[:20]:
                users_text += (
                    f"ID: `{user_id}`\n"
                    f"├ Загрузок: {stats.downloads_count}\n"
                    f"├ Видео: {stats.total_videos} | Аудио: {stats.total_audios}\n"
                    f"└ Размер: {stats.total_size_mb:.1f} MB\n\n"
                )

            if len(user_list) > 20:
                users_text += f"... и ещё {len(user_list) - 20} пользователей"

        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="admin_back"))

        bot.edit_message_text(
            users_text,
            call.message.chat.id,
            call.message.message_id,
            parse_mode='Markdown',
            reply_markup=keyboard
        )

    @bot.callback_query_handler(func=lambda call: call.data == "admin_clear_db")
    def callback_admin_clear_db(call):
        if not is_admin(call.from_user.id):
            return

        confirm_text = "⚠️ *ВНИМАНИЕ!*\n\nВы уверены что хотите очистить всю статистику БД?\nЭто действие необратимо!"

        keyboard = types.InlineKeyboardMarkup()
        keyboard.row(
            types.InlineKeyboardButton("✅ Да, очистить", callback_data="admin_clear_confirm"),
            types.InlineKeyboardButton("❌ Отменить", callback_data="admin_back")
        )

        bot.edit_message_text(
            confirm_text,
            call.message.chat.id,
            call.message.message_id,
            parse_mode='Markdown',
            reply_markup=keyboard
        )

    @bot.callback_query_handler(func=lambda call: call.data == "admin_clear_confirm")
    def callback_admin_clear_confirm(call):
        if not is_admin(call.from_user.id):
            return

        stats_manager.stats.clear()
        stats_manager._save_stats()

        done_text = "✅ *База данных успешно очищена!*"

        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(types.InlineKeyboardButton("⬅️ Назад в админ-панель", callback_data="admin_back"))

        bot.edit_message_text(
            done_text,
            call.message.chat.id,
            call.message.message_id,
            parse_mode='Markdown',
            reply_markup=keyboard
        )

    @bot.callback_query_handler(func=lambda call: call.data == "broadcast_confirm")
    def callback_broadcast_confirm(call):
        if not is_admin(call.from_user.id):
            return

        try:
            if not hasattr(bot, 'broadcast_cache'):
                bot.send_message(call.message.chat.id, "❌ Ошибка: данные рассылки не найдены")
                return


            broadcast_data = None
            broadcast_id = None

            for bid, data in bot.broadcast_cache.items():
                if data.get('admin_id') == call.from_user.id:
                    broadcast_data = data
                    broadcast_id = bid
                    break

            if not broadcast_data:
                bot.send_message(call.message.chat.id, "❌ Ошибка: данные рассылки не найдены")
                return

            message_to_send = broadcast_data['message']
            user_ids = broadcast_data['user_ids']
            total_users = broadcast_data['total']


            bot.edit_message_text(
                f"📤 *Рассылка начата...*\n\nОтправлено: 0/{total_users}",
                call.message.chat.id,
                call.message.message_id,
                parse_mode='Markdown'
            )

            sent = 0
            failed = 0

            for user_id in user_ids:
                try:

                    if message_to_send.content_type == 'text':
                        bot.send_message(
                            user_id,
                            message_to_send.text,
                            parse_mode=message_to_send.parse_mode if hasattr(message_to_send, 'parse_mode') else None
                        )
                    elif message_to_send.content_type == 'photo':
                        bot.send_photo(user_id, message_to_send.photo[-1].file_id,
                                      caption=message_to_send.caption)
                    elif message_to_send.content_type == 'video':
                        bot.send_video(user_id, message_to_send.video.file_id,
                                      caption=message_to_send.caption)
                    elif message_to_send.content_type == 'document':
                        bot.send_document(user_id, message_to_send.document.file_id,
                                         caption=message_to_send.caption)
                    elif message_to_send.content_type == 'audio':
                        bot.send_audio(user_id, message_to_send.audio.file_id,
                                      caption=message_to_send.caption)

                    sent += 1
                except Exception as e:
                    failed += 1
                    logger.error(f"Ошибка отправки пользователю {user_id}: {e}")


                if sent % 10 == 0 or sent == total_users:
                    try:
                        bot.edit_message_text(
                            f"📤 *Рассылка в процессе...*\n\n"
                            f"Отправлено: {sent}/{total_users}\n"
                            f"Ошибок: {failed}",
                            call.message.chat.id,
                            call.message.message_id,
                            parse_mode='Markdown'
                        )
                    except:
                        pass


            final_text = (
                f"✅ *Рассылка завершена!*\n\n"
                f"✉️ Успешно отправлено: `{sent}/{total_users}`\n"
                f"❌ Ошибок: `{failed}`"
            )

            keyboard = types.InlineKeyboardMarkup()
            keyboard.add(types.InlineKeyboardButton("⬅️ Назад в админ-панель", callback_data="admin_back"))

            bot.edit_message_text(
                final_text,
                call.message.chat.id,
                call.message.message_id,
                parse_mode='Markdown',
                reply_markup=keyboard
            )


            if broadcast_id in bot.broadcast_cache:
                del bot.broadcast_cache[broadcast_id]

        except Exception as e:
            logger.error(f"Ошибка в callback_broadcast_confirm: {e}")
            bot.send_message(call.message.chat.id, f"❌ Ошибка: {str(e)}")

    @bot.callback_query_handler(func=lambda call: call.data == "broadcast_cancel")
    def callback_broadcast_cancel(call):
        if not is_admin(call.from_user.id):
            return
        try:

            if hasattr(bot, 'broadcast_cache'):
                to_delete = []
                for bid, data in bot.broadcast_cache.items():
                    if data.get('admin_id') == call.from_user.id:
                        to_delete.append(bid)
                for bid in to_delete:
                    del bot.broadcast_cache[bid]

            bot.edit_message_text(
                "❌ *Рассылка отменена*",
                call.message.chat.id,
                call.message.message_id,
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Ошибка в callback_broadcast_cancel: {e}")

    @bot.callback_query_handler(func=lambda call: call.data == "admin_back")
    def callback_admin_back(call):
        if not is_admin(call.from_user.id):
            return

        all_stats = stats_manager.get_all_stats()

        admin_text = "🔐 *Панель администратора*\n\n"
        admin_text += f"👥 *Активных пользователей:* {len(all_stats)}\n"

        total_downloads = sum(s.downloads_count for s in all_stats.values())
        total_videos = sum(s.total_videos for s in all_stats.values())
        total_audios = sum(s.total_audios for s in all_stats.values())
        total_failed = sum(s.failed_downloads for s in all_stats.values())
        total_size = sum(s.total_size_mb for s in all_stats.values())

        admin_text += f"📥 *Всего загрузок:* {total_downloads}\n"
        admin_text += f"🎬 *Видео загружено:* {total_videos}\n"
        admin_text += f"🎵 *Аудио загружено:* {total_audios}\n"
        admin_text += f"❌ *Ошибок:* {total_failed}\n"
        admin_text += f"💾 *Общий размер:* {total_size:.1f} MB\n\n"

        admin_text += "Выберите действие:"

        keyboard = types.InlineKeyboardMarkup()
        keyboard.row(
            types.InlineKeyboardButton("📊 Глобальная статистика", callback_data="admin_stats_global"),
            types.InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast")
        )
        keyboard.row(
            types.InlineKeyboardButton("👥 Список пользователей", callback_data="admin_user_list"),
            types.InlineKeyboardButton("🗑️ Очистить БД", callback_data="admin_clear_db")
        )

        bot.edit_message_text(
            admin_text,
            call.message.chat.id,
            call.message.message_id,
            parse_mode='Markdown',
            reply_markup=keyboard
        )
