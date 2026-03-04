import shutil
import logging

logger = logging.getLogger(__name__)


def which(cmd: str):
    return shutil.which(cmd)


def ensure_ffmpeg(auto_download: bool = False) -> bool:
    # auto_download intentionally ignored: automatic installation is disabled.
    ffmpeg_path = which("ffmpeg")
    if ffmpeg_path:
        logger.info(f"FFmpeg detected: {ffmpeg_path}")
        return True

    logger.warning("FFmpeg was not found in PATH.")
    return False
