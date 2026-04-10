"""Unit tests for fal helpers; optional live API checks when FAL_KEY is set."""

import os

import pytest

from director_api.providers import media_fal


def test_format_fal_result_message_combines_error_and_detail() -> None:
    assert (
        media_fal.format_fal_result_message({"error": "http_422", "detail": "bad field"})
        == "http_422: bad field"
    )


def test_format_fal_http_body_json_detail_string() -> None:
    body = '{"detail": "prompt is required"}'
    assert media_fal._format_fal_http_body(body) == "prompt is required"


def test_format_fal_http_body_nested_error_message() -> None:
    body = '{"error": {"message": "nope"}}'
    assert media_fal._format_fal_http_body(body) == "nope"


@pytest.mark.skipif(not (os.environ.get("FAL_KEY") or "").strip(), reason="FAL_KEY not set")
def test_smoke_image_live() -> None:
    from director_api.config import get_settings

    r = media_fal.smoke_image(get_settings())
    assert r.get("provider") == "fal"
    assert r.get("configured") is not False


@pytest.mark.skipif(not (os.environ.get("FAL_KEY") or "").strip(), reason="FAL_KEY not set")
def test_smoke_video_live_or_skipped() -> None:
    from director_api.config import get_settings

    r = media_fal.smoke_video(get_settings(), download=False)
    assert r.get("provider") == "fal"
    assert r.get("configured") is not False
    if r.get("skipped"):
        assert isinstance(r.get("reason"), str) and r["reason"]
    else:
        assert r.get("video_url") or r.get("error")
