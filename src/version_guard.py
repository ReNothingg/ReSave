import sys


def ensure_supported_python() -> None:
    if sys.version_info < (3, 11):
        raise SystemExit(
            "ReSave requires Python 3.11 or newer. "
            "Run it with python3.11 (or newer) or with .venv/bin/python."
        )
