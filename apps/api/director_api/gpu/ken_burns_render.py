#!/usr/bin/env python3
"""Standalone GPU Ken Burns renderer: still image -> MP4, warped on the GPU, encoded with NVENC.

This script is executed by the CUDA *sidecar* venv (Python 3.11 + torch cuXX). It is NEVER
imported by the Director worker (whose Python 3.14 runtime has no CUDA PyTorch). Dependencies
are only ``torch`` and ``numpy`` plus a working ``ffmpeg`` on PATH (or via ``--ffmpeg``).

Pipeline (everything except one decode + the NVENC encode is the per-frame affine warp on GPU):

1. Decode the still once with ffmpeg to a raw RGB buffer at an over-scaled, frame-aspect size.
2. Upload to a CUDA tensor; for each output frame build an affine sampling grid (zoom/pan with
   easing) and ``grid_sample`` on the GPU.
3. Pipe the rendered rgb24 frames into ``ffmpeg -c:v h264_nvenc`` (plus a silent AAC track), so
   the encode also runs on the GPU.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

import numpy as np
import torch
import torch.nn.functional as F


def _ease(s: float, easing: str) -> float:
    if easing == "smoother":
        return 6 * s**5 - 15 * s**4 + 10 * s**3
    if easing == "smooth":
        return 3 * s**2 - 2 * s**3
    return s


def _decode_rgb(ffmpeg: str, image: str, w: int, h: int) -> np.ndarray:
    """Decode + cover-scale the still to exactly (h, w, 3) uint8 via ffmpeg."""
    cmd = [
        ffmpeg, "-y", "-v", "error",
        "-i", image,
        "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},format=rgb24",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ]
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or b"").decode("utf-8", "ignore")[-2000:] or "ffmpeg decode failed")
    need = w * h * 3
    buf = np.frombuffer(p.stdout, dtype=np.uint8)
    if buf.size < need:
        raise RuntimeError(f"short decode: got {buf.size} want {need}")
    return buf[:need].reshape(h, w, 3)


def _theta(motion: str, direction: str, p: float, *, zoom_frac: float, over: float, dev, dtype):
    """Affine matrix mapping output [-1,1] grid -> input [-1,1] (source is over-scaled)."""
    fill = 1.0 / over  # frame-filling crop of the over-scaled source (no black bars)
    k = 0.9  # keep pan just inside the source edges

    if motion == "zoom":
        if direction == "out":
            a = fill / (1.0 + zoom_frac * (1.0 - p))
        else:
            a = fill / (1.0 + zoom_frac * p)
        tx = 0.0
        ty = 0.0
    elif motion == "pan":
        a = fill
        span = (1.0 - a) * k
        if direction == "left":
            tx = span * (1.0 - 2.0 * p)
        else:
            tx = span * (2.0 * p - 1.0)
        # gentle diagonal drift
        ty = span * (0.3 - 0.6 * p) if direction == "left" else span * (-0.3 + 0.6 * p)
    else:
        a = fill
        tx = 0.0
        ty = 0.0

    return torch.tensor([[[a, 0.0, tx], [0.0, a, ty]]], device=dev, dtype=dtype)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--duration", type=float, required=True)
    ap.add_argument("--width", type=int, required=True)
    ap.add_argument("--height", type=int, required=True)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--motion", choices=("none", "pan", "zoom"), default="zoom")
    ap.add_argument("--direction", choices=("in", "out", "left", "right"), default="in")
    ap.add_argument("--zoom-frac", type=float, default=0.12)
    ap.add_argument("--overscale", type=float, default=1.25)
    ap.add_argument("--easing", choices=("linear", "smooth", "smoother"), default="smooth")
    ap.add_argument("--ffmpeg", default="ffmpeg")
    ap.add_argument("--nvenc", default="h264_nvenc")
    ap.add_argument("--cq", type=int, default=23)
    args = ap.parse_args()

    if not torch.cuda.is_available():
        print("CUDA not available in sidecar", file=sys.stderr)
        return 3

    dev = torch.device("cuda")
    dtype = torch.float32
    W, H, fps = args.width, args.height, max(1, args.fps)
    over = max(1.01, float(args.overscale))
    OW = ((round(W * over) + 1) // 2) * 2
    OH = ((round(H * over) + 1) // 2) * 2

    src = _decode_rgb(args.ffmpeg, args.image, OW, OH)  # (OH, OW, 3)
    img = torch.from_numpy(src.copy()).to(dev).to(dtype).div_(255.0).permute(2, 0, 1).unsqueeze(0)

    n_frames = max(2, round(float(args.duration) * fps))
    denom = max(1, n_frames - 1)

    enc = [
        args.ffmpeg, "-y", "-v", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-t", f"{float(args.duration):.3f}",
        "-c:v", args.nvenc, "-preset", "p4", "-rc", "vbr", "-cq", str(args.cq), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", "-shortest",
        args.out,
    ]
    proc = subprocess.Popen(enc, stdin=subprocess.PIPE)
    assert proc.stdin is not None
    try:
        with torch.no_grad():
            for n in range(n_frames):
                s = n / denom
                p = _ease(s, args.easing)
                theta = _theta(
                    args.motion, args.direction, p,
                    zoom_frac=float(args.zoom_frac), over=over, dev=dev, dtype=dtype,
                )
                grid = F.affine_grid(theta, (1, 3, H, W), align_corners=False)
                frame = F.grid_sample(img, grid, mode="bilinear", padding_mode="border", align_corners=False)
                out8 = (
                    frame.clamp_(0.0, 1.0).mul_(255.0).round_().byte()
                    .squeeze(0).permute(1, 2, 0).contiguous().cpu().numpy()
                )
                proc.stdin.write(out8.tobytes())
        proc.stdin.close()
    except BrokenPipeError:
        pass
    rc = proc.wait()
    if rc != 0:
        print(f"nvenc encode failed rc={rc}", file=sys.stderr)
        return rc or 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
