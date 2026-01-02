"""
Менеджер обработки ошибок с красивыми user-friendly сообщениями
"""
import logging
from enum import Enum
from typing import Tuple

logger = logging.getLogger(__name__)


class ErrorCategory(Enum):
    """Категории ошибок"""
    NETWORK = "network"
    FORMAT = "format"
    SECURITY = "security"
    FILE = "file"
    AVAILABILITY = "availability"
    QUOTA = "quota"
    UNKNOWN = "unknown"


class ErrorMessage:
    """Содержит информацию об ошибке"""

    def __init__(self, user_message: str, log_message: str = None, category: ErrorCategory = ErrorCategory.UNKNOWN):
        self.user_message = user_message  # Сообщение для пользователя
        self.log_message = log_message or user_message  # Сообщение для логов
        self.category = category


class ErrorHandler:
    """Обрабатывает ошибки и преобразует их в user-friendly сообщения"""

    def __init__(self):
        self.error_patterns = self._init_error_patterns()

    def _init_error_patterns(self) -> dict:
        """Инициализирует паттерны для определения типов ошибок"""
        return {
            # Сетевые ошибки
            "timeout": (
                "❌ Сетевая ошибка: истекло время ожидания\n\n"
                "Попробуйте:\n"
                "• Проверить интернет-соединение\n"
                "• Отправить ссылку заново\n"
                "• Попробовать позже\n\n"
                "@ReSafeBot",
                ErrorCategory.NETWORK
            ),
            "read timed out": (
                "❌ Сервер длительное время не отвечает\n\n"
                "Это может быть из-за:\n"
                "• Перегрузки сервера видеоплатформы\n"
                "• Проблем с интернетом\n\n"
                "Попробуйте повторить попытку через минуту.",
                ErrorCategory.NETWORK
            ),
            "connection refused": (
                "❌ Не удалось подключиться\n\n"
                "Сервер видеоплатформы недоступен.\n"
                "Попробуйте позже.",
                ErrorCategory.NETWORK
            ),
            "no route to host": (
                "❌ Нет соединения с сервером\n\n"
                "Проверьте интернет-соединение и повторите попытку.",
                ErrorCategory.NETWORK
            ),

            # Ошибки формата и доступности
            "unavailable": (
                "❌ Видео недоступно или удалено\n\n"
                "Возможные причины:\n"
                "• Видео было удалено\n"
                "• Канал закрыт\n"
                "• Вы заблокированы регионально\n\n"
                "Попробуйте другое видео.",
                ErrorCategory.AVAILABILITY
            ),
            "private": (
                "❌ Это приватное видео\n\n"
                "Вы не имеете доступа к этому видео.\n"
                "Видео может быть:\n"
                "• Установлено на приватное\n"
                "• Удалено\n"
                "• Ограничено по возрасту",
                ErrorCategory.SECURITY
            ),
            "geo-restricted": (
                "❌ Видео недоступно в вашем регионе\n\n"
                "Видео заблокировано в вашей стране.",
                ErrorCategory.SECURITY
            ),
            "age-restricted": (
                "❌ Видео ограничено по возрасту\n\n"
                "Требуется подтверждение возраста.\n"
                "К сожалению, мы не можем его обойти.",
                ErrorCategory.SECURITY
            ),

            # Ошибки квоты
            "throttled": (
                "⚠️ Превышен лимит запросов\n\n"
                "Видеосервис ограничил скорость запросов.\n"
                "Подождите несколько минут и попробуйте снова.",
                ErrorCategory.QUOTA
            ),
            "rate limit": (
                "⚠️ Слишком много запросов\n\n"
                "Подождите несколько минут и попробуйте снова.",
                ErrorCategory.QUOTA
            ),

            # Ошибки файлов
            "file not found": (
                "❌ Файл не найден\n\n"
                "Видеофайл не был найден на сервере.",
                ErrorCategory.FILE
            ),
            "disk quota": (
                "❌ Недостаточно места\n\n"
                "На сервере закончилось место для загрузки.\n"
                "Попробуйте позже.",
                ErrorCategory.QUOTA
            ),
            "no such file": (
                "❌ Ошибка при работе с файлом\n\n"
                "Попробуйте отправить ссылку заново.",
                ErrorCategory.FILE
            ),
        }

    def handle_error(self, error: Exception, error_type: str = None) -> ErrorMessage:
        """
        Обрабатывает ошибку и возвращает user-friendly сообщение

        Args:
            error: Исключение для обработки
            error_type: Опциональный тип ошибки для точного определения

        Returns:
            ErrorMessage с пользовательским и логируемым сообщениями
        """
        error_str = str(error).lower()
        log_message = f"Ошибка: {type(error).__name__}: {str(error)}"

        logger.warning(log_message)

        # Проверяем по паттернам
        for pattern, (user_msg, category) in self.error_patterns.items():
            if pattern in error_str:
                return ErrorMessage(user_msg, log_message, category)

        # Специальная обработка для конкретных ошибок
        if "403" in error_str:
            return ErrorMessage(
                "❌ Доступ запрещен\n\n"
                "Возможно, видео ограничено по регионам или доступу.",
                log_message,
                ErrorCategory.SECURITY
            )

        if "404" in error_str:
            return ErrorMessage(
                "❌ Видео не найдено\n\n"
                "Ссылка может быть неправильной или видео удалено.",
                log_message,
                ErrorCategory.AVAILABILITY
            )

        if "429" in error_str:
            return ErrorMessage(
                "⚠️ Слишком много запросов\n\n"
                "Подождите несколько минут и попробуйте снова.",
                log_message,
                ErrorCategory.QUOTA
            )

        # Проверяем на опасные слова для скрытия технических деталей
        if any(word in error_str for word in ["certificate", "ssl", "https", "verify"]):
            return ErrorMessage(
                "❌ Ошибка безопасности соединения\n\n"
                "Попробуйте отправить ссылку заново.",
                log_message,
                ErrorCategory.NETWORK
            )

        #默认ное сообщение об ошибке
        return ErrorMessage(
            "❌ Неизвестная ошибка\n\n"
            "Что-то пошло не так. Попробуйте:\n"
            "• Проверить ссылку\n"
            "• Отправить её заново\n"
            "• Попробовать позже",
            log_message,
            ErrorCategory.UNKNOWN
        )

    def get_generic_error_message(self, category: ErrorCategory = None) -> str:
        """Получает общее сообщение об ошибке для категории"""
        if category == ErrorCategory.NETWORK:
            return (
                "❌ Ошибка соединения\n\n"
                "Проверьте интернет и повторите попытку."
            )
        elif category == ErrorCategory.SECURITY:
            return (
                "❌ Ошибка безопасности\n\n"
                "Видео недоступно для загрузки."
            )
        elif category == ErrorCategory.QUOTA:
            return (
                "⚠️ Ограничение лимита\n\n"
                "Подождите несколько минут и повторите попытку."
            )
        else:
            return (
                "❌ Что-то пошло не так\n\n"
                "Попробуйте позже."
            )


# Глобальный обработчик ошибок
_error_handler = None


def get_error_handler() -> ErrorHandler:
    """Получает глобальный обработчик ошибок"""
    global _error_handler
    if _error_handler is None:
        _error_handler = ErrorHandler()
    return _error_handler
