"""Tests for YouTube publish resolver and upload gating."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

from director_api.services.publish_youtube import (
    resolve_publish_to_youtube,
    should_youtube_upload,
    try_youtube_upload_after_export,
    youtube_upload_metadata,
)


def _project(**kwargs):
    return SimpleNamespace(
        id=uuid.uuid4(),
        title="Project Title",
        topic="Project topic",
        publish_pack_json=kwargs.get("publish_pack_json"),
        publish_to_youtube=kwargs.get("publish_to_youtube", False),
    )


def test_resolve_publish_to_youtube_pipeline_options_win():
    p = _project(publish_to_youtube=True)
    assert resolve_publish_to_youtube(p, {"publish_to_youtube": False}) is False
    assert resolve_publish_to_youtube(p, {"publish_to_youtube": True}) is True


def test_resolve_publish_to_youtube_falls_back_to_project():
    p = _project(publish_to_youtube=True)
    assert resolve_publish_to_youtube(p, None) is True
    assert resolve_publish_to_youtube(p, {}) is True


def test_should_youtube_upload_or_gate():
    settings = SimpleNamespace(youtube_auto_upload_after_export=False)
    assert should_youtube_upload(settings, publish_to_youtube=False) is False
    assert should_youtube_upload(settings, publish_to_youtube=True) is True

    settings_auto = SimpleNamespace(youtube_auto_upload_after_export=True)
    assert should_youtube_upload(settings_auto, publish_to_youtube=False) is True


def test_youtube_upload_metadata_prefers_publish_pack():
    p = _project(
        publish_pack_json={
            "youtube_title": "Pack Title",
            "youtube_description": "Pack description",
        },
    )
    title, desc = youtube_upload_metadata(p)
    assert title == "Pack Title"
    assert desc == "Pack description"


def test_youtube_upload_metadata_falls_back_to_project_fields():
    p = _project(publish_pack_json={})
    title, desc = youtube_upload_metadata(p)
    assert title == "Project Title"
    assert desc == "Project topic"


def test_try_youtube_upload_after_export_skipped_when_not_requested():
    db = MagicMock()
    settings = SimpleNamespace(youtube_auto_upload_after_export=False)
    p = _project(publish_to_youtube=False)
    out = try_youtube_upload_after_export(
        db,
        settings,
        tenant_id="t1",
        project=p,
        publish_to_youtube=False,
    )
    assert out == {"ok": False, "skipped_reason": "upload_not_requested"}
