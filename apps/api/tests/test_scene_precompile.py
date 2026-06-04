"""Tests for scene precompile helpers."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from director_api.services import scene_precompile as sp


def test_manifest_row_fingerprint_changes_with_duration(tmp_path: Path) -> None:
    m1 = {"asset_id": "a", "storage_url": "file:///x", "asset_type": "image", "duration_sec": 5.0}
    m2 = {**m1, "duration_sec": 10.0}
    assert sp.manifest_row_fingerprint(m1) != sp.manifest_row_fingerprint(m2)


def test_precompile_meta_roundtrip(tmp_path: Path) -> None:
    pid = uuid.uuid4()
    aid = uuid.uuid4()
    sp.write_precompile_meta(
        storage_root=tmp_path,
        project_id=pid,
        asset=type(
            "A",
            (),
            {
                "id": aid,
                "scene_id": uuid.uuid4(),
                "asset_type": "image",
            },
        )(),
        fingerprint="fp1",
        duration_sec=10.0,
        width=1280,
        height=720,
    )
    meta = sp.read_precompile_meta(sp.precompile_meta_path(tmp_path, pid, aid))
    assert meta is not None
    assert meta["fingerprint"] == "fp1"


def test_substitute_precompiled_segments(tmp_path: Path) -> None:
    pid = uuid.uuid4()
    aid = uuid.uuid4()
    mp4 = sp.precompile_mp4_path(tmp_path, pid, aid)
    mp4.parent.mkdir(parents=True, exist_ok=True)
    mp4.write_bytes(b"\x00" * 64)
    m = {
        "asset_id": str(aid),
        "storage_url": "file:///src.png",
        "asset_type": "image",
        "duration_sec": 10.0,
    }
    fp = sp.precompile_storage_fingerprint(m)
    sp.precompile_meta_path(tmp_path, pid, aid).write_text(
        json.dumps({"fingerprint": fp, "scene_id": str(uuid.uuid4())}),
        encoding="utf-8",
    )
    segs = [("image", Path("/unused.png"), 10.0)]
    out, n = sp.substitute_precompiled_clip_segments(
        segs,
        [m],
        storage_root=tmp_path,
        project_id=pid,
    )
    assert n == 1
    assert out[0][0] == "video"
    assert out[0][1] == mp4
