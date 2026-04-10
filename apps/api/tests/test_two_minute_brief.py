"""Brief / project accepts 2-minute target runtime (short demos)."""

from director_api.api.schemas.project import ProjectCreate


def test_project_create_accepts_two_minute_runtime():
    p = ProjectCreate(
        title="2m smoke",
        topic="A very short test topic for a two-minute target.",
        target_runtime_minutes=2,
    )
    assert p.target_runtime_minutes == 2
