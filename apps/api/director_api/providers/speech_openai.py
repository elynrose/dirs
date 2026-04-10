"""OpenAI text-to-speech for chapter narration (long scripts chunked + ffmpeg concat)."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from director_api.agents.openai_client import make_openai_client
from director_api.config import Settings
from ffmpeg_pipelines.probe import ffprobe_duration_seconds

# Built-in voices per OpenAI TTS docs (expand when API adds more).
OPENAI_TTS_VOICES = frozenset(
    {
        "alloy",
        "ash",
        "ballad",
        "coral",
        "echo",
        "fable",
        "onyx",
        "nova",
        "sage",
        "shimmer",
        "verse",
        "marin",
        "cedar",
    }
)
_MAX_INPUT_CHARS = 4000  # tts-1 limit is 4096


def ffmpeg_concat_mp3_demuxer(
    ffmpeg_bin: str,
    concat_list_path: Path,
    out_path: Path,
    *,
    timeout_sec: float,
) -> None:
    """Join MP3 segments listed in concat_list_path. Prefer re-encode; stream copy often fails across TTS chunks."""
    last_err = ""
    for extra in (
        ["-c:a", "libmp3lame", "-b:a", "192k"],
        ["-c:a", "libmp3lame", "-q:a", "3"],
        ["-c", "copy"],
    ):
        proc = subprocess.run(
            [
                ffmpeg_bin,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_list_path),
                *extra,
                str(out_path),
            ],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
        if proc.returncode == 0 and out_path.is_file() and out_path.stat().st_size > 64:
            return
        last_err = (proc.stderr or proc.stdout or "")[-3000:]
        try:
            out_path.unlink(missing_ok=True)
        except OSError:
            pass
    raise RuntimeError(last_err.strip() or "ffmpeg MP3 concat failed (tried libmp3lame and copy)")


def _bytes_look_like_mp3(data: bytes) -> bool:
    """Reject JSON/HTML error bodies saved as .mp3 (ffmpeg: 'Failed to find two consecutive MPEG audio frames')."""
    if not data or len(data) < 16:
        return False
    if data[:3] == b"ID3":
        return True
    # MPEG-1/2 Layer III sync: 0xFF then 0xE0–0xFF (11 sync bits)
    return data[0] == 0xFF and (data[1] & 0xE0) == 0xE0


def chunk_narration_text(text: str, max_len: int = _MAX_INPUT_CHARS) -> list[str]:
    """Split long narration into TTS-sized segments (paragraph/sentence aware)."""
    return _chunk_text(text, max_len)


def resolve_openai_tts_voice(preferred_speech_provider: str | None) -> str:
    p = (preferred_speech_provider or "").strip().lower()
    if not p or p in ("openai", "openai_tts"):
        return "alloy"
    if p in OPENAI_TTS_VOICES:
        return p
    if p.startswith("openai:"):
        v = p.split(":", 1)[1].strip().lower()
        if v in OPENAI_TTS_VOICES:
            return v
        raise ValueError(
            f"Unknown OpenAI TTS voice: {v!r}. Use one of {sorted(OPENAI_TTS_VOICES)}."
        )
    if any(x in p for x in ("fal", "piper", "azure")):
        raise ValueError(
            f"preferred_speech_provider {preferred_speech_provider!r} is not implemented. "
            f"Use openai, elevenlabs, or gemini (or an OpenAI voice id: {', '.join(sorted(OPENAI_TTS_VOICES))})."
        )
    return "alloy"


def _chunk_text(text: str, max_len: int = _MAX_INPUT_CHARS) -> list[str]:
    text = text.strip()
    if not text:
        return []
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        j = min(i + max_len, n)
        if j < n:
            cut = text.rfind("\n\n", i, j)
            if cut == -1 or cut < i + max_len // 2:
                cut = text.rfind(". ", i, j)
            if cut == -1 or cut < i + max_len // 2:
                cut = text.rfind(" ", i, j)
            if cut == -1 or cut <= i:
                cut = j
        else:
            cut = j
        piece = text[i:cut].strip()
        next_i = cut if cut > i else j
        if piece:
            out.append(piece)
        elif next_i <= i:
            next_i = min(i + max_len, n)
        i = next_i
    return out


def synthesize_chapter_narration_mp3(
    text: str,
    settings: Settings,
    *,
    voice: str,
    ffmpeg_bin: str = "ffmpeg",
    ffprobe_bin: str = "ffprobe",
    timeout_sec: float = 600.0,
) -> tuple[bytes, float]:
    if not settings.openai_api_key:
        raise ValueError("OPENAI_API_KEY is required for narration synthesis")
    model = (settings.openai_tts_model or "tts-1").strip() or "tts-1"
    chunks = _chunk_text(text)
    if not chunks:
        raise ValueError("empty narration text for TTS")

    client = make_openai_client(settings)
    ffmpeg_bin = (ffmpeg_bin or "ffmpeg").strip() or "ffmpeg"
    ffprobe_bin = (ffprobe_bin or "ffprobe").strip() or "ffprobe"

    with tempfile.TemporaryDirectory(prefix="director_tts_") as td:
        tdir = Path(td)
        part_paths: list[Path] = []
        for idx, ch in enumerate(chunks):
            resp = client.audio.speech.create(
                model=model,
                voice=voice,
                input=ch,
                response_format="mp3",
            )
            raw = resp.content
            if not raw:
                raise RuntimeError(f"OpenAI TTS returned empty audio for chunk {idx}")
            if not _bytes_look_like_mp3(raw):
                head = raw[:400].decode("utf-8", errors="replace")
                raise RuntimeError(
                    f"OpenAI TTS chunk {idx} is not valid MP3 (check API key, model {model!r}, and base URL). "
                    f"Body starts with: {head[:300]!r}"
                )
            p = tdir / f"part_{idx:04d}.mp3"
            p.write_bytes(raw)
            part_paths.append(p)

        if len(part_paths) == 1:
            merged = part_paths[0]
        else:
            lst = tdir / "concat.txt"
            lst.write_text(
                "\n".join(f"file '{p.as_posix()}'" for p in part_paths),
                encoding="utf-8",
            )
            merged = tdir / "merged.mp3"
            ffmpeg_concat_mp3_demuxer(ffmpeg_bin, lst, merged, timeout_sec=timeout_sec)

        dur = ffprobe_duration_seconds(merged, ffprobe_bin=ffprobe_bin, timeout_sec=min(120.0, timeout_sec))
        data = merged.read_bytes()
        if len(data) < 64:
            raise RuntimeError("TTS produced empty MP3")
        return data, float(dur)
