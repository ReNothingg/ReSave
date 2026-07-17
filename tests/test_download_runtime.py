import config

from src.core.download_handler import DownloadVariant, _base_ydl_params


def test_download_runtime_is_bounded_for_shared_hosting():
    variant = DownloadVariant(
        label="1080p",
        format="bv*[height=1080]+ba/b[height=1080]",
        postprocessors=(),
        output_template="/tmp/media.%(ext)s",
    )

    params = _base_ydl_params(variant)

    assert params["noprogress"] is True
    assert params["buffersize"] == 64 * 1024
    assert params["noresizebuffer"] is True
    if config.DOWNLOAD_RATE_LIMIT_BYTES > 0:
        assert params["ratelimit"] == config.DOWNLOAD_RATE_LIMIT_BYTES


def test_download_stall_timeout_is_shorter_than_total_timeout():
    assert 30 <= config.DOWNLOAD_STALL_TIMEOUT_SECONDS
    assert config.DOWNLOAD_STALL_TIMEOUT_SECONDS < config.DOWNLOAD_TIMEOUT_SECONDS
