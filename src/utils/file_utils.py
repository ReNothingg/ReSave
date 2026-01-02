import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def sanitize_filename(name):
    bad = '<>:"/\\|?*\n\r\t'
    return "".join("_" if c in bad else c for c in name).strip()[:200]


def cleanup_old_files(temp_dir):
    try:
        for file in Path(temp_dir).glob("*"):
            if file.is_file():
                try:
                    os.remove(file)
                except Exception as e:
                    logger.warning(f"Не удалось удалить файл {file}: {e}")
    except Exception as e:
        logger.error(f"Ошибка при очистке временных файлов: {e}")


def ensure_temp_dir(temp_dir):
    os.makedirs(temp_dir, exist_ok=True)
