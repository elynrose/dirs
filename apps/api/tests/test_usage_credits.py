"""Director credits computation."""

from director_api.services.usage_credits import (
    CREDITS_PER_USD,
    compute_request_credits,
    credits_from_llm_cost_usd,
)


def test_credits_from_llm_cost() -> None:
    assert credits_from_llm_cost_usd(0.01) == 10.0
    assert credits_from_llm_cost_usd(1.0) == 1000.0
    assert CREDITS_PER_USD == 1000.0


def test_image_gen_credits() -> None:
    assert (
        compute_request_credits(
            provider="placeholder",
            service_type="image_gen",
            unit_type="request",
            units=1.0,
            meta={},
        )
        == 0.5
    )
    assert (
        compute_request_credits(
            provider="fal",
            service_type="image_gen",
            unit_type="request",
            units=1.0,
            meta={},
        )
        == 10.0
    )


def test_video_gen_duration() -> None:
    c = compute_request_credits(
        provider="local_ffmpeg",
        service_type="video_gen",
        unit_type="request",
        units=1.0,
        meta={"duration_sec": 10.0},
    )
    assert c == 5.0 + 10.0 * 0.25


def test_tts_chars() -> None:
    c = compute_request_credits(
        provider="openai",
        service_type="narration_tts_openai_scene",
        unit_type="tts_chars",
        units=2000.0,
        meta={},
    )
    assert c == 100.0
