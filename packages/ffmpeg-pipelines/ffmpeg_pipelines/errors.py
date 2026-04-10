class FFmpegCompileError(RuntimeError):
    """Raised when FFmpeg exits non-zero or produces no output."""


def ffmpeg_cli_excerpt(
    stderr: str | None,
    stdout: str | None = None,
    *,
    max_chars: int = 8000,
) -> str:
    """FFmpeg often prints pages of per-input metadata; the real error may be at the start, not the end."""
    text = (stderr or "").strip()
    if not text:
        text = (stdout or "").strip()
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    budget = max_chars - 100
    half = budget // 2
    omitted = len(text) - budget
    return (
        f"{text[:half]}\n\n"
        f"... [{omitted} characters omitted from middle of FFmpeg log] ...\n\n"
        f"{text[-half:]}"
    )
