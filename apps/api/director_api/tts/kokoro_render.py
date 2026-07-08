#!/usr/bin/env python3
"""Standalone Kokoro TTS renderer: text -> 24kHz PCM WAV + per-token timing JSON.

Executed by the local-TTS sidecar venv (Python 3.11 + torch + kokoro). NEVER imported by the
Director worker (Python 3.14, no torch). The worker writes the narration text to a file, runs
this script, then reads back the WAV (to encode MP3 via ffmpeg) and the token timings (to build
WebVTT). Only the torch-based ``KPipeline`` inference happens here.

Inputs (argv): --text-file, --wav-out, --tokens-out, --voice, --lang, --speed, --repo-id,
optional --device (cpu|cuda|mps) and --sample-rate.
Outputs: a PCM16 WAV at --wav-out and JSON ``{"tokens": [...], "duration": <sec>}`` at --tokens-out.
"""

from __future__ import annotations

import argparse
import json
import sys


def _select_device(pref: str) -> str:
    pref = (pref or "").strip().lower()
    if pref in ("cpu", "cuda", "mps"):
        return pref
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def _audio_to_numpy(chunk):
    import numpy as np

    if hasattr(chunk, "detach"):
        chunk = chunk.detach().cpu().numpy()
    return np.asarray(chunk, dtype=np.float32).reshape(-1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--text-file", required=True)
    ap.add_argument("--wav-out", required=True)
    ap.add_argument("--tokens-out", required=True)
    ap.add_argument("--voice", default="af_bella")
    ap.add_argument("--lang", default="a")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--repo-id", default="hexgrad/Kokoro-82M")
    ap.add_argument("--device", default="")
    ap.add_argument("--sample-rate", type=int, default=24000)
    args = ap.parse_args()

    with open(args.text_file, "r", encoding="utf-8") as fh:
        body = (fh.read() or "").strip()
    if len(body) < 2:
        print("empty narration text", file=sys.stderr)
        return 2

    import numpy as np
    import soundfile as sf
    from kokoro import KPipeline

    _valid = frozenset("abefhijpz")
    lc = args.lang.strip().lower()
    lc = lc if lc in _valid else "a"
    device = _select_device(args.device)
    voice = (args.voice or "af_bella").strip() or "af_bella"
    speed = float(args.speed) if args.speed else 1.0

    pipeline = KPipeline(lang_code=lc, repo_id=args.repo_id, device=device)
    sample_rate = int(args.sample_rate) or 24000

    chunks: list = []
    flat_tokens: list[dict] = []
    current_time = 0.0
    for result in pipeline(body, voice=voice, speed=speed, split_pattern=r"\n\n+"):
        audio = _audio_to_numpy(result.audio)
        if audio.size == 0:
            continue
        chunk_start = current_time
        chunks.append(audio)
        for tok in getattr(result, "tokens", None) or []:
            st = getattr(tok, "start_ts", None) or 0.0
            et = getattr(tok, "end_ts", None) or 0.0
            flat_tokens.append(
                {
                    "start": chunk_start + float(st or 0.0),
                    "end": chunk_start + float(et or 0.0),
                    "text": getattr(tok, "text", "") or "",
                    "whitespace": getattr(tok, "whitespace", "") or "",
                }
            )
        current_time += float(len(audio)) / float(sample_rate)

    if not chunks:
        print("Kokoro produced no audio", file=sys.stderr)
        return 3

    wav = np.clip(np.concatenate(chunks), -1.0, 1.0)
    pcm = (wav * 32767.0).astype(np.int16)
    sf.write(args.wav_out, pcm, sample_rate, subtype="PCM_16")

    with open(args.tokens_out, "w", encoding="utf-8") as fh:
        json.dump({"tokens": flat_tokens, "duration": current_time, "sample_rate": sample_rate}, fh)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
