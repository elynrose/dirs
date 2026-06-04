"""Per-scene camera angle / perspective hints for image and video generation."""

from __future__ import annotations

import hashlib
import re

# Stable variety across scenes; index chosen from scene identity + order.
_IMAGE_PERSPECTIVE_HINTS: tuple[str, ...] = (
    "Camera perspective: eye-level medium documentary framing, natural and observational.",
    "Camera perspective: low-angle shot looking up at the subject, subtle dramatic scale.",
    "Camera perspective: high-angle shot looking down on the scene for context and geography.",
    "Camera perspective: side profile three-quarter view, subject readable in profile.",
    "Camera perspective: over-the-shoulder view toward the focal action, depth in frame.",
    "Camera perspective: view from behind the subject toward what they face, back partially visible.",
    "Camera perspective: wide elevated bird's-eye view establishing the environment.",
    "Camera perspective: worm's-eye view from ground level, strong vertical emphasis.",
    "Camera perspective: Dutch angle tilt for tension while keeping the subject legible.",
    "Camera perspective: distant long-lens compression, subject small in a vast setting.",
)

_VIDEO_PERSPECTIVE_HINTS: tuple[str, ...] = (
    "Camera motion: slow push-in from eye level; stable documentary feel.",
    "Camera motion: gentle low-angle drift upward; same subject and setting as the still.",
    "Camera motion: high-angle crane or tilt down revealing layout; same world as the still.",
    "Camera motion: lateral truck along the subject's profile; same beat and wardrobe.",
    "Camera motion: over-the-shoulder drift toward the focal point; shallow depth of field.",
    "Camera motion: follow from behind the subject toward what they face; same continuity.",
    "Camera motion: slow descending bird's-eye move; environment stays consistent with the still.",
    "Camera motion: low ground-level creep forward; same subject and location as the still.",
    "Camera motion: subtle handheld observational sway at a three-quarter angle.",
    "Camera motion: slow pull-back revealing wider context; same characters and era.",
)

_ANGLE_KEYWORD_RE = re.compile(
    r"\b("
    r"eye[- ]?level|low[- ]?angle|high[- ]?angle|bird['\u2019]?s[- ]?eye|worm['\u2019]?s[- ]?eye|"
    r"over[- ]?the[- ]?shoulder|ots\b|from behind|rear view|back view|side profile|profile view|"
    r"three[- ]?quarter|dutch angle|tilted frame|top[- ]?down|overhead shot|aerial view|"
    r"ground[- ]?level|worm['\u2019]?s eye|elevated view|looking up at|looking down on|"
    r"camera perspective:|camera motion:"
    r")\b",
    re.IGNORECASE,
)

_LEADING_TAG_RE = re.compile(r"^\s*(\[[A-Z][A-Z0-9_]*\])\s*")


def _stable_index(key: str, modulo: int) -> int:
    if modulo <= 0:
        return 0
    digest = hashlib.md5(key.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % modulo


def scene_camera_hint_index(*, scene_key: str, order_index: int | None = None) -> int:
    """Pick a perspective variant deterministically for ``scene_key`` (scene id or chapter id)."""
    oi = 0 if order_index is None else int(order_index)
    return _stable_index(f"{scene_key}:{oi}", len(_IMAGE_PERSPECTIVE_HINTS))


def scene_camera_image_hint(scene_key: str, order_index: int | None = None) -> str:
    return _IMAGE_PERSPECTIVE_HINTS[scene_camera_hint_index(scene_key=scene_key, order_index=order_index)]


def scene_camera_video_hint(scene_key: str, order_index: int | None = None) -> str:
    idx = scene_camera_hint_index(scene_key=scene_key, order_index=order_index)
    return _VIDEO_PERSPECTIVE_HINTS[idx % len(_VIDEO_PERSPECTIVE_HINTS)]


def prompt_already_specifies_camera_angle(prompt: str | None) -> bool:
    return bool(_ANGLE_KEYWORD_RE.search((prompt or "").strip()))


def inject_camera_perspective_into_prompt(
    prompt: str | None,
    *,
    scene_key: str,
    order_index: int | None,
    for_video: bool,
    max_total: int,
) -> str:
    """Prepend a camera hint when the prompt does not already specify angle or perspective."""
    p = (prompt or "").strip()
    if not p or prompt_already_specifies_camera_angle(p):
        return p[:max_total]
    hint = scene_camera_video_hint(scene_key, order_index) if for_video else scene_camera_image_hint(
        scene_key, order_index
    )
    m = _LEADING_TAG_RE.match(p)
    if m:
        tag = m.group(1)
        rest = p[m.end() :].lstrip()
        combined = f"{tag} {hint}\n\n{rest}" if rest else f"{tag} {hint}"
    else:
        combined = f"{hint}\n\n{p}"
    if len(combined) <= max_total:
        return combined
    room = max(0, max_total - len(hint) - 2)
    if m:
        tag = m.group(1)
        rest = p[m.end() :].lstrip()
        return f"{tag} {hint}\n\n{rest[: max(0, room - len(tag) - 2)]}"[:max_total]
    return f"{hint}\n\n{p[:room]}"[:max_total]
