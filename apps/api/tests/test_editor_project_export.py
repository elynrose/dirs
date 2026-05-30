"""Editor export (CapCut draft + OpenShot FCP XML)."""

from director_api.services.editor_project_export import (
    EditorAudioClip,
    EditorClip,
    EditorExportPlan,
    build_capcut_draft,
    build_fcpxml,
)


def _sample_plan() -> EditorExportPlan:
    return EditorExportPlan(
        project_title="Test Doc",
        width=1920,
        height=1080,
        fps=30,
        ratio_label="16:9",
        video_clips=[
            EditorClip(
                rel_media="media/001_clip.mp4",
                abs_path=__file__,  # type: ignore[arg-type]
                label="Scene 1",
                asset_type="video",
                timeline_start_sec=0.0,
                duration_sec=5.0,
                trim_start_sec=0.0,
            ),
            EditorClip(
                rel_media="media/002_still.png",
                abs_path=__file__,  # type: ignore[arg-type]
                label="Scene 2",
                asset_type="image",
                timeline_start_sec=5.0,
                duration_sec=3.0,
                trim_start_sec=0.0,
            ),
        ],
        narration_clips=[
            EditorAudioClip(
                rel_media="media/narration.mp3",
                abs_path=__file__,  # type: ignore[arg-type]
                label="Narration Scene 1",
                timeline_start_sec=0.0,
                duration_sec=4.5,
            ),
        ],
        music_clip=None,
        total_duration_sec=8.0,
    )


def test_build_fcpxml_has_sequence_and_clips():
    xml = build_fcpxml(_sample_plan())
    assert "<xmeml" in xml
    assert "Test Doc" in xml
    assert "001_clip.mp4" in xml
    assert "<clipitem>" in xml


def test_build_capcut_draft_structure():
    draft = build_capcut_draft(_sample_plan())
    assert draft["platform"]["app_source"] == "cc"
    assert draft["canvas_config"]["width"] == 1920
    assert len(draft["tracks"]) >= 1
    video_track = next(t for t in draft["tracks"] if t["type"] == "video")
    assert len(video_track["segments"]) == 2
    assert len(draft["materials"]["videos"]) == 2
    assert draft["duration"] == 8_000_000
