"""ComfyUI image and video over HTTP — OSS desktop and Comfy Cloud.

**OSS** (default): ``/prompt``, ``/history/{id}``, ``/view``, ``/upload/image`` at ``COMFYUI_BASE_URL``.
Optional ``COMFYUI_API_KEY`` → ``Authorization: Bearer …`` for gated proxies.
When ``COMFYUI_USE_WEBSOCKET`` is true (default), a background ``/ws`` connection uses the same
``client_id`` as ``/prompt`` to detect run completion and tighten ``/history`` polling (same pattern as
https://9elements.com/blog/hosting-a-comfyui-workflow-via-api/).

**Cloud** (``COMFYUI_API_FLAVOR=cloud``): Comfy Cloud uses ``/api/*`` routes and ``X-API-Key``; see
https://docs.comfy.org/development/cloud/api-reference — default base ``https://cloud.comfy.org`` when URL is empty.
"""

from __future__ import annotations

import copy
import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from director_api.config import Settings

log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[4]

_CLOUD_DEFAULT_BASE = "https://cloud.comfy.org"


def _normalize_base_url(url: str) -> str:
    return (url or "").strip().rstrip("/")


def _is_cloud(settings: Settings) -> bool:
    return (getattr(settings, "comfyui_api_flavor", "oss") or "oss").strip().lower() == "cloud"


def _effective_base_url(settings: Settings) -> str:
    raw = _normalize_base_url(settings.comfyui_base_url)
    if _is_cloud(settings) and not raw:
        return _CLOUD_DEFAULT_BASE
    return raw


def _comfyui_request_headers(settings: Settings) -> dict[str, str]:
    key = (settings.comfyui_api_key or "").strip()
    if _is_cloud(settings):
        if not key:
            return {}
        return {"X-API-Key": key}
    if key:
        return {"Authorization": f"Bearer {key}"}
    return {}


def _prompt_path(settings: Settings) -> str:
    return "/api/prompt" if _is_cloud(settings) else "/prompt"


def _view_path(settings: Settings) -> str:
    return "/api/view" if _is_cloud(settings) else "/view"


def _upload_image_path(settings: Settings) -> str:
    return "/api/upload/image" if _is_cloud(settings) else "/upload/image"


def _http_base_to_ws_base(base: str) -> str:
    """``http(s)://host:port`` → ``ws(s)://host:port`` for ComfyUI ``/ws``."""
    b = (base or "").strip().rstrip("/")
    if b.startswith("https://"):
        return "wss://" + b[len("https://") :]
    if b.startswith("http://"):
        return "ws://" + b[len("http://") :]
    return b


def _spawn_comfyui_ws_done_watcher(
    base: str,
    client_id: str,
    prompt_id: str,
    deadline: float,
) -> threading.Event | None:
    """Background thread: watch ``/ws`` until ComfyUI signals this ``prompt_id`` finished."""
    try:
        from websocket import WebSocket, WebSocketTimeoutException
    except ImportError:
        log.debug("comfyui_ws_unavailable", reason="websocket_client_not_installed")
        return None

    ev = threading.Event()

    def _run() -> None:
        ws: WebSocket | None = None
        try:
            remain_conn = min(15.0, max(2.0, deadline - time.monotonic()))
            if remain_conn <= 0:
                return
            ws = WebSocket()
            ws.connect(
                f"{_http_base_to_ws_base(base)}/ws?clientId={client_id}",
                timeout=remain_conn,
            )
            while time.monotonic() < deadline:
                rem = deadline - time.monotonic()
                if rem <= 0:
                    break
                ws.settimeout(min(4.0, rem))
                try:
                    raw = ws.recv()
                except WebSocketTimeoutException:
                    continue
                except Exception:
                    break
                if not isinstance(raw, str):
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(msg, dict):
                    continue
                if msg.get("type") == "progress":
                    data = msg.get("data")
                    if isinstance(data, dict) and "value" in data and "max" in data:
                        log.debug(
                            "comfyui_sampler_progress",
                            prompt_id=prompt_id,
                            step=data.get("value"),
                            max=data.get("max"),
                        )
                    continue
                if msg.get("type") != "executing":
                    continue
                data = msg.get("data")
                if not isinstance(data, dict):
                    continue
                if data.get("node") is not None:
                    continue
                if str(data.get("prompt_id") or "") != prompt_id:
                    continue
                ev.set()
                break
        except Exception as e:
            log.debug("comfyui_ws_watcher_failed", error=str(e)[:400])
        finally:
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass

    threading.Thread(target=_run, name="comfyui-ws", daemon=True).start()
    return ev


def _history_response_to_entry(hr: httpx.Response, prompt_id: str) -> dict[str, Any] | None:
    if hr.status_code != 200:
        return None
    try:
        hist = hr.json()
    except Exception:
        return None
    if not isinstance(hist, dict):
        return None
    if prompt_id in hist:
        e = hist[prompt_id]
        return e if isinstance(e, dict) else None
    if "outputs" in hist and isinstance(hist.get("outputs"), dict):
        return hist
    if len(hist) == 1:
        v = next(iter(hist.values()))
        return v if isinstance(v, dict) else None
    return None


def _wait_for_history_entry(
    http: httpx.Client,
    settings: Settings,
    base: str,
    prompt_id: str,
    timeout: float,
    poll: float,
    *,
    client_id: str | None = None,
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """Poll until workflow outputs are available. Returns (entry, error_code, detail)."""
    deadline = time.monotonic() + timeout
    cloud = _is_cloud(settings)
    last_hr: httpx.Response | None = None

    ws_done: threading.Event | None = None
    if (
        not cloud
        and (client_id or "").strip()
        and bool(getattr(settings, "comfyui_use_websocket", True))
    ):
        ws_done = _spawn_comfyui_ws_done_watcher(base, client_id.strip(), prompt_id, deadline)

    if cloud:
        while time.monotonic() < deadline:
            sr = http.get(f"{base}/api/job/{prompt_id}/status")
            if sr.status_code == 200:
                try:
                    data = sr.json()
                except Exception:
                    data = {}
                st = data.get("status") if isinstance(data, dict) else None
                if st == "completed":
                    hist_deadline = time.monotonic() + min(120.0, max(30.0, timeout * 0.25))
                    while time.monotonic() < hist_deadline:
                        last_hr = http.get(f"{base}/api/history_v2/{prompt_id}")
                        entry = _history_response_to_entry(last_hr, prompt_id)
                        if entry is not None:
                            return entry, None, None
                        time.sleep(min(poll, 2.0))
                    tail = (last_hr.text[:800] if last_hr is not None else "")
                    return None, "history_empty_after_complete", tail
                if st in ("failed", "cancelled"):
                    return None, f"cloud_job_{st}", str(data)[:2000]
            time.sleep(poll)
        return None, "timeout", f"Job did not complete within {timeout:.0f}s"

    while time.monotonic() < deadline:
        hr = http.get(f"{base}/history/{prompt_id}")
        if hr.status_code == 200:
            try:
                hist = hr.json()
            except Exception:
                hist = None
            if isinstance(hist, dict) and prompt_id in hist:
                entry = hist[prompt_id]
                if isinstance(entry, dict):
                    return entry, None, None
        sleep_for = poll
        if ws_done is not None and ws_done.is_set():
            sleep_for = min(poll, 0.25)
        time.sleep(sleep_for)
    return None, "timeout", f"No history for prompt_id after {timeout:.0f}s"


def _resolve_workflow_path(settings: Settings) -> Path:
    raw = (settings.comfyui_workflow_json_path or "").strip()
    if not raw:
        raise FileNotFoundError("comfyui_workflow_json_path is empty (set COMFYUI_WORKFLOW_JSON_PATH)")
    p = Path(raw)
    if p.is_file():
        return p.resolve()
    p2 = (_REPO_ROOT / raw).resolve()
    if p2.is_file():
        return p2
    raise FileNotFoundError(f"ComfyUI workflow JSON not found: {raw}")


def _clip_text_encode_node_ids(workflow: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for nid, node in workflow.items():
        if not isinstance(node, dict):
            continue
        ct = str(node.get("class_type") or "")
        if "CLIPTextEncode" not in ct:
            continue
        inputs = node.get("inputs")
        if isinstance(inputs, dict) and "text" in inputs:
            out.append(str(nid))

    def _sort_key(x: str) -> tuple[int, str]:
        return (int(x), "") if x.isdigit() else (999_999, x)

    out.sort(key=_sort_key)
    return out


def _inject_prompt(
    workflow: dict[str, Any],
    prompt: str,
    *,
    node_id: str,
    field: str,
    negative_node_id: str,
    negative_prompt: str,
) -> None:
    text = prompt[:8000]
    nid = (node_id or "").strip()
    if nid:
        node = workflow.get(nid)
        if not isinstance(node, dict):
            raise ValueError(f"comfyui: prompt node {nid} missing from workflow")
        inputs = node.setdefault("inputs", {})
        if field not in inputs:
            raise ValueError(f"comfyui: node {nid} has no input field {field!r}")
        inputs[field] = text
        return

    candidates = _clip_text_encode_node_ids(workflow)
    if not candidates:
        raise ValueError(
            "comfyui: no CLIPTextEncode node with a 'text' input — export API JSON from ComfyUI "
            "and set COMFYUI_PROMPT_NODE_ID to the positive prompt node's id"
        )
    pos_id = candidates[0]
    workflow[pos_id].setdefault("inputs", {})[field] = text

    neg_nid = (negative_node_id or "").strip()
    neg = (negative_prompt or "").strip()
    if neg_nid and neg:
        nnode = workflow.get(neg_nid)
        if isinstance(nnode, dict):
            nnode.setdefault("inputs", {})[field] = neg[:8000]
    elif len(candidates) >= 2 and neg:
        workflow[candidates[1]].setdefault("inputs", {})[field] = neg[:8000]


def _validate_workflow_nodes_in_graph(
    workflow: dict[str, Any],
    *,
    prompt_node_id: str,
    prompt_field: str,
    negative_node_id: str,
    load_image_node_id: str,
    require_load_image: bool,
) -> tuple[list[str], list[str]]:
    """Validate API-export workflow JSON against configured node ids. Returns (errors, warnings)."""
    errors: list[str] = []
    warnings: list[str] = []
    field = (prompt_field or "text").strip() or "text"
    p_nid = (prompt_node_id or "").strip()
    if p_nid:
        node = workflow.get(p_nid)
        if not isinstance(node, dict):
            errors.append(f"prompt_node_missing:{p_nid}")
        else:
            inputs = node.get("inputs")
            if not isinstance(inputs, dict) or field not in inputs:
                errors.append(f"prompt_node_missing_field:{p_nid}:{field}")
    elif not _clip_text_encode_node_ids(workflow):
        errors.append("no_clip_text_encode_for_auto_prompt_inject")

    n_nid = (negative_node_id or "").strip()
    if n_nid:
        node = workflow.get(n_nid)
        if not isinstance(node, dict):
            errors.append(f"negative_node_missing:{n_nid}")
        else:
            inputs = node.get("inputs")
            if not isinstance(inputs, dict) or field not in inputs:
                errors.append(f"negative_node_missing_field:{n_nid}:{field}")

    if require_load_image:
        lid = (load_image_node_id or "").strip()
        if not lid:
            errors.append("load_image_node_id_required_when_comfyui_video_use_scene_image")
        else:
            node = workflow.get(lid)
            if not isinstance(node, dict):
                errors.append(f"load_image_node_missing:{lid}")
            else:
                inputs = node.get("inputs")
                if not isinstance(inputs, dict) or "image" not in inputs:
                    errors.append(f"load_image_node_no_image_input:{lid}")

    return errors, warnings


def _workflow_env_report_from_path(
    role: str,
    path: Path,
    *,
    prompt_node_id: str,
    prompt_field: str,
    negative_node_id: str,
    load_image_node_id: str = "",
    require_load_image: bool = False,
) -> dict[str, Any]:
    """Structured checks for adapter smoke / local ComfyUI env verification."""
    out: dict[str, Any] = {
        "role": role,
        "path": str(path),
        "ok": False,
        "errors": [],
        "warnings": [],
    }
    try:
        raw_text = path.read_text(encoding="utf-8")
        wf = json.loads(raw_text)
    except OSError as e:
        out["errors"].append(f"read_failed:{e}")
        return out
    except json.JSONDecodeError as e:
        out["errors"].append(f"invalid_json:{e}")
        return out
    if not isinstance(wf, dict):
        out["errors"].append("workflow_root_not_object")
        return out
    errs, warns = _validate_workflow_nodes_in_graph(
        wf,
        prompt_node_id=prompt_node_id,
        prompt_field=prompt_field,
        negative_node_id=negative_node_id,
        load_image_node_id=load_image_node_id,
        require_load_image=require_load_image,
    )
    out["errors"] = errs
    out["warnings"] = warns
    out["ok"] = len(errs) == 0
    return out


_VIDEO_EXTS = (".mp4", ".webm", ".mkv", ".mov", ".avi")


def _pick_output_video(entry: dict[str, Any]) -> dict[str, str] | None:
    """
    ComfyUI PreviewVideo serializes as ``{"images": [SavedResult, ...], "animated": (True,)}``
    (see comfy_api/latest/_ui.py). Pick the last video file by node id order.
    """
    outputs = entry.get("outputs")
    if not isinstance(outputs, dict):
        return None
    last: dict[str, str] | None = None
    for nid in sorted(outputs.keys(), key=lambda x: int(x) if str(x).isdigit() else 999_999):
        node_out = outputs.get(nid)
        if not isinstance(node_out, dict):
            continue
        images = node_out.get("images")
        if not isinstance(images, list):
            continue
        for img in images:
            if not isinstance(img, dict):
                continue
            fn = img.get("filename")
            if not isinstance(fn, str) or not fn:
                continue
            low = fn.lower()
            anim = node_out.get("animated")
            is_anim = anim is True or (
                isinstance(anim, (list, tuple)) and len(anim) > 0 and bool(anim[0])
            )
            if is_anim or any(low.endswith(ext) for ext in _VIDEO_EXTS):
                last = {
                    "filename": fn,
                    "subfolder": str(img.get("subfolder") or ""),
                    "type": str(img.get("type") or "output"),
                }
    return last


def _pick_output_image(entry: dict[str, Any]) -> dict[str, str] | None:
    outputs = entry.get("outputs")
    if not isinstance(outputs, dict):
        return None
    last: dict[str, str] | None = None
    for nid in sorted(outputs.keys(), key=lambda x: int(x) if str(x).isdigit() else 999_999):
        node_out = outputs.get(nid)
        if not isinstance(node_out, dict):
            continue
        images = node_out.get("images")
        if not isinstance(images, list):
            continue
        for img in images:
            if not isinstance(img, dict):
                continue
            fn = img.get("filename")
            if not isinstance(fn, str) or not fn:
                continue
            last = {
                "filename": fn,
                "subfolder": str(img.get("subfolder") or ""),
                "type": str(img.get("type") or "output"),
            }
    return last


def smoke_image(settings: Settings) -> dict[str, Any]:
    base = _effective_base_url(settings)
    if not base:
        return {
            "configured": False,
            "provider": "comfyui",
            "error": "comfyui_base_url_empty",
            "detail": "Set COMFYUI_BASE_URL to your ComfyUI HTTP root (or use COMFYUI_API_FLAVOR=cloud).",
        }
    if _is_cloud(settings) and not (settings.comfyui_api_key or "").strip():
        return {
            "configured": False,
            "provider": "comfyui",
            "error": "comfyui_api_key_required",
            "detail": "Comfy Cloud requires COMFYUI_API_KEY or COMFY_CLOUD_API_KEY (X-API-Key).",
            "base_url": base,
            "api_flavor": "cloud",
        }
    hdr = _comfyui_request_headers(settings)
    try:
        wf_path = _resolve_workflow_path(settings)
    except OSError as e:
        return {
            "configured": False,
            "provider": "comfyui",
            "error": str(e),
            "base_url": base,
        }

    img_field = (settings.comfyui_prompt_input_key or "text").strip() or "text"
    img_env = _workflow_env_report_from_path(
        "image",
        wf_path,
        prompt_node_id=settings.comfyui_prompt_node_id,
        prompt_field=img_field,
        negative_node_id=settings.comfyui_negative_node_id,
        require_load_image=False,
    )
    video_env: dict[str, Any]
    try:
        vpath = _resolve_video_workflow_path(settings)
    except FileNotFoundError:
        video_env = {
            "skipped": True,
            "detail": "COMFYUI_VIDEO_WORKFLOW_JSON_PATH unset or file not found",
        }
    else:
        v_field = (
            (settings.comfyui_video_prompt_input_key or settings.comfyui_prompt_input_key or "text").strip()
            or "text"
        )
        video_env = _workflow_env_report_from_path(
            "video",
            vpath,
            prompt_node_id=settings.comfyui_video_prompt_node_id or settings.comfyui_prompt_node_id,
            prompt_field=v_field,
            negative_node_id=settings.comfyui_video_negative_node_id or settings.comfyui_negative_node_id,
            load_image_node_id=settings.comfyui_video_load_image_node_id,
            require_load_image=bool(settings.comfyui_video_use_scene_image),
        )

    workflow_env: dict[str, Any] = {"image": img_env, "video": video_env}
    workflow_env_ok = bool(img_env.get("ok")) and (
        bool(video_env.get("skipped")) or bool(video_env.get("ok"))
    )

    if not img_env.get("ok"):
        return {
            "configured": False,
            "provider": "comfyui",
            "error": "workflow_env_image_invalid",
            "detail": "; ".join(img_env.get("errors") or []),
            "base_url": base,
            "workflow_path": str(wf_path),
            "workflow_env": workflow_env,
            "workflow_env_ok": False,
        }

    ping = f"{base}/api/object_info" if _is_cloud(settings) else f"{base}/system_stats"
    try:
        with httpx.Client(timeout=8.0, headers=hdr) as client:
            r = client.get(ping)
            if r.status_code >= 400:
                return {
                    "configured": True,
                    "provider": "comfyui",
                    "base_url": base,
                    "api_flavor": "cloud" if _is_cloud(settings) else "oss",
                    "workflow_path": str(wf_path),
                    "workflow_env": workflow_env,
                    "workflow_env_ok": workflow_env_ok,
                    "error": f"http_{r.status_code}",
                    "detail": r.text[:400],
                }
    except httpx.RequestError as e:
        return {
            "configured": False,
            "provider": "comfyui",
            "base_url": base,
            "api_flavor": "cloud" if _is_cloud(settings) else "oss",
            "workflow_path": str(wf_path),
            "workflow_env": workflow_env,
            "workflow_env_ok": workflow_env_ok,
            "error": "request_failed",
            "detail": str(e)[:400],
        }
    out: dict[str, Any] = {
        "configured": True,
        "provider": "comfyui",
        "base_url": base,
        "api_flavor": "cloud" if _is_cloud(settings) else "oss",
        "workflow_path": str(wf_path),
        "workflow_env": workflow_env,
        "workflow_env_ok": workflow_env_ok,
    }
    if not _is_cloud(settings):
        out["comfyui_use_websocket"] = bool(getattr(settings, "comfyui_use_websocket", True))
    if not workflow_env_ok and not video_env.get("skipped"):
        out["workflow_env_warnings"] = [
            "Video workflow env has errors; comfyui_wan may fail until COMFYUI_VIDEO_* node paths match the JSON.",
        ]
    return out


def generate_scene_image_comfyui(
    settings: Settings, prompt: str, *, negative_prompt: str | None = None
) -> dict[str, Any]:
    """
    Run a saved API-format workflow with the scene prompt injected.

    Returns {ok, bytes?, content_type?, error?, detail?, provider, model?}.
    """
    base = _effective_base_url(settings)
    if not base:
        return {
            "ok": False,
            "provider": "comfyui",
            "error": "comfyui_base_url_empty",
            "detail": "Set COMFYUI_BASE_URL to your ComfyUI HTTP root (or use COMFYUI_API_FLAVOR=cloud).",
        }
    if _is_cloud(settings) and not (settings.comfyui_api_key or "").strip():
        return {
            "ok": False,
            "provider": "comfyui",
            "error": "comfyui_api_key_required",
            "detail": "Comfy Cloud requires COMFYUI_API_KEY or COMFY_CLOUD_API_KEY.",
        }
    timeout = max(30.0, float(settings.comfyui_timeout_sec))
    poll = max(0.2, min(5.0, float(settings.comfyui_poll_interval_sec)))
    hdr = _comfyui_request_headers(settings)

    try:
        wf_path = _resolve_workflow_path(settings)
    except OSError as e:
        return {"ok": False, "provider": "comfyui", "error": "workflow_path", "detail": str(e)}

    try:
        tpl = json.loads(wf_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        return {"ok": False, "provider": "comfyui", "error": "workflow_json", "detail": str(e)[:800]}

    if not isinstance(tpl, dict):
        return {"ok": False, "provider": "comfyui", "error": "workflow_not_object"}

    workflow = copy.deepcopy(tpl)
    field = (settings.comfyui_prompt_input_key or "text").strip() or "text"
    scene_neg = (negative_prompt or "").strip()
    cfg_neg = (settings.comfyui_default_negative_prompt or "").strip()
    if scene_neg and cfg_neg:
        merged_negative = f"{scene_neg}, {cfg_neg}"
    elif scene_neg:
        merged_negative = scene_neg
    else:
        merged_negative = cfg_neg
    try:
        _inject_prompt(
            workflow,
            str(prompt),
            node_id=settings.comfyui_prompt_node_id,
            field=field,
            negative_node_id=settings.comfyui_negative_node_id,
            negative_prompt=merged_negative,
        )
    except ValueError as e:
        return {"ok": False, "provider": "comfyui", "error": "prompt_inject", "detail": str(e)}

    model = (settings.comfyui_model_name or "").strip() or wf_path.name
    client_id = str(uuid.uuid4())
    body = {"prompt": workflow, "client_id": client_id}

    try:
        with httpx.Client(timeout=timeout, headers=hdr, follow_redirects=True) as http:
            pr = http.post(f"{base}{_prompt_path(settings)}", json=body)
            if pr.status_code >= 400:
                try:
                    err_j = pr.json()
                    detail = json.dumps(err_j)[:2000]
                except Exception:
                    detail = pr.text[:2000]
                return {
                    "ok": False,
                    "provider": "comfyui",
                    "model": model,
                    "error": f"http_{pr.status_code}",
                    "detail": detail,
                }
            try:
                q = pr.json()
            except Exception:
                return {"ok": False, "provider": "comfyui", "model": model, "error": "prompt_bad_json"}
            prompt_id = str(q.get("prompt_id") or "")
            if not prompt_id:
                return {"ok": False, "provider": "comfyui", "model": model, "error": "no_prompt_id"}

            entry, wait_err, wait_detail = _wait_for_history_entry(
                http, settings, base, prompt_id, timeout, poll, client_id=client_id
            )
            if wait_err:
                return {
                    "ok": False,
                    "provider": "comfyui",
                    "model": model,
                    "error": wait_err,
                    "detail": wait_detail or "",
                }
            if not isinstance(entry, dict):
                return {
                    "ok": False,
                    "provider": "comfyui",
                    "model": model,
                    "error": "no_history_entry",
                    "detail": "Unexpected empty history",
                }

            status = entry.get("status")
            if isinstance(status, dict) and status.get("status_str") == "error":
                msgs = status.get("messages") or []
                return {
                    "ok": False,
                    "provider": "comfyui",
                    "model": model,
                    "error": "comfyui_execution_error",
                    "detail": str(msgs)[:2000],
                }

            ref = _pick_output_image(entry)
            if not ref:
                return {
                    "ok": False,
                    "provider": "comfyui",
                    "model": model,
                    "error": "no_output_image",
                    "detail": str(list((entry.get("outputs") or {}).keys()))[:500],
                }

            vr = http.get(
                f"{base}{_view_path(settings)}",
                params={
                    "filename": ref["filename"],
                    "subfolder": ref["subfolder"],
                    "type": ref["type"],
                },
            )
            if vr.status_code >= 400:
                return {
                    "ok": False,
                    "provider": "comfyui",
                    "model": model,
                    "error": f"view_http_{vr.status_code}",
                    "detail": vr.text[:400],
                }
            ct = vr.headers.get("content-type") or "image/png"
            return {
                "ok": True,
                "provider": "comfyui",
                "model": model,
                "bytes": vr.content,
                "content_type": ct.split(";")[0].strip(),
            }
    except httpx.RequestError as e:
        return {"ok": False, "provider": "comfyui", "model": model, "error": "request_failed", "detail": str(e)[:800]}


def _resolve_video_workflow_path(settings: Settings) -> Path:
    raw = (settings.comfyui_video_workflow_json_path or "").strip()
    if not raw:
        raise FileNotFoundError(
            "comfyui_video_workflow_json_path is empty (set COMFYUI_VIDEO_WORKFLOW_JSON_PATH to your WAN / Save Video API JSON)"
        )
    p = Path(raw)
    if p.is_file():
        return p.resolve()
    p2 = (_REPO_ROOT / raw).resolve()
    if p2.is_file():
        return p2
    raise FileNotFoundError(f"ComfyUI video workflow JSON not found: {raw}")


def _upload_image_to_comfyui(
    http: httpx.Client,
    settings: Settings,
    base: str,
    image_bytes: bytes,
    filename: str,
) -> dict[str, Any]:
    ct = "image/jpeg"
    low = filename.lower()
    if low.endswith(".png"):
        ct = "image/png"
    elif low.endswith(".webp"):
        ct = "image/webp"
    files = {"image": (filename, image_bytes, ct)}
    data = {"type": "input", "overwrite": "true"}
    ur = http.post(f"{base}{_upload_image_path(settings)}", files=files, data=data, timeout=120.0)
    if ur.status_code >= 400:
        return {
            "ok": False,
            "error": f"upload_http_{ur.status_code}",
            "detail": ur.text[:600],
        }
    try:
        body = ur.json()
    except Exception:
        return {"ok": False, "error": "upload_bad_json", "detail": ur.text[:400]}
    name = body.get("name")
    if not isinstance(name, str) or not name:
        return {"ok": False, "error": "upload_no_name", "detail": str(body)[:400]}
    return {"ok": True, "name": name, "subfolder": str(body.get("subfolder") or ""), "type": str(body.get("type") or "input")}


def generate_scene_video_comfyui(
    settings: Settings,
    prompt: str,
    *,
    scene_image_path: Path | None = None,
    duration_sec: float | None = None,
) -> dict[str, Any]:
    """
    Run a ComfyUI API workflow that ends in Save Video / PreviewVideo (e.g. WAN 2.1 i2v).

    When ``comfyui_video_use_scene_image`` is True, pass ``scene_image_path`` to upload into ComfyUI
    and set the LoadImage node given by ``comfyui_video_load_image_node_id``.

    Returns {ok, bytes?, content_type?, error?, detail?, provider, model?}.
    """
    _ = duration_sec  # reserved for future frame-count / length injection
    base = _effective_base_url(settings)
    if not base:
        return {
            "ok": False,
            "provider": "comfyui_wan",
            "error": "comfyui_base_url_empty",
            "detail": "Set COMFYUI_BASE_URL to your ComfyUI HTTP root (or use COMFYUI_API_FLAVOR=cloud).",
        }
    if _is_cloud(settings) and not (settings.comfyui_api_key or "").strip():
        return {
            "ok": False,
            "provider": "comfyui_wan",
            "error": "comfyui_api_key_required",
            "detail": "Comfy Cloud requires COMFYUI_API_KEY or COMFY_CLOUD_API_KEY.",
        }
    timeout = max(60.0, float(settings.comfyui_video_timeout_sec))
    poll = max(0.2, min(5.0, float(settings.comfyui_poll_interval_sec)))
    hdr = _comfyui_request_headers(settings)

    try:
        wf_path = _resolve_video_workflow_path(settings)
    except OSError as e:
        return {"ok": False, "provider": "comfyui_wan", "error": "workflow_path", "detail": str(e)}

    try:
        tpl = json.loads(wf_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        return {"ok": False, "provider": "comfyui_wan", "error": "workflow_json", "detail": str(e)[:800]}

    if not isinstance(tpl, dict):
        return {"ok": False, "provider": "comfyui_wan", "error": "workflow_not_object"}

    workflow = copy.deepcopy(tpl)
    p_node = (settings.comfyui_video_prompt_node_id or settings.comfyui_prompt_node_id or "").strip()
    neg_node = (settings.comfyui_video_negative_node_id or settings.comfyui_negative_node_id or "").strip()
    neg_text = (settings.comfyui_video_default_negative_prompt or settings.comfyui_default_negative_prompt or "").strip()
    field = (
        (settings.comfyui_video_prompt_input_key or settings.comfyui_prompt_input_key or "text").strip() or "text"
    )

    use_img = bool(settings.comfyui_video_use_scene_image)
    load_nid = (settings.comfyui_video_load_image_node_id or "").strip()
    model = (settings.comfyui_video_model_name or "").strip() or wf_path.name

    try:
        with httpx.Client(timeout=timeout, headers=hdr, follow_redirects=True) as http:
            if use_img:
                if scene_image_path is None or not scene_image_path.is_file():
                    return {
                        "ok": False,
                        "provider": "comfyui_wan",
                        "model": model,
                        "error": "scene_image_required",
                        "detail": "comfyui_video_use_scene_image is true but no local scene image path was provided",
                    }
                if not load_nid:
                    return {
                        "ok": False,
                        "provider": "comfyui_wan",
                        "model": model,
                        "error": "load_image_node_required",
                        "detail": "Set COMFYUI_VIDEO_LOAD_IMAGE_NODE_ID to your LoadImage node id (API JSON)",
                    }
                img_bytes = scene_image_path.read_bytes()
                up = _upload_image_to_comfyui(
                    http,
                    settings,
                    base,
                    img_bytes,
                    scene_image_path.name or "director_scene.jpg",
                )
                if not up.get("ok"):
                    return {
                        "ok": False,
                        "provider": "comfyui_wan",
                        "model": model,
                        "error": str(up.get("error") or "upload_failed"),
                        "detail": str(up.get("detail") or "")[:800],
                    }
                lnode = workflow.get(load_nid)
                if not isinstance(lnode, dict):
                    return {
                        "ok": False,
                        "provider": "comfyui_wan",
                        "model": model,
                        "error": "load_image_node_missing",
                        "detail": f"Node {load_nid} not in workflow",
                    }
                lnode.setdefault("inputs", {})["image"] = up["name"]

            try:
                _inject_prompt(
                    workflow,
                    str(prompt),
                    node_id=p_node,
                    field=field,
                    negative_node_id=neg_node,
                    negative_prompt=neg_text,
                )
            except ValueError as e:
                return {"ok": False, "provider": "comfyui_wan", "model": model, "error": "prompt_inject", "detail": str(e)}

            client_id = str(uuid.uuid4())
            pr = http.post(
                f"{base}{_prompt_path(settings)}",
                json={"prompt": workflow, "client_id": client_id},
            )
            if pr.status_code >= 400:
                try:
                    detail = json.dumps(pr.json())[:2000]
                except Exception:
                    detail = pr.text[:2000]
                return {
                    "ok": False,
                    "provider": "comfyui_wan",
                    "model": model,
                    "error": f"http_{pr.status_code}",
                    "detail": detail,
                }
            try:
                q = pr.json()
            except Exception:
                return {"ok": False, "provider": "comfyui_wan", "model": model, "error": "prompt_bad_json"}
            prompt_id = str(q.get("prompt_id") or "")
            if not prompt_id:
                return {"ok": False, "provider": "comfyui_wan", "model": model, "error": "no_prompt_id"}

            entry, wait_err, wait_detail = _wait_for_history_entry(
                http, settings, base, prompt_id, timeout, poll, client_id=client_id
            )
            if wait_err:
                return {
                    "ok": False,
                    "provider": "comfyui_wan",
                    "model": model,
                    "error": wait_err,
                    "detail": wait_detail or "",
                }
            if not isinstance(entry, dict):
                return {
                    "ok": False,
                    "provider": "comfyui_wan",
                    "model": model,
                    "error": "no_history_entry",
                    "detail": "Unexpected empty history",
                }

            status = entry.get("status")
            if isinstance(status, dict) and status.get("status_str") == "error":
                msgs = status.get("messages") or []
                return {
                    "ok": False,
                    "provider": "comfyui_wan",
                    "model": model,
                    "error": "comfyui_execution_error",
                    "detail": str(msgs)[:2000],
                }

            ref = _pick_output_video(entry)
            if not ref:
                ref = _pick_output_image(entry)
                if ref and not any(str(ref.get("filename") or "").lower().endswith(ext) for ext in _VIDEO_EXTS):
                    ref = None
            if not ref:
                return {
                    "ok": False,
                    "provider": "comfyui_wan",
                    "model": model,
                    "error": "no_output_video",
                    "detail": str(list((entry.get("outputs") or {}).keys()))[:500],
                }

            vr = http.get(
                f"{base}{_view_path(settings)}",
                params={
                    "filename": ref["filename"],
                    "subfolder": ref["subfolder"],
                    "type": ref["type"],
                },
            )
            if vr.status_code >= 400:
                return {
                    "ok": False,
                    "provider": "comfyui_wan",
                    "model": model,
                    "error": f"view_http_{vr.status_code}",
                    "detail": vr.text[:400],
                }
            ct = vr.headers.get("content-type") or "video/mp4"
            return {
                "ok": True,
                "provider": "comfyui_wan",
                "model": model,
                "bytes": vr.content,
                "content_type": ct.split(";")[0].strip(),
            }
    except httpx.RequestError as e:
        return {
            "ok": False,
            "provider": "comfyui_wan",
            "model": model,
            "error": "request_failed",
            "detail": str(e)[:800],
        }
