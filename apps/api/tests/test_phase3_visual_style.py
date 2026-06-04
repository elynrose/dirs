"""Visual preset → scene negative prompt and preset resolution."""

from __future__ import annotations

from types import SimpleNamespace

from director_api.services import phase3 as phase3_svc
from director_api.services.phase3 import (
    _DEFAULT_SCENE_NEGATIVE_PROMPT,
    _THREE_D_ANIMATION_NEGATIVE_PROMPT,
)


def test_resolve_visual_preset_from_project():
    project = SimpleNamespace(visual_style="preset:three_d_animation")
    settings = SimpleNamespace(visual_style_preset="cinematic_documentary")
    assert phase3_svc.resolve_visual_preset_id_for_project(project, settings) == "three_d_animation"


def test_resolve_visual_preset_falls_back_to_settings():
    project = SimpleNamespace(visual_style=None)
    settings = SimpleNamespace(visual_style_preset="three_d_animation")
    assert phase3_svc.resolve_visual_preset_id_for_project(project, settings) == "three_d_animation"


def test_default_scene_negative_for_three_d():
    project = SimpleNamespace(visual_style="preset:three_d_animation")
    settings = SimpleNamespace(visual_style_preset="cinematic_documentary")
    neg = phase3_svc.default_scene_negative_prompt_for_project(project, settings)
    assert "flat 2D cel" in neg
    assert "cartoon, anime" not in neg


def test_effective_scene_negative_upgrades_stored_photoreal_default():
    project = SimpleNamespace(visual_style="preset:three_d_animation")
    settings = SimpleNamespace(visual_style_preset="cinematic_documentary")
    pp = {"negative_prompt": _DEFAULT_SCENE_NEGATIVE_PROMPT.strip()}
    got = phase3_svc.effective_scene_negative_prompt(project, settings, pp)
    assert got == _THREE_D_ANIMATION_NEGATIVE_PROMPT


def test_effective_scene_negative_keeps_custom_stored():
    project = SimpleNamespace(visual_style="preset:three_d_animation")
    settings = SimpleNamespace(visual_style_preset="cinematic_documentary")
    custom = "my custom negative"
    pp = {"negative_prompt": custom}
    got = phase3_svc.effective_scene_negative_prompt(project, settings, pp)
    assert got == custom
