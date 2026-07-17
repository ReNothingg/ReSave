from pathlib import Path
from types import SimpleNamespace

from aiogram.types import FSInputFile

from src.utils.aiogram_bot_adapter import AiogramSyncBotAdapter


def _adapter(*, is_local: bool) -> AiogramSyncBotAdapter:
    bot = SimpleNamespace(
        session=SimpleNamespace(api=SimpleNamespace(is_local=is_local)),
    )
    return AiogramSyncBotAdapter(bot=bot, loop=None)


def test_local_api_uses_file_uri(tmp_path):
    media_path = tmp_path / "video with spaces.mp4"
    media_path.write_bytes(b"media")

    prepared = _adapter(is_local=True)._prepare_file(
        media_path,
        local_upload=True,
    )

    assert prepared == media_path.resolve().as_uri()
    assert prepared.startswith("file:///")


def test_cloud_api_uses_streaming_upload(tmp_path):
    media_path = tmp_path / "video.mp4"
    media_path.write_bytes(b"media")

    prepared = _adapter(is_local=False)._prepare_file(
        media_path,
        local_upload=True,
    )

    assert isinstance(prepared, FSInputFile)
    assert Path(prepared.path) == media_path


def test_cloud_fallback_never_receives_file_uri(tmp_path):
    media_path = tmp_path / "video.mp4"
    media_path.write_bytes(b"media")
    adapter = _adapter(is_local=True)

    prepared = adapter._prepare_file(media_path)

    assert isinstance(prepared, FSInputFile)
