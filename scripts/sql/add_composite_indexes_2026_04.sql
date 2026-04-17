-- Optional manual migration for existing databases (create_all adds these for fresh installs).
-- Run against your director DB when upgrading from a schema before these indexes existed.
--
-- Note: CREATE INDEX CONCURRENTLY cannot run inside a transaction block. In psql, use one
-- statement per execution, or run with autocommit.

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_research_claims_dossier_sourced_disputed
  ON research_claims (dossier_id, adequately_sourced, disputed);

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_critic_reports_target_created
  ON critic_reports (target_type, target_id, created_at);

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_narration_tracks_project_scene_created
  ON narration_tracks (project_id, scene_id, created_at);
