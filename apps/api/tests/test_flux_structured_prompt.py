from director_api.services.flux_structured_prompt import (
    build_flux_structured_prompt,
    structure_flux_scene_prompt,
)

SCENE = (
    "In a dimly lit bedroom, Samson and Delilah lie close together, tension and intimacy in their expressions."
)


def test_three_d_structured_sections():
    out = build_flux_structured_prompt(
        subject=SCENE,
        visual_preset_id="three_d_animation",
        composition="Low-angle shot looking up, subtle dramatic scale",
        environment="Ancient Levant, private chamber",
        mood="Tense intimacy",
    )
    assert out.startswith("Subject:")
    assert "Visual treatment:" in out
    assert "Pixar-inspired 3D CGI" in out
    assert "Environment:" in out
    assert "Composition:" in out
    assert "Lighting:" in out
    assert "Rendering:" in out
    assert "Mood:" in out
    assert "NOT 2D hand-drawn cel" in out


def test_documentary_structured_sections():
    out = build_flux_structured_prompt(
        subject="Archaeologist examines pottery shards at a dig site.",
        visual_preset_id="cinematic_documentary",
    )
    assert "Photoreal documentary" in out
    assert "NOT illustration" in out


def test_hand_drawn_2d_structured():
    out = build_flux_structured_prompt(
        subject="Young shepherd with staff on a hillside.",
        visual_preset_id="hand_drawn_2d",
    )
    assert "Hand-drawn 2D" in out
    assert "NOT 3D CGI" in out


def test_video_includes_motion():
    out = build_flux_structured_prompt(
        subject=SCENE,
        visual_preset_id="three_d_animation",
        for_video=True,
    )
    assert "Motion:" in out


def test_structure_parses_legacy_loose_prompt():
    loose = (
        "STYLIZED 3D ANIMATED FILM STILL — Pixar style.\n\n"
        "Camera perspective: low-angle shot looking up.\n\n"
        f"{SCENE}\n\n"
        "Samson: muscular, long hair || Delilah: slender, dark hair\n\n"
        "| Setting: Samson and Delilah"
    )
    out = structure_flux_scene_prompt(
        loose,
        visual_preset_id="three_d_animation",
        for_video=False,
    )
    assert "Subject:" in out
    assert SCENE[:40] in out
    assert "Samson:" in out
    assert "low-angle" in out.lower()
    assert "Samson and Delilah" in out
    assert out.count("STYLIZED 3D") == 0  # style block replaced by Visual treatment section
