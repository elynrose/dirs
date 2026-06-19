"""Background ComfyUI workflow tests (Settings page image/video smoke)."""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from director_api.providers.media_comfyui import generate_scene_image_comfyui, generate_scene_video_comfyui
from director_api.services.comfyui_workflow_storage import test_output_absolute_path, test_output_storage_key

ComfyuiTestMode = Literal["image", "video"]

_lock = threading.Lock()
_runs: dict[str, dict[str, Any]] = {}

_MAX_TEST_VIDEO_SEC = 4.0


def _runs_dir(storage_root: Path) -> Path:
    return (storage_root / "comfyui_test_runs").resolve()


def _persist_run(storage_root: Path, test_id: str, row: dict[str, Any]) -> None:
    d = _runs_dir(storage_root)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{test_id}.json").write_text(json.dumps(row, indent=2), encoding="utf-8")


def _load_run(storage_root: Path, test_id: str) -> dict[str, Any] | None:
    path = _runs_dir(storage_root) / f"{test_id}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _cap_test_video_duration(duration_sec: float | None, rt: Any) -> float:
    if duration_sec is None:
        duration_sec = float(getattr(rt, "scene_clip_duration_sec", 5) or 5)
    return max(1.0, min(_MAX_TEST_VIDEO_SEC, float(duration_sec)))


def start_comfyui_workflow_test(
    *,
    mode: ComfyuiTestMode,
    rt: Any,
    tenant_id: str,
    storage_root: Path,
    prompt: str,
    duration_sec: float | None = None,
) -> str:
    test_id = str(uuid.uuid4())
    row = {
        "test_id": test_id,
        "mode": mode,
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    if mode == "video":
        row["duration_sec"] = _cap_test_video_duration(duration_sec, rt)
    with _lock:
        _runs[test_id] = dict(row)
    _persist_run(storage_root, test_id, row)
    cap_dur = row.get("duration_sec") if mode == "video" else duration_sec
    threading.Thread(
        target=_run_test,
        args=(test_id, mode, rt, tenant_id, storage_root, prompt, cap_dur),
        daemon=True,
        name=f"comfyui-test-{mode}-{test_id[:8]}",
    ).start()
    return test_id


def get_comfyui_workflow_test(test_id: str, *, storage_root: Path | None = None) -> dict[str, Any] | None:
    with _lock:
        row = _runs.get(test_id)
        if row is not None:
            return dict(row)
    if storage_root is not None:
        loaded = _load_run(storage_root, test_id)
        if loaded is not None:
            with _lock:
                _runs[test_id] = loaded
            return dict(loaded)
    return None


def _finalize(test_id: str, storage_root: Path, out: dict[str, Any]) -> None:
    out["status"] = "succeeded" if out.get("ok") else "failed"
    out["finished_at"] = datetime.now(timezone.utc).isoformat()
    with _lock:
        _runs[test_id] = {**(_runs.get(test_id) or {}), **out}
    _persist_run(storage_root, test_id, _runs[test_id])


def _run_test(
    test_id: str,
    mode: ComfyuiTestMode,
    rt: Any,
    tenant_id: str,
    storage_root: Path,
    prompt: str,
    duration_sec: float | None,
) -> None:
    try:
        if mode == "image":
            res = generate_scene_image_comfyui(rt, prompt, frame_aspect_ratio="16:9")
            out: dict[str, Any] = {
                "test_id": test_id,
                "mode": mode,
                "ok": bool(res.get("ok")),
                "provider": res.get("provider"),
                "error": res.get("error"),
                "detail": (res.get("detail") or "")[:2000] or None,
                "model": res.get("model"),
            }
            if res.get("ok") and res.get("bytes"):
                dest = test_output_absolute_path(storage_root=storage_root, tenant_id=tenant_id, role="image")
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(res["bytes"])
                out["test_output_storage_key"] = test_output_storage_key(tenant_id, "image")
                out["content_type"] = res.get("content_type") or "image/png"
                out["bytes_written"] = len(res["bytes"])
        else:
            dur = _cap_test_video_duration(duration_sec, rt)
            res = generate_scene_video_comfyui(
                rt, prompt, scene_image_path=None, duration_sec=dur, frame_aspect_ratio="16:9"
            )
            out = {
                "test_id": test_id,
                "mode": mode,
                "ok": bool(res.get("ok")),
                "provider": res.get("provider"),
                "error": res.get("error"),
                "detail": (res.get("detail") or "")[:2000] or None,
                "model": res.get("model"),
                "duration_sec": dur,
            }
            if res.get("ok") and res.get("bytes"):
                dest = test_output_absolute_path(storage_root=storage_root, tenant_id=tenant_id, role="video")
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(res["bytes"])
                out["test_output_storage_key"] = test_output_storage_key(tenant_id, "video")
                out["content_type"] = res.get("content_type") or "video/mp4"
                out["bytes_written"] = len(res["bytes"])
        _finalize(test_id, storage_root, out)
    except Exception as e:  # noqa: BLE001
        _finalize(
            test_id,
            storage_root,
            {
                "test_id": test_id,
                "mode": mode,
                "ok": False,
                "error": "test_exception",
                "detail": str(e)[:2000],
            },
        )
