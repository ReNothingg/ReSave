import logging
import time
import random
from typing import Callable, Any, Optional, Type
from enum import Enum

logger = logging.getLogger(__name__)


class NonRetryableError(Exception):
    """Error wrapper for failures that must not be retried by outer operations."""


class RetryStrategy(Enum):
    EXPONENTIAL = "exponential"
    LINEAR = "linear"
    FIBONACCI = "fibonacci"
    FIXED = "fixed"
    RANDOM = "random"


class RetryConfig:
    def __init__(
        self,
        max_retries: int = 3,
        initial_delay: float = 1.0,
        max_delay: float = 60.0,
        strategy: RetryStrategy = RetryStrategy.EXPONENTIAL,
        exponential_base: float = 2.0,
        jitter: bool = True,
        retryable_exceptions: tuple = (Exception,),
    ):
        self.max_retries = max_retries
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.strategy = strategy
        self.exponential_base = exponential_base
        self.jitter = jitter
        self.retryable_exceptions = retryable_exceptions


class RetryManager:
    def __init__(self, config: RetryConfig = None):
        self.config = config or RetryConfig()
        self.attempt_counts = {}

    def retry_operation(
        self,
        operation: Callable,
        *args,
        operation_id: str = None,
        on_retry: Callable = None,
        on_failure: Callable = None,
        **kwargs
    ) -> Any:
        retry_count = 0
        last_exception = None

        while retry_count <= self.config.max_retries:
            try:
                if retry_count > 0:
                    delay = self._calculate_delay(retry_count)
                    logger.info(
                        f"Повторная попытка {retry_count}/{self.config.max_retries} "
                        f"для '{operation_id}'. Ожидание {delay:.2f}s..."
                    )

                    if on_retry:
                        on_retry(retry_count, delay)

                    time.sleep(delay)


                result = operation(*args, **kwargs)
                logger.debug(f"Операция успешна (попытка {retry_count + 1})")
                return result

            except Exception as e:
                last_exception = e
                logger.warning(
                    f"Ошибка при выполнении операции "
                    f"(попытка {retry_count + 1}/{self.config.max_retries + 1}): {type(e).__name__}: {str(e)}"
                )

                if not self._should_retry(e, retry_count):
                    logger.error(f"Операция не подлежит повтору или достигнут лимит: {str(e)}")
                    break

                retry_count += 1


        if on_failure:
            on_failure(retry_count, last_exception)

        raise last_exception or Exception("Операция не удалась после всех попыток")

    def _calculate_delay(self, attempt: int) -> float:
        if self.config.strategy == RetryStrategy.EXPONENTIAL:
            delay = self.config.initial_delay * (self.config.exponential_base ** (attempt - 1))

        elif self.config.strategy == RetryStrategy.LINEAR:
            delay = self.config.initial_delay * attempt

        elif self.config.strategy == RetryStrategy.FIBONACCI:
            delay = self.config.initial_delay * self._fibonacci(attempt)

        elif self.config.strategy == RetryStrategy.FIXED:
            delay = self.config.initial_delay

        elif self.config.strategy == RetryStrategy.RANDOM:
            delay = random.uniform(self.config.initial_delay, self.config.max_delay)

        else:
            delay = self.config.initial_delay


        delay = min(delay, self.config.max_delay)


        if self.config.jitter and self.config.strategy != RetryStrategy.RANDOM:
            jitter_factor = random.uniform(0.8, 1.2)
            delay *= jitter_factor

        return delay

    def _should_retry(self, exception: Exception, attempt_count: int) -> bool:
        if attempt_count >= self.config.max_retries:
            return False

        if isinstance(exception, NonRetryableError):
            logger.debug("NonRetryableError не повторяем: %s", exception)
            return False

        if isinstance(exception, FileNotFoundError):
            logger.debug("FileNotFoundError не повторяем: %s", exception)
            return False


        if not isinstance(exception, self.config.retryable_exceptions):
            return False


        error_str = str(exception).lower()

        if "загрузка отменена пользователем" in error_str or "cancelled" in error_str:
            logger.debug("Отмененную пользователем операцию не повторяем: %s", exception)
            return False


        permanent_errors = [
            "404",
            "403",
            "401",
            "private",
            "age-restricted",
            "unavailable",
            "unsupported url",
            "not a valid url",
            "no video formats found",
            "requested format is not available",
            "requested format not available",
            "download timed out after",
            "postprocessing: conversion failed",
            "conversion failed",
            "no such file",
            "файл не найден после скачивания",
            "invalid url",
            "bad request",
            "invalid file http url",
        ]

        for error_key in permanent_errors:
            if error_key in error_str:
                logger.debug(f"Постоянная ошибка, повтор не требуется: {error_key}")
                return False


        retry_keywords = [
            "timeout",
            "temporarily unavailable",
            "service unavailable",
            "rate limit",
            "throttle",
            "connection",
            "timed out",
            "try again",
            "503",
            "502",
            "500",
        ]

        for keyword in retry_keywords:
            if keyword in error_str:
                logger.debug(f"Повторяемая ошибка обнаружена: {keyword}")
                return True


        return True

    @staticmethod
    def _fibonacci(n: int) -> int:
        if n <= 1:
            return 1
        a, b = 1, 1
        for _ in range(n - 1):
            a, b = b, a + b
        return b

    def get_retry_info(self, operation_id: str) -> dict:
        return self.attempt_counts.get(operation_id, {"attempts": 0})

    def reset_retry_count(self, operation_id: str):
        if operation_id in self.attempt_counts:
            del self.attempt_counts[operation_id]


class SmartRetryManager(RetryManager):
    def __init__(self, config: RetryConfig = None):
        super().__init__(config)
        self.error_history = {}

    def retry_operation_smart(
        self,
        operation: Callable,
        *args,
        operation_id: str = None,
        on_retry: Callable = None,
        on_failure: Callable = None,
        **kwargs
    ) -> Any:
        retry_count = 0
        last_exception = None
        consecutive_same_errors = 0
        last_error_type = None

        while retry_count <= self.config.max_retries:
            try:
                if retry_count > 0:

                    delay = self._calculate_adaptive_delay(
                        retry_count, last_error_type, consecutive_same_errors
                    )
                    logger.info(
                        f"Попытка {retry_count}/{self.config.max_retries}. "
                        f"Ожидание {delay:.2f}s..."
                    )

                    if on_retry:
                        on_retry(retry_count, delay)

                    time.sleep(delay)


                result = operation(*args, **kwargs)
                logger.debug(f"Операция успешна (попытка {retry_count + 1})")


                if operation_id:
                    self.error_history[operation_id] = []

                return result

            except Exception as e:
                last_exception = e
                error_type = type(e).__name__


                if operation_id:
                    if operation_id not in self.error_history:
                        self.error_history[operation_id] = []
                    self.error_history[operation_id].append({
                        "attempt": retry_count + 1,
                        "error": error_type,
                        "message": str(e)
                    })


                if error_type == last_error_type:
                    consecutive_same_errors += 1
                else:
                    consecutive_same_errors = 1
                    last_error_type = error_type

                logger.warning(
                    f"Ошибка (попытка {retry_count + 1}/{self.config.max_retries + 1}): "
                    f"{error_type}: {str(e)}"
                )


                if not self._should_retry(e, retry_count):
                    logger.error(f"Не повторяем операцию: {str(e)}")
                    break


                if consecutive_same_errors >= 2:
                    logger.warning(
                        f"Одна и та же ошибка '{error_type}' повторилась {consecutive_same_errors} раз"
                    )

                retry_count += 1


        if on_failure:
            on_failure(retry_count, last_exception)

        raise last_exception or Exception("Операция не удалась")

    def _calculate_adaptive_delay(
        self,
        attempt: int,
        error_type: Optional[str] = None,
        consecutive_errors: int = 0
    ) -> float:

        base_delay = self._calculate_delay(attempt)


        if consecutive_errors > 1:
            base_delay *= (1.5 * consecutive_errors)


        if error_type and "rate" in error_type.lower():

            base_delay = max(base_delay, 10.0 * attempt)


        return min(base_delay, self.config.max_delay)



DOWNLOAD_RETRY_CONFIG = RetryConfig(
    max_retries=2,
    initial_delay=2.0,
    max_delay=20.0,
    strategy=RetryStrategy.EXPONENTIAL,
    exponential_base=1.5,
)

UPLOAD_RETRY_CONFIG = RetryConfig(
    max_retries=2,
    initial_delay=2.0,
    max_delay=20.0,
    strategy=RetryStrategy.EXPONENTIAL,
    exponential_base=2.0,
)

NETWORK_RETRY_CONFIG = RetryConfig(
    max_retries=5,
    initial_delay=1.0,
    max_delay=30.0,
    strategy=RetryStrategy.EXPONENTIAL,
    exponential_base=1.3,
)

SUBTITLE_RETRY_CONFIG = RetryConfig(
    max_retries=5,
    initial_delay=5.0,
    max_delay=300.0,
    strategy=RetryStrategy.EXPONENTIAL,
    exponential_base=2.0,
    jitter=True,
)


_retry_manager = None
_smart_retry_manager = None


def get_retry_manager(config: RetryConfig = None) -> RetryManager:
    global _retry_manager
    if config is not None:
        return RetryManager(config)

    if _retry_manager is None:
        _retry_manager = RetryManager(DOWNLOAD_RETRY_CONFIG)
    return _retry_manager


def get_smart_retry_manager(config: RetryConfig = None) -> SmartRetryManager:
    global _smart_retry_manager
    if config is not None:
        return SmartRetryManager(config)

    if _smart_retry_manager is None:
        _smart_retry_manager = SmartRetryManager(DOWNLOAD_RETRY_CONFIG)
    return _smart_retry_manager
