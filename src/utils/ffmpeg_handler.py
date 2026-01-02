"""
FFmpeg установка и управление
"""
import os
import sys
import shutil
import subprocess
import platform
import urllib.request
import zipfile
import tarfile
import io
import stat
import logging

logger = logging.getLogger(__name__)


def which(cmd):
    """Проверяет наличие команды в PATH"""
    return shutil.which(cmd)


def run_cmd(cmd, check=False):
    """Выполняет системную команду"""
    try:
        return subprocess.check_call(cmd, shell=isinstance(cmd, str))
    except Exception as e:
        logger.error(f"Ошибка выполнения команды: {e}")
        if check:
            raise
        return None


def detect_env():
    """Определяет операционную систему и окружение"""
    syspl = sys.platform
    if syspl.startswith("win"):
        return "windows"
    if syspl == "darwin":
        return "macos"
    if os.environ.get("PREFIX", "").startswith("/data/data/com.termux") or os.environ.get("ANDROID_ROOT"):
        return "termux"
    return "linux"


def try_package_install(ff="ffmpeg"):
    """Пытается установить FFmpeg используя системный пакетный менеджер"""
    env = detect_env()
    runs = []

    if env == "termux":
        runs.append("pkg install -y ffmpeg")

    if env == "linux":
        if which("apt"):
            runs.append("sudo apt update -y && sudo apt install -y ffmpeg")
        if which("dnf"):
            runs.append("sudo dnf install -y ffmpeg")
        if which("yum"):
            runs.append("sudo yum install -y ffmpeg")
        if which("pacman"):
            runs.append("sudo pacman -Sy --noconfirm ffmpeg")

    if env == "macos":
        if which("brew"):
            runs.append("brew install ffmpeg")

    if env == "windows":
        if which("choco"):
            runs.append("choco install -y ffmpeg")
        if which("winget"):
            runs.append("winget install -e --id Gyan.FFmpeg")

    for c in runs:
        try:
            run_cmd(c, check=True)
            if which("ffmpeg"):
                return True
        except Exception:
            continue

    return False


def download_binary(url, extract=False):
    """Загружает бинарный файл с URL"""
    try:
        with urllib.request.urlopen(url) as r:
            data = r.read()
            return data
    except Exception as e:
        logger.error(f"Ошибка загрузки бинарного файла: {e}")
        return None


def install_ffmpeg_manual():
    """Вручную скачивает и устанавливает FFmpeg"""
    env = detect_env()

    if env == "windows":
        urls = [
            "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
            "https://www.gyan.dev/ffmpeg/builds/ffmpeg-git-essentials.7z"
        ]
        for u in urls:
            d = download_binary(u)
            if d:
                try:
                    z = zipfile.ZipFile(io.BytesIO(d))
                    members = [m for m in z.namelist() if m.endswith("ffmpeg.exe")]
                    if not members:
                        continue
                    target_dir = os.path.join(os.path.expanduser("~"), ".local", "ffmpeg")
                    os.makedirs(target_dir, exist_ok=True)
                    for m in members:
                        out = os.path.join(target_dir, os.path.basename(m))
                        with z.open(m) as src, open(out, "wb") as dst:
                            dst.write(src.read())
                        os.chmod(out, os.stat(out).st_mode | stat.S_IEXEC)
                    os.environ["PATH"] = target_dir + os.pathsep + os.environ.get("PATH", "")
                    return which("ffmpeg")
                except Exception:
                    continue

    if env in ("linux", "termux", "macos"):
        urls = [
            "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz",
            "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-64bit-static.tar.xz"
        ]
        for u in urls:
            d = download_binary(u)
            if d:
                try:
                    f = io.BytesIO(d)
                    tf = tarfile.open(fileobj=f, mode="r:xz")
                    members = [m for m in tf.getmembers() if m.name.endswith("/ffmpeg")]
                    if not members:
                        members = [m for m in tf.getmembers() if m.name.endswith("ffmpeg")]
                    if not members:
                        continue
                    target_dir = os.path.join(os.path.expanduser("~"), ".local", "ffmpeg", "bin")
                    os.makedirs(target_dir, exist_ok=True)
                    for m in members:
                        fn = os.path.basename(m.name)
                        outpath = os.path.join(target_dir, fn)
                        with open(outpath, "wb") as out:
                            out.write(tf.extractfile(m).read())
                        os.chmod(outpath, os.stat(outpath).st_mode | stat.S_IEXEC)
                    os.environ["PATH"] = os.path.join(os.path.expanduser("~"), ".local", "ffmpeg", "bin") + os.pathsep + os.environ.get("PATH", "")
                    return which("ffmpeg")
                except Exception:
                    continue

    return None


def ensure_ffmpeg(auto_download=True):
    """Проверяет наличие FFmpeg и пытается его установить если нужно"""
    if which("ffmpeg"):
        return True

    if not auto_download:
        return False

    try:
        if try_package_install():
            return True
    except Exception:
        pass

    try:
        if install_ffmpeg_manual():
            return True
    except Exception:
        pass

    return False
