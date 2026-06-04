"""Tests for topic-specific narrative arc (anti-template)."""

from __future__ import annotations

from types import SimpleNamespace

from director_api.services import phase2 as phase2_svc


def test_build_director_pack_seed_is_not_three_act_template() -> None:
    p = SimpleNamespace(
        title="Daniel",
        topic="The bible story of Daniel",
        tone="documentary",
        visual_style="preset:three_d_animation",
        narration_style="preset:warm_human_interest",
        target_runtime_minutes=5,
        factual_strictness=None,
        audience="general",
    )
    pack = phase2_svc.build_director_pack_from_project(p)  # type: ignore[arg-type]
    joined = " ".join(pack["narrative_arc"]).lower()
    assert "act i — establish the world" not in joined
    assert "daniel" in joined


def test_detects_legacy_three_act_arc() -> None:
    arc = [
        "Act I — Establish the world and stakes",
        "Act II — Develop evidence and tension",
        "Act III — Resolution and perspective",
    ]
    assert phase2_svc.narrative_arc_looks_generic(arc) is True


def test_topic_fallback_is_distinct() -> None:
    arc = phase2_svc.topic_narrative_arc_fallback(topic="Napoleon at Waterloo", title="Waterloo")
    assert len(arc) >= 4
    assert phase2_svc.narrative_arc_looks_generic(arc) is False
    assert "Waterloo" in " ".join(arc) or "Napoleon" in " ".join(arc)


def test_normalized_director_pack_replaces_generic_stored_arc() -> None:
    p = SimpleNamespace(
        title="Sampson",
        topic="Sampson and Delilah",
        director_output_json={
            "schema_id": "director-pack/v1",
            "title": "Sampson",
            "topic": "Sampson and Delilah",
            "narrative_arc": [
                "Act I — Establish the world and stakes",
                "Act II — Develop evidence and tension",
                "Act III — Resolution and perspective",
            ],
            "style_notes": {},
            "production_constraints": {},
        },
    )
    out = phase2_svc.normalized_director_pack(p)  # type: ignore[arg-type]
    assert phase2_svc.narrative_arc_looks_generic(out["narrative_arc"]) is False
