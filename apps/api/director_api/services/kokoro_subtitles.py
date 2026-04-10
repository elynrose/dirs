"""Build WebVTT from Kokoro token timestamps (Abogen-style sentence grouping)."""

from __future__ import annotations

import re

from director_api.services.subtitles_vtt import _escape_vtt, _fmt_ts

# Subtitle timing is reliable for Kokoro US/UK English per upstream notes.
KOKORO_SUBTITLE_LANG_CODES = frozenset({"a", "b"})


def build_sentence_cues_from_tokens(
    tokens: list[dict],
    *,
    max_subtitle_words: int = 16,
    fallback_end_time: float | None = None,
) -> list[tuple[float, float, str]]:
    """
    ``tokens`` items: start, end, text, whitespace (optional), matching Abogen's structure.
    """
    if not tokens:
        return []
    separator = r"[.!?]"
    current_sentence: list[dict] = []
    word_count = 0
    subtitle_entries: list[tuple[float, float, str]] = []

    for token in tokens:
        current_sentence.append(token)
        word_count += 1
        ws = token.get("whitespace", "") or ""
        if (re.search(separator, token["text"]) and ws == " ") or word_count >= max_subtitle_words:
            if current_sentence:
                start_time = float(current_sentence[0]["start"])
                end_time = float(current_sentence[-1]["end"])
                sentence_text = ""
                for t in current_sentence:
                    sentence_text += t["text"] + (t.get("whitespace", "") or "")
                subtitle_entries.append((start_time, end_time, sentence_text.strip()))
                current_sentence = []
                word_count = 0

    if current_sentence:
        start_time = float(current_sentence[0]["start"])
        end_time = float(current_sentence[-1]["end"])
        sentence_text = "".join(t["text"] + (t.get("whitespace", "") or "") for t in current_sentence)
        subtitle_entries.append((start_time, end_time, sentence_text.strip()))

    if subtitle_entries and fallback_end_time is not None:
        start, end, text = subtitle_entries[-1]
        if end is None or end <= start or end <= 0:
            subtitle_entries[-1] = (start, float(fallback_end_time), text)

    return subtitle_entries


def cues_to_webvtt(cues: list[tuple[float, float, str]]) -> str:
    if not cues:
        return "WEBVTT\n\n"
    lines = ["WEBVTT", ""]
    for start, end, text in cues:
        lines.append(f"{_fmt_ts(start)} --> {_fmt_ts(end)}")
        lines.append(_escape_vtt(text))
        lines.append("")
    return "\n".join(lines) + "\n"
