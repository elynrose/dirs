from director_api.services.image_prompt_assembly import (
    polish_scene_image_prompt,
    polish_scene_video_prompt,
)

STYLE = (
    "STYLIZED 3D ANIMATED FILM STILL — clearly computer-generated characters and worlds "
    "(Pixar/DreamWorks-style family animation): soft rounded forms."
)

SCENE = "In a dimly lit room, Samson and Delilah are lying in bed."


def test_strip_trailing_visual_style_suffix():
    from director_api.services.image_prompt_assembly import strip_redundant_visual_style_clauses

    raw = f"{STYLE}\n\n{SCENE}\n\nVisual style: {STYLE}"
    out = strip_redundant_visual_style_clauses(raw, STYLE)
    assert "Visual style:" not in out
    assert SCENE in out


def test_polish_image_uses_structured_sections():
    out = polish_scene_image_prompt(
        f"Camera perspective: eye-level medium shot.\n\n{SCENE}",
        vis_style=STYLE,
        visual_preset_id="three_d_animation",
        mood="Tense intimacy",
    )
    assert out.startswith("Subject:")
    assert "Visual treatment:" in out
    assert "Mood: Tense intimacy" in out


def test_polish_video_uses_structured_sections():
    out = polish_scene_video_prompt(
        SCENE,
        vis_style=STYLE,
        visual_preset_id="cinematic_documentary",
    )
    assert "Subject:" in out
    assert "Motion:" in out
    assert "Photoreal documentary" in out


def test_polish_preserves_already_labeled_image_prompt():
    labeled = (
        "Subject: Victorian classroom, teacher at chalkboard with Victoria portrait.\n\n"
        "Visual treatment: Photoreal cinematic historical epic.\n\n"
        "Composition: wide elevated bird's-eye view"
    )
    out = polish_scene_image_prompt(
        labeled,
        vis_style="Photoreal cinematic historical epic",
        visual_preset_id="cinematic_historical_epic",
    )
    assert "Victorian classroom" in out
    assert "bird's-eye view" in out
    assert "Eye-level medium shot" not in out
