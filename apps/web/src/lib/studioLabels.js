/**
 * User-facing labels for project status, workflow phases, and pipeline runs.
 * Maps API enums to plain language (see `agent_resume.workflow_phase_rank`).
 */

export const PROJECT_STATUS_LABEL = {
  draft: "Draft",
  active: "In progress",
  published: "Published",
  archived: "Archived",
  failed: "Failed",
};

export const WORKFLOW_PHASE_LABEL = {
  draft: "Not started",
  director_ready: "Story direction",
  research_running: "Research in progress",
  research_ready: "Research ready",
  research_approved: "Research approved",
  outline_ready: "Outline ready",
  chapters_ready: "Scripts written",
  thumbnail_ready: "Thumbnail ready",
  hook_ready: "Hook ready",
  scenes_planned: "Scenes planned",
  outro_ready: "Outro ready",
  critique_review: "Story review",
  critique_complete: "Review complete",
  final_video_ready: "Video ready",
};

export function friendlyProjectStatus(status) {
  const key = String(status || "").trim().toLowerCase();
  if (PROJECT_STATUS_LABEL[key]) return PROJECT_STATUS_LABEL[key];
  if (!key) return "Draft";
  return key.replace(/_/g, " ");
}

export function friendlyWorkflowPhase(phase) {
  const key = String(phase || "").trim().toLowerCase();
  if (WORKFLOW_PHASE_LABEL[key]) return WORKFLOW_PHASE_LABEL[key];
  if (!key) return "";
  return key.replace(/_/g, " ");
}

/** One-line subtitle under project title in lists. */
export function friendlyProjectListMeta(status, workflowPhase) {
  const st = friendlyProjectStatus(status);
  const ph = friendlyWorkflowPhase(workflowPhase);
  if (!ph) return st;
  return `${st} · ${ph}`;
}

export function friendlyAgentRunStatusLabel(status) {
  const key = String(status || "").trim().toLowerCase();
  const map = {
    running: "Running",
    succeeded: "Finished",
    failed: "Failed",
    cancelled: "Stopped",
    queued: "Waiting",
    paused: "Paused",
    blocked: "Needs attention",
  };
  if (map[key]) return map[key];
  if (!key) return "—";
  return key.replace(/_/g, " ");
}

/** Chat / run sidebar: `Running · Run #abc12345` */
export function formatAgentRunSidebarLine(run) {
  const id = run?.id ? String(run.id).trim() : "";
  const short = id ? id.slice(0, 8) : "";
  const st = friendlyAgentRunStatusLabel(run?.status);
  return short ? `${st} · Run #${short}` : st;
}

/** Friendly stage line for pipeline progress header (replaces raw phase/rank pill). */
export function formatPipelineStageSummary(workflowPhase, phaseRank) {
  const ph = friendlyWorkflowPhase(workflowPhase);
  const rank = Number(phaseRank);
  if (ph && Number.isFinite(rank) && rank >= 0) {
    return `Stage: ${ph}`;
  }
  return ph ? `Stage: ${ph}` : "Stage: Not started";
}

export const PIPELINE_SPEED_SELECT_OPTIONS = [
  { value: "standard", label: "Standard" },
  { value: "demo_fast", label: "Quick demo (images only)" },
  { value: "production_heavy", label: "High quality (more media)" },
];
