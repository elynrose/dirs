"""Unit tests for scene clip upload classification and validation."""

import pytest

from director_api.services.scene_clip_upload import (
    classify_from_filename_and_hint,
    normalized_extension,
    validate_explicit_clip_kind,
)


def test_validate_explicit_image_vs_mp4():
    with pytest.raises(ValueError, match="video container"):
        validate_explicit_clip_kind(kind_hint="image", filename="clip.mp4")


def test_validate_explicit_video_vs_png():
    with pytest.raises(ValueError, match="still image"):
        validate_explicit_clip_kind(kind_hint="video", filename="still.png")


def test_classify_auto_png():
    k, ext = classify_from_filename_and_hint("shot.png", kind_hint=None)
    assert k == "image"
    assert ext == ".png"


def test_classify_explicit_audio():
    k, ext = classify_from_filename_and_hint("narration.mp3", kind_hint="audio")
    assert k == "audio"
    assert ext == ".mp3"


def test_normalized_jpeg_to_jpg():
    assert normalized_extension("image", ".jpeg") == ".jpg"
