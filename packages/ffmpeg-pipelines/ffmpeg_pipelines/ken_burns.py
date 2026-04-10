"""Ken Burns-style motion via FFmpeg ``zoompan``.

* **Slow zoom** (``build_slow_zoom_vf``): classic Ken Burns zoom in/out.
* **Smooth pan** (``build_crop_pan_vf``): diagonal drift using ``zoompan``
  with z=1 (no zoom, just sub-pixel pan). Much faster than actual zoom because
  the resampling is trivial (fixed crop size), yet avoids the integer-pixel
  staircase effect of the ``crop`` filter.

See FFmpeg ``zoompan`` docs:
https://ffmpeg.org/ffmpeg-filters.html#zoompan
"""

from __future__ import annotations

from typing import Literal

Easing = Literal["linear", "smooth", "smoother"]


def _zp_easing(st: str, easing: Easing) -> str:
    """Build an easing expression for zoompan (uses ``on``-based time, no comma escaping needed)."""
    if easing == "smoother":
        return f"(6*pow({st},5)-15*pow({st},4)+10*pow({st},3))"
    if easing == "smooth":
        return f"(3*pow({st},2)-2*pow({st},3))"
    return st


def build_slow_zoom_vf(
    *,
    width: int,
    height: int,
    fps: int,
    duration_sec: float,
    zoom_frac: float = 0.07,
    overscale: float = 1.12,
    direction: Literal["in", "out"] = "in",
    easing: Easing = "smooth",
) -> str:
    """
    Video filter: upscale slightly, then center-crop with ``zoompan``.

    * **Zoom in** (default): scale runs from 1.0 -> (1 + ``zoom_frac``).
    * **Zoom out**: scale runs from (1 + ``zoom_frac``) -> 1.0.

    ``duration_sec`` sets how many frames ``zoompan`` emits (``d = round(duration * fps)``, min 2).
    """
    dur = max(0.05, float(duration_sec))
    dframes = max(2, int(round(dur * float(fps))))
    denom = max(1, dframes - 1)
    zf = float(zoom_frac)
    oscl = float(overscale)

    st = f"on/{denom}"
    p = _zp_easing(st, easing)

    if direction == "in":
        zexpr = f"1+{zf:.8f}*{p}"
    else:
        zexpr = f"(1+{zf:.8f})-{zf:.8f}*{p}"

    return (
        f"scale=ceil(iw*{oscl}/2)*2:ceil(ih*{oscl}/2)*2:force_original_aspect_ratio=decrease,"
        f"zoompan=z='{zexpr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={dframes}:s={width}x{height}:fps={fps},"
        f"format=yuv420p"
    )


def build_crop_pan_vf(
    *,
    width: int,
    height: int,
    fps: int,
    duration_sec: float,
    overscale: float = 1.25,
    direction: Literal["right", "left"] = "right",
    easing: Easing = "smoother",
    diagonal: bool = True,
) -> str:
    """
    Pre-scale image to ``overscale`` x output, then use ``zoompan`` with z=1
    (no zoom) to smoothly pan across it with sub-pixel interpolation.

    Faster than actual zoom (z is constant so resampling is trivial) but avoids
    the integer-pixel staircase artefact of the ``crop`` filter.

    * **right** (default): drift left-to-right (+ slight downward if ``diagonal``).
    * **left**: drift right-to-left (+ slight upward if ``diagonal``).
    """
    dur = max(0.05, float(duration_sec))
    dframes = max(2, int(round(dur * float(fps))))
    denom = max(1, dframes - 1)
    oscl = max(1.01, float(overscale))

    w_s = max(width + 2, ((int(width * oscl) + 1) // 2) * 2)
    h_s = max(height + 2, ((int(height * oscl) + 1) // 2) * 2)

    st = f"on/{denom}"
    p = _zp_easing(st, easing)

    if direction == "right":
        x_expr = f"(iw-{width})*{p}"
    else:
        x_expr = f"(iw-{width})*(1-{p})"

    if diagonal:
        if direction == "right":
            y_expr = f"(ih-{height})*(0.3+0.4*{p})"
        else:
            y_expr = f"(ih-{height})*(0.7-0.4*{p})"
    else:
        y_expr = f"(ih-{height})/2"

    return (
        f"scale={w_s}:{h_s}:force_original_aspect_ratio=decrease,"
        f"pad={w_s}:{h_s}:(ow-iw)/2:(oh-ih)/2,"
        f"zoompan=z='1':x='{x_expr}':y='{y_expr}':d={dframes}:s={width}x{height}:fps={fps},"
        f"format=yuv420p"
    )
