"""Compound indexes for hot queries (narration tracks, research claims, critic reports).

Revision ID: 019
Revises: 018
"""

from typing import Sequence, Union

from alembic import op

revision: str = "019"
down_revision: Union[str, None] = "018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NarrationTrack: list by project + scene, newest first (common export / narration paths)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_narration_tracks_proj_scene_created "
        "ON narration_tracks (project_id, scene_id, created_at DESC)"
    )
    # ResearchClaim: filter by dossier + sourcing flags in chapter script phase
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_research_claims_dossier_sourced_disputed "
        "ON research_claims (dossier_id, adequately_sourced, disputed)"
    )
    # CriticReport: latest report per target (scene/chapter critic cycles)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_critic_reports_target_created "
        "ON critic_reports (target_type, target_id, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_critic_reports_target_created")
    op.execute("DROP INDEX IF EXISTS ix_research_claims_dossier_sourced_disputed")
    op.execute("DROP INDEX IF EXISTS ix_narration_tracks_proj_scene_created")
