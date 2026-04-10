"""Ken Burns filter strings (no ffmpeg required)."""

from ffmpeg_pipelines.ken_burns import build_slow_zoom_vf


def test_build_slow_zoom_vf_linear_in_contains_expected_terms() -> None:
    vf = build_slow_zoom_vf(
        width=1280,
        height=720,
        fps=30,
        duration_sec=1.0,
        zoom_frac=0.05,
        easing="linear",
        direction="in",
    )
    assert "zoompan=" in vf
    assert "1+0.05000000*on/" in vf
    assert "iw/2-(iw/zoom/2)" in vf
    assert "s=1280x720" in vf


def test_build_slow_zoom_vf_smooth_in_uses_smoothstep() -> None:
    vf = build_slow_zoom_vf(
        width=640,
        height=360,
        fps=25,
        duration_sec=0.5,
        zoom_frac=0.07,
        easing="smooth",
        direction="in",
    )
    assert "pow(on/" in vf
    assert "3*pow(" in vf and "-2*pow(" in vf


def test_build_slow_zoom_vf_smooth_out_subtracts_term() -> None:
    vf = build_slow_zoom_vf(
        width=320,
        height=240,
        fps=15,
        duration_sec=0.4,
        zoom_frac=0.06,
        easing="smooth",
        direction="out",
    )
    assert "(1+0.06000000)-0.06000000*" in vf
