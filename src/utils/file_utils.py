import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


def sanitize_filename(name):
    bad = '<>:"/\\|?*\n\r\t'
    return "".join("_" if c in bad else c for c in name).strip()


def cleanup_old_files(temp_dir):
    try:
        temp_path = Path(temp_dir)
        if not temp_path.exists():
            return

        for entry in temp_path.iterdir():
            try:
                if entry.is_dir() and not entry.is_symlink():
                    shutil.rmtree(entry)
                else:
                    entry.unlink()
            except Exception as exc:
                logger.warning("Не удалось очистить %s: %s", entry, exc)
    except Exception as exc:
        logger.error("Ошибка при очистке временных файлов: %s", exc)


def ensure_temp_dir(temp_dir):
    os.makedirs(temp_dir, exist_ok=True)
