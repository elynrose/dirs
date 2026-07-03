"""Ken Burns-style motion via FFmpeg ``zoompan``.

* **Slow zoom** (``build_slow_zoom_vf``): classic Ken Burns zoom in/out.
* **Smooth pan** (``build_crop_pan_vf``): diagonal drift across an over-scaled image.

Smoothness
----------
``zoompan`` floors the crop origin to whole pixels every frame, so a slow zoom/pan
advances in visible 1-pixel stair-steps ("jitter"). To avoid that we run the motion on a
**supersampled canvas** (up to ~4K) and then downscale to the output size with lanczos: a
1-pixel step at 4K becomes a fraction of an output pixel, i.e. smooth sub-pixel motion.

See FFmpeg ``zoompan`` docs:
https://ffmpeg.org/ffmpeg-filters.html#zoompan
"""

from __future__ import annotations

from typing import Literal

Easing = Literal["linear", "smooth", "smoother"]

# Cap the supersampled working resolution on the long side (keeps CPU/memory bounded at ~4K).
_SUPERSAMPLE_LONG_CAP = 3840


def _zp_easing(st: str, easing: Easing) -> str:
    """Build an easing expression for zoompan (uses ``on``-based time, no comma escaping needed)."""
    if easing == "smoother":
        return f"(6*pow({st},5)-15*pow({st},4)+10*pow({st},3))"
    if easing == "smooth":
        return f"(3*pow({st},2)-2*pow({st},3))"
    return st


def _even(n: int) -> int:
    return int(n) - (int(n) % 2)


def _supersample_factor(width: int, height: int) -> int:
    """Integer supersample so the long side lands near (but not above) ~4K; >=1."""
    long_side = max(2, int(max(width, height)))
    return max(1, _SUPERSAMPLE_LONG_CAP // long_side)


def build_slow_zoom_vf(
    *,
    width: int,
    height: int,
    fps: int,
    duration_sec: float,
    zoom_frac: float = 0.07,
    overscale: float = 1.12,  # kept for API compatibility; supersampling supersedes it
    direction: Literal["in", "out"] = "in",
    easing: Easing = "smoother",
    supersample: int | None = None,
) -> str:
    """
    Video filter: run the zoom on a supersampled canvas via ``zoompan``, then downscale.

    * **Zoom in** (default): scale runs from 1.0 -> (1 + ``zoom_frac``).
    * **Zoom out**: scale runs from (1 + ``zoom_frac``) -> 1.0.

    ``duration_sec`` sets how many frames ``zoompan`` emits (``d = round(duration * fps)``, min 2).
    """
    dur = max(0.05, float(duration_sec))
    dframes = max(2, int(round(dur * float(fps))))
    denom = max(1, dframes - 1)
    zf = float(zoom_frac)

    ss = int(supersample) if supersample and int(supersample) >= 1 else _supersample_factor(width, height)
    op_w = max(2, _even(width * ss))
    op_h = max(2, _even(height * ss))

    st = f"on/{denom}"
    p = _zp_easing(st, easing)

    if direction == "in":
        zexpr = f"1+{zf:.8f}*{p}"
    else:
        zexpr = f"(1+{zf:.8f})-{zf:.8f}*{p}"

    return (
        # Cover the output aspect at the supersampled resolution.
        f"scale={op_w}:{op_h}:force_original_aspect_ratio=increase,crop={op_w}:{op_h},"
        # Zoom on the high-res canvas; integer crop steps here are sub-pixel after the downscale.
        f"zoompan=z='{zexpr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={dframes}:s={op_w}x{op_h}:fps={fps},"
        f"scale={width}:{height}:flags=lanczos,"
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
    supersample: int | None = None,
) -> str:
    """
    Pan across an over-scaled image using ``zoompan`` (z=1), run on a supersampled canvas
    and downscaled with lanczos so the drift is smooth (sub-pixel) rather than stair-stepped.

    * **right** (default): drift left-to-right (+ slight downward if ``diagonal``).
    * **left**: drift right-to-left (+ slight upward if ``diagonal``).
    """
    dur = max(0.05, float(duration_sec))
    dframes = max(2, int(round(dur * float(fps))))
    denom = max(1, dframes - 1)
    oscl = max(1.01, float(overscale))

    ss = int(supersample) if supersample and int(supersample) >= 1 else _supersample_factor(width, height)
    op_w = max(2, _even(width * ss))
    op_h = max(2, _even(height * ss))
    # Canvas is the operating window plus the pan headroom.
    cw = max(op_w + 2, _even(round(op_w * oscl)))
    ch = max(op_h + 2, _even(round(op_h * oscl)))

    st = f"on/{denom}"
    p = _zp_easing(st, easing)

    if direction == "right":
        x_expr = f"(iw-{op_w})*{p}"
    else:
        x_expr = f"(iw-{op_w})*(1-{p})"

    if diagonal:
        if direction == "right":
            y_expr = f"(ih-{op_h})*(0.3+0.4*{p})"
        else:
            y_expr = f"(ih-{op_h})*(0.7-0.4*{p})"
    else:
        y_expr = f"(ih-{op_h})/2"

    return (
        f"scale={cw}:{ch}:force_original_aspect_ratio=increase,crop={cw}:{ch},"
        f"zoompan=z='1':x='{x_expr}':y='{y_expr}':d={dframes}:s={op_w}x{op_h}:fps={fps},"
        f"scale={width}:{height}:flags=lanczos,"
        f"format=yuv420p"
    )
