"""Unit tests for Pexels client helpers (no network)."""

from director_api.providers.pexels_client import (
    pick_photo_download_url,
    pick_video_download_url,
    slim_photo_result,
    slim_video_result,
)


def test_pick_photo_download_prefers_original_jpg():
    photo = {
        "src": {
            "original": "https://images.pexels.com/photos/1/file.jpeg?h=999",
            "medium": "https://images.pexels.com/photos/1/med.jpg",
        }
    }
    url, suf = pick_photo_download_url(photo)
    assert "file.jpeg" in url
    assert suf == ".jpg"


def test_pick_video_download_prefers_sd_over_hd():
    video = {
        "video_files": [
            {"quality": "hd", "file_type": "video/mp4", "width": 1920, "link": "https://cdn/a.mp4"},
            {"quality": "sd", "file_type": "video/mp4", "width": 640, "link": "https://cdn/b.mp4"},
        ]
    }
    url, suf = pick_video_download_url(video)
    assert url.endswith("b.mp4") or "b.mp4" in url
    assert suf == ".mp4"


def test_slim_photo_has_thumb():
    p = {
        "id": 42,
        "width": 100,
        "height": 80,
        "photographer": "A",
        "photographer_url": "https://pexels.com/u",
        "url": "https://pexels.com/photo/x",
        "alt": "x",
        "src": {"medium": "https://i/thumb.jpg"},
    }
    s = slim_photo_result(p)
    assert s["pexels_id"] == 42
    assert s["thumb_url"] == "https://i/thumb.jpg"


def test_slim_video_uses_video_pictures():
    v = {
        "id": 7,
        "width": 1920,
        "height": 1080,
        "duration": 5,
        "url": "https://pexels.com/video/x",
        "user": {"name": "Pat", "url": "https://pexels.com/@pat"},
        "video_pictures": [{"picture": "https://thumb/still.jpg", "nr": 0}],
    }
    s = slim_video_result(v)
    assert s["pexels_id"] == 7
    assert s["thumb_url"] == "https://thumb/still.jpg"
    assert s["photographer"] == "Pat"
