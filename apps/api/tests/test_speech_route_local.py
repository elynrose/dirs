"""Speech routing for optional local TTS providers."""

from director_api.config import Settings
from director_api.providers.speech_route import resolve_chatterbox_ref_to_path, resolve_speech_narration_route


def test_director_placeholder_media_does_not_force_ding_narration():
    """DIRECTOR_PLACEHOLDER_MEDIA is for cheap images; narration uses workspace speech (see speech_route)."""
    s = Settings(director_placeholder_media=True)
    p, o = resolve_speech_narration_route(None, s)
    assert p == "openai"
    assert o.get("voice") == "alloy"


def test_resolve_kokoro_default():
    s = Settings()
    p, o = resolve_speech_narration_route("kokoro", s)
    assert p == "kokoro"
    assert o["voice"] == "af_bella"
    assert o["lang_code"] == "a"


def test_resolve_kokoro_voice_suffix():
    s = Settings()
    p, o = resolve_speech_narration_route("kokoro:af_heart", s)
    assert p == "kokoro"
    assert o["voice"] == "af_heart"


def test_resolve_chatterbox_turbo_alias():
    s = Settings()
    p, o = resolve_speech_narration_route("chatterbox", s)
    assert p == "chatterbox_turbo"


def test_resolve_chatterbox_mtl_lang():
    s = Settings()
    p, o = resolve_speech_narration_route("chatterbox_mtl:es", s)
    assert p == "chatterbox_mtl"
    assert o["language_id"] == "es"


def test_resolve_chatterbox_ref_to_path_absolute(tmp_path):
    wav = tmp_path / "r.wav"
    wav.write_bytes(b"RIFF")
    p = resolve_chatterbox_ref_to_path(str(wav), storage_root=tmp_path)
    assert p == wav.resolve()


def test_kokoro_subtitles_sentence_cues():
    from director_api.services.kokoro_subtitles import build_sentence_cues_from_tokens, cues_to_webvtt

    tokens = [
        {"start": 0.0, "end": 0.2, "text": "Hi", "whitespace": " "},
        {"start": 0.2, "end": 0.5, "text": "there", "whitespace": " "},
        {"start": 0.5, "end": 0.8, "text": "Bob", "whitespace": ""},
        {"start": 0.9, "end": 1.2, "text": "Yes", "whitespace": ""},
    ]
    cues = build_sentence_cues_from_tokens(tokens, max_subtitle_words=8, fallback_end_time=2.0)
    assert len(cues) >= 1
    vtt = cues_to_webvtt(cues)
    assert vtt.startswith("WEBVTT")
    assert "-->" in vtt
