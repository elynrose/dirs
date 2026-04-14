import { useEffect, useState } from "react";
import { EditorCardColumn } from "./EditorCard.jsx";
import { CriticReportIndexList } from "./CriticReportIndex.jsx";
import { agentRunAutoGenerateSceneVideos } from "../lib/constants.js";

// ---------------------------------------------------------------------------
// Per-step elapsed time
// ---------------------------------------------------------------------------

/**
 * Build a map of { stepKey → { startedAt: Date, endedAt: Date|null } }
 * from the agent run's steps_json event log.
 */
function buildStepTimingMap(stepsJson) {
  const map = new Map();
  const evs = Array.isArray(stepsJson) ? stepsJson : [];
  for (const ev of evs) {
    if (!ev?.step || !ev?.at) continue;
    const existing = map.get(ev.step);
    const ts = new Date(ev.at);
    if (!existing) {
      map.set(ev.step, { startedAt: ts, endedAt: null });
    } else if (ev.status === "succeeded" || ev.status === "failed" || ev.status === "skipped") {
      map.set(ev.step, { ...existing, endedAt: ts });
    }
  }
  return map;
}

function formatElapsed(startedAt, endedAt) {
  if (!startedAt) return null;
  const end = endedAt ?? new Date();
  const diffSec = Math.round((end - startedAt) / 1000);
  if (diffSec < 60) return `${diffSec}s`;
  const m = Math.floor(diffSec / 60);
  const s = diffSec % 60;
  return s > 0 ? `${m}m ${s}s` : `${m}m`;
}

/** Live-updating elapsed time for a running step (ticks every second). */
function LiveElapsed({ startedAt }) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  const elapsed = formatElapsed(startedAt, now);
  if (!elapsed) return null;
  return <span className="pipeline-step-timing pipeline-step-timing--live">{elapsed}</span>;
}

/** Completed step timing (static, no tick). */
function CompletedElapsed({ startedAt, endedAt }) {
  const elapsed = formatElapsed(startedAt, endedAt);
  if (!elapsed) return null;
  return <span className="pipeline-step-timing pipeline-step-timing--done">{elapsed}</span>;
}

const TIMELINE_EXPORT_ATTENTION_CODES = new Set([
  "timeline_asset_not_approved",
  "timeline_asset_not_in_project",
  "timeline_clip_not_visual_asset",
  "timeline_asset_rejected_or_failed",
  "timeline_asset_not_succeeded",
  "timeline_asset_file_missing",
]);

function issueDetailObj(iss) {
  const d = iss?.detail;
  return d && typeof d === "object" && !Array.isArray(d) ? d : {};
}

function ExportAttentionTimelineExtras({ iss, busy, phase5Ready, openSceneForTimelineAttentionAsset }) {
  const code = iss?.code;
  if (!TIMELINE_EXPORT_ATTENTION_CODES.has(code)) return null;
  const detail = issueDetailObj(iss);
  const aid = detail.asset_id;
  if (!aid) return null;
  const list = phase5Ready?.export_attention_timeline_assets;
  const row = Array.isArray(list) ? list.find((x) => String(x?.asset_id) === String(aid)) : null;
  const sceneId = row?.scene_id;
  const short = String(aid).replace(/-/g, "").slice(0, 8);
  return (
    <div className="critic-gate-timeline-attention" style={{ marginTop: 6 }}>
      {sceneId && typeof openSceneForTimelineAttentionAsset === "function" ? (
        <button
          type="button"
          className="secondary"
          disabled={busy}
          title="Opens the scene that owns this media and pins it in the canvas preview when available."
          onClick={() => void openSceneForTimelineAttentionAsset(String(aid))}
        >
          Open scene · asset …{short}
        </button>
      ) : (
        <span className="subtle mono" style={{ fontSize: "0.7rem", lineHeight: 1.4 }}>
          Timeline asset …{short} — not linked to a scene in this project; remove or replace the clip in the timeline
          version.
        </span>
      )}
    </div>
  );
}

/**
 * Right inspector column: progress, project brief (pipeline + run controls), reviews & activity.
 * Props bag `p` keeps App.jsx call sites short.
 */
export function InspectorPipelinePanel({ p }) {
  const allowFullThrough = p.entitlementFullThrough !== false;
  const allowUnattended = p.entitlementUnattended !== false;
  const showProgress = Boolean(p.projectId && Array.isArray(p.pipelineStatus?.steps));
  const stepTimingMap = buildStepTimingMap(p.run?.steps_json);
  const showReviews =
    Boolean(p.blocked) ||
    Boolean(p.run?.status === "failed" && p.failedReadinessIssues?.length > 0) ||
    Boolean(p.projectId) ||
    Boolean(p.criticReport?.report) ||
    Boolean(p.events?.length > 0);

  return (
    <section className="panel inspector-panel">
      <h2 id="studio-pipeline-panel">Pipeline &amp; agent</h2>
      <EditorCardColumn
        column="right"
        sections={[
          {
            id: "progress",
            title: "Project progress",
            show: showProgress,
            children: showProgress ? (
              <div className="pipeline-status-block pipeline-status-block--scrollable">
                <div className="mono pipeline-phase-pill">
                  phase: {p.pipelineStatus.workflow_phase || "—"} · rank {p.pipelineStatus.phase_rank ?? "—"}
                </div>
                {p.run?.status === "cancelled" ? (
                  <p className="subtle pipeline-stopped-hint" style={{ margin: "0 0 10px" }}>
                    Automation was <strong>stopped</strong>. Step states reflect saved project data; use <strong>Re-run</strong> on a row for a
                    new agent job from that phase.
                  </p>
                ) : null}
                {p.run?.status === "running" &&
                p.run?.pipeline_control_json &&
                typeof p.run.pipeline_control_json === "object" &&
                p.run.pipeline_control_json.stop_requested ? (
                  <p className="subtle pipeline-stopping-hint" style={{ margin: "0 0 10px" }}>
                    <strong>Stopping</strong> — rows no longer show live agent progress; background Studio jobs may still run.
                  </p>
                ) : null}
                {typeof p.rerunPipelineFromStep === "function" ? (
                  <p className="subtle" style={{ margin: "0 0 8px", fontSize: "0.72rem", lineHeight: 1.45 }}>
                    <strong>Re-run</strong> starts a <strong>new</strong> agent job from that phase (earlier steps are skipped when the worker considers them
                    satisfied). Rows here stay tied to <strong>project</strong> state, not the old run.
                  </p>
                ) : null}
                <ul className="pipeline-steps">
                  {p.pipelineStatus.steps.map((s) => (
                    <li key={s.id} className={`pipeline-step pipeline-step--${s.status || "pending"}`}>
                      <span className="pipeline-step-label">
                        {s.status === "running" && p.pipelineStepActivityIconClass ? (
                          <i
                            className={p.pipelineStepActivityIconClass(
                              s.id,
                              p.pipelineActivityRunStatus ?? p.run?.status,
                            )}
                            aria-hidden="true"
                          />
                        ) : null}
                        {s.label}
                      </span>
                      <span className="pipeline-step-badge">{p.friendlyPipelineStepStatus(s.status)}</span>
                      {(() => {
                        // Map pipeline step id to agent step key for timing lookup.
                        const agentKey = p.PIPELINE_STEP_ID_TO_AGENT_EFF_KEY?.[s.id] ?? s.id;
                        const timing = stepTimingMap.get(agentKey);
                        if (!timing) return null;
                        if (s.status === "running") {
                          return <LiveElapsed startedAt={timing.startedAt} />;
                        }
                        if (s.status === "done" && timing.endedAt) {
                          return <CompletedElapsed startedAt={timing.startedAt} endedAt={timing.endedAt} />;
                        }
                        return null;
                      })()}
                      {typeof p.rerunPipelineFromStep === "function" ? (
                        <button
                          type="button"
                          className="secondary pipeline-step-rerun"
                          disabled={!p.projectId || Boolean(p.pipelineRerunLocked)}
                          title="Queue a new agent run that skips earlier phases when safe and re-executes from this one. Progress above reflects project data; Run activity shows the new run."
                          onClick={() => void p.rerunPipelineFromStep(s.id)}
                        >
                          Re-run
                        </button>
                      ) : null}
                      {s.detail ? <span className="pipeline-step-detail">{s.detail}</span> : null}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null,
          },
          {
            id: "brief",
            title: "Project brief",
            info: (
              <>
                <strong>New project:</strong> fill title &amp; topic, pick Manual / Auto / Hands-off, then <strong>Start</strong>.{" "}
                <strong>Automate</strong> only runs when a project is open in the list — it continues that project, not the brief alone.
              </>
            ),
            children: (
              <>
                <label htmlFor="title">Title</label>
                <input id="title" value={p.title} onChange={(e) => p.setTitle(e.target.value)} />
                <label htmlFor="topic">Topic</label>
                <textarea id="topic" value={p.topic} onChange={(e) => p.setTopic(e.target.value)} rows={3} />
                <label htmlFor="runtime">Runtime minutes</label>
                <input id="runtime" type="number" min={5} max={120} value={p.runtime} onChange={(e) => p.setRuntime(e.target.value)} />
                <label htmlFor="frameAspect">Picture frame</label>
                <select
                  id="frameAspect"
                  value={p.frameAspectRatio === "9:16" ? "9:16" : "16:9"}
                  onChange={(e) => p.setFrameAspectRatio(e.target.value)}
                  disabled={Boolean(p.projectId)}
                  title={
                    p.projectId ?
                      "Frame is fixed for this project (set when it was created)."
                    : "16:9 landscape or 9:16 vertical — used for generated stills, scene clips, and exports."
                  }
                >
                  <option value="16:9">16:9 landscape (YouTube-style)</option>
                  <option value="9:16">9:16 portrait (shorts / Reels)</option>
                </select>
                <div className="pipeline-mode-row brief-pipeline-controls-row">
                  <span className="pipeline-mode-label">Pipeline</span>
                  <div className="segmented segmented--pipeline-three" role="group" aria-label="Pipeline mode">
                    <button
                      type="button"
                      className={p.pipelineMode === "manual" ? "active" : ""}
                      onClick={() => p.setPipelineMode("manual")}
                    >
                      Manual
                    </button>
                    <button
                      type="button"
                      className={p.pipelineMode === "auto" ? "active" : ""}
                      onClick={() => p.setPipelineMode("auto")}
                    >
                      Auto
                    </button>
                    <button
                      type="button"
                      className={p.pipelineMode === "unattended" ? "active" : ""}
                      disabled={!allowUnattended}
                      title={
                        allowUnattended
                          ? "Runs from brief through final video without stopping for strict research source counts (logged warnings only)."
                          : "Not included in your workspace plan — open Account to review access."
                      }
                      onClick={() => allowUnattended && p.setPipelineMode("unattended")}
                    >
                      Hands-off
                    </button>
                  </div>
                </div>
                {p.pipelineMode === "auto" ? (
                  <label className="pipeline-through-label brief-auto-target-label">
                    Auto target
                    <select value={allowFullThrough ? p.autoThrough : "critique"} onChange={(e) => p.setAutoThrough(e.target.value)}>
                      <option value="critique">Through story vs research review</option>
                      {allowFullThrough ? (
                        <option value="full_video">Through final video (character bible, images, TTS, timeline, cuts)</option>
                      ) : null}
                    </select>
                  </label>
                ) : null}
                {p.pipelineMode === "unattended" ? (
                  <p className="subtle brief-hands-off-hint">
                    Hands-off always targets <strong>final video</strong> (same as Auto → full video), and relaxes the research dossier source gate so
                    the worker does not block for human approval on thin sources.
                  </p>
                ) : null}
                <div className="pipeline-mode-row brief-run-automate-row">
                  {(() => {
                    const hasProject = Boolean(p.projectId);
                    const leftActive = !hasProject || p.pipelineMode === "manual";
                    const automateActive =
                      hasProject && (p.pipelineMode === "auto" || p.pipelineMode === "unattended");
                    const startLabel = !hasProject
                      ? "Start"
                      : p.pipelineMode === "manual"
                        ? "Manual Run"
                        : "New from brief";
                    const startTitle = !hasProject
                      ? "Creates a new project from the title & topic above using the selected pipeline mode (Manual, Auto, or Hands-off)."
                      : p.pipelineMode === "manual"
                        ? "Queue a new agent run from this brief (stops after chapter scripts in Manual mode)."
                        : "Queue a new agent run from this brief as a new project (does not replace the open project until the run creates it).";
                    const automateDisabledReason =
                      !hasProject
                        ? "No project selected — use Start to create one from the brief, or pick a project in the list first."
                        : p.pipelineMode !== "auto" && p.pipelineMode !== "unattended"
                          ? "Switch to Auto or Hands-off to resume automation on this project."
                          : undefined;
                    return (
                  <div
                    className="segmented segmented--run-automate"
                    role="group"
                    aria-label="Start new run from brief, automate existing project, or restart pipeline"
                  >
                    <button
                      type="button"
                      className={leftActive ? "active" : ""}
                      disabled={p.busy}
                      title={startTitle}
                      onClick={p.startAgentRun}
                    >
                      {startLabel}
                    </button>
                    <button
                      type="button"
                      className={automateActive ? "active" : ""}
                      disabled={
                        p.busy ||
                        !p.projectId ||
                        (p.pipelineMode !== "auto" && p.pipelineMode !== "unattended")
                      }
                      title={automateDisabledReason}
                      onClick={p.continuePipelineAuto}
                    >
                      Automate
                    </button>
                    <button
                      type="button"
                      disabled={
                        p.busy ||
                        !p.projectId ||
                        (p.pipelineMode !== "auto" && p.pipelineMode !== "unattended") ||
                        Boolean(p.pipelineRerunLocked)
                      }
                      title="Choose which phases to re-run (even if complete); others fast-skip when satisfied."
                      onClick={() => (typeof p.openRestartAutomationModal === "function" ? p.openRestartAutomationModal() : null)}
                    >
                      Restart…
                    </button>
                  </div>
                    );
                  })()}
                </div>
                {!p.projectId && (p.pipelineMode === "auto" || p.pipelineMode === "unattended") ? (
                  <p className="subtle brief-automate-hint-no-project" style={{ marginTop: 10, marginBottom: 0, fontSize: "0.74rem", lineHeight: 1.45 }}>
                    <strong>Automate</strong> only continues a project you already opened from the list. For a <strong>new</strong> project in Auto or
                    Hands-off, press <strong>Start</strong> — it uses the same mode and brief above.
                  </p>
                ) : null}
                {(p.pipelineMode === "auto" || p.pipelineMode === "unattended") && p.projectId ? (
                  <div
                    className="brief-automate-options-row"
                    role="group"
                    aria-label="Rewrite scenes and generate videos"
                  >
                    <label
                      className="subtle brief-automate-option"
                      title="When on, Automate replans every scripted chapter and replaces existing scene cards. Leave off to keep chapters you planned manually."
                    >
                      <input
                        type="checkbox"
                        checked={p.forceReplanScenesOnContinue}
                        onChange={(e) => p.setForceReplanScenesOnContinue(e.target.checked)}
                      />
                      <span>Rewrite scenes</span>
                    </label>
                    <label
                      className="subtle brief-automate-option"
                      title="When enabled, runs that go through final video also generate a scene video for each scene missing one (uses your workspace video provider). Saved to Settings. Default is on until you turn it off in Settings."
                    >
                      <input
                        type="checkbox"
                        checked={agentRunAutoGenerateSceneVideos(p.appConfig)}
                        disabled={Boolean(p.settingsBusy || p.busy)}
                        onChange={(e) => void p.patchWorkspaceConfig({ agent_run_auto_generate_scene_videos: e.target.checked })}
                      />
                      <span>Generate videos</span>
                    </label>
                  </div>
                ) : null}
                {p.agentRunId && p.run && typeof p.friendlyAgentRunStatus === "function" ? (
                  <p className="subtle brief-automation-run-status" style={{ marginTop: 12, marginBottom: 0 }}>
                    <strong>Current automation run:</strong> {p.friendlyAgentRunStatus(p.run)}
                    {p.run.status === "cancelled"
                      ? " — you can Re-run steps, Restart…, or Automate again."
                      : p.run.status === "running" &&
                          p.run.pipeline_control_json &&
                          typeof p.run.pipeline_control_json === "object" &&
                          p.run.pipeline_control_json.stop_requested
                        ? " — worker exits after the current step."
                        : p.run.status === "succeeded" || p.run.status === "failed" || p.run.status === "blocked"
                          ? " — project cards above refresh with the latest data."
                          : ""}
                  </p>
                ) : p.agentRunId ? (
                  <p className="subtle brief-automation-run-status" style={{ marginTop: 12, marginBottom: 0 }}>
                    <strong>Current automation run:</strong> …
                  </p>
                ) : null}
                <div className="action-row agent-run-controls-row brief-agent-run-controls">
                  <button type="button" className="secondary" disabled={!p.agentRunId} onClick={p.refreshRun}>
                    Refresh
                  </button>
                  <button
                    type="button"
                    className="secondary"
                    disabled={
                      p.busy ||
                      !p.agentRunId ||
                      p.run?.status !== "running" ||
                      Boolean(p.run?.pipeline_control_json?.paused)
                    }
                    onClick={() => p.pipelineControl("pause")}
                  >
                    Pause
                  </button>
                  <button
                    type="button"
                    className="secondary"
                    disabled={
                      p.busy ||
                      !p.agentRunId ||
                      !(p.run?.status === "paused" || (p.run?.status === "running" && p.run?.pipeline_control_json?.paused))
                    }
                    onClick={() => p.pipelineControl("resume")}
                  >
                    Resume
                  </button>
                  <button
                    type="button"
                    className="secondary"
                    disabled={p.busy || !p.agentRunId || !["running", "paused", "queued"].includes(p.run?.status)}
                    onClick={() => p.pipelineControl("stop")}
                  >
                    Stop
                  </button>
                </div>
                <p className="subtle">
                  Stop sets a flag the worker checks between steps and before long provider calls (research, story review, character
                  bible, images, video, narration). A single request already in flight to an external API may still complete; the pipeline exits
                  as soon as the worker observes the stop.
                </p>
                <div className="subtle" style={{ marginTop: 6 }}>
                  Status:{" "}
                  {typeof p.friendlyAgentRunStatus === "function"
                    ? p.friendlyAgentRunStatus(p.run)
                    : p.friendlyRunStatus(p.run?.status)}
                  {p.run?.current_step
                    ? ` · ${p.friendlyPipelineStep(p.run.current_step)}`
                    : p.pipelineBanner?.effKey
                      ? ` · ${p.pipelineBanner.stepShort || p.friendlyPipelineStep(p.pipelineBanner.effKey)}`
                      : ""}
                </div>
                {p.runStepNow || p.pipelineBanner?.detail ? (
                  <p className="subtle run-step-now">
                    <strong>Now doing:</strong> {p.runStepNow || p.pipelineBanner?.detail}
                  </p>
                ) : null}
              </>
            ),
          },
          {
            id: "projectSubtitles",
            title: "Project subtitles",
            info: (
              <>
                Subtitles are built from <strong>scene narration scripts</strong> (story order). If no scene has script text yet, chapter
                scripts are used as a fallback.
              </>
            ),
            children: (
              <>
                {p.accountProfile?.entitlements?.subtitles_enabled === false ? (
                  <p className="subtle" style={{ marginBottom: 10 }}>
                    Subtitle generation is not included in your workspace plan. Open <strong>Account</strong> to review access.
                  </p>
                ) : null}
                <div className="audio-panel-actions">
                  <button
                    type="button"
                    disabled={
                      p.busy ||
                      !p.projectId ||
                      p.accountProfile?.entitlements?.subtitles_enabled === false
                    }
                    onClick={async () => {
                      if (!p.projectId || typeof p.queueMediaJob !== "function") return;
                      await p.queueMediaJob(`/v1/projects/${p.projectId}/subtitles/generate`, {}, "Subtitles job queued…");
                    }}
                  >
                    Subtitles
                  </button>
                </div>
              </>
            ),
          },
          {
            id: "reviewsAndAlerts",
            title: "Reviews, alerts & run log",
            show: showReviews,
            children: showReviews ? (
              <>
                {p.blocked ? (
                  <div className="warn">
                    <div>Automation stopped: {p.friendlyBlockReason(p.run?.block_code)}</div>
                    {p.run?.block_code === "CRITIC_GATE" ? (
                      <p className="subtle" style={{ marginTop: 8, marginBottom: 0 }}>
                        The system already retried chapter reviews automatically (see <strong>Run activity</strong> below). Open{" "}
                        <strong>Critic reports</strong> for details, fix scripts or scenes, then press <strong>Automate</strong>. If your
                        team allows bypassing a chapter gate, an administrator can waive it; you can also raise retry limits or relax review
                        thresholds under <strong>Settings</strong>.
                      </p>
                    ) : null}
                  </div>
                ) : null}
                {p.blocked && p.run?.block_code === "CRITIC_GATE" && (p.criticGateChapterIds?.length ?? 0) > 0 ? (
                  <div className="critic-gate-fix-card">
                    <h3 style={{ marginTop: 0 }}>Which chapters failed the gate</h3>
                    <p className="subtle" style={{ marginTop: 4 }}>
                      Open a chapter to edit script/scenes in the tree, or re-queue just that chapter&apos;s critic. Scene-level problems are listed
                      under “Scenes &amp; gates” if any scenes have not passed their critic.
                    </p>
                    <ul className="critic-gate-chapter-list">
                      {(p.criticGateChapterIds || []).map((cid) => (
                        <li key={cid} className="critic-gate-chapter-row">
                          <div className="critic-gate-chapter-title">{p.chapterTitleForId(cid)}</div>
                          <div className="critic-gate-chapter-actions">
                            <button type="button" className="secondary" disabled={p.busy} onClick={() => p.goToChapterScene(cid, null)}>
                              Open chapter
                            </button>
                            <button type="button" className="secondary" disabled={p.busy} onClick={() => p.postChapterCritique(cid)}>
                              Re-run chapter critic
                            </button>
                          </div>
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
                {p.blocked &&
                p.run?.block_code === "CRITIC_GATE" &&
                Array.isArray(p.run?.block_detail_json?.failing_gates) &&
                p.run.block_detail_json.failing_gates.length > 0 ? (
                  <div className="pipeline-status-block pipeline-status-block--scrollable" style={{ marginTop: 10 }}>
                    <h3 style={{ marginTop: 0 }}>Reports for blocked chapters</h3>
                    <p className="subtle" style={{ marginTop: 4 }}>
                      <strong>View report</strong> opens the summary under <strong>Report detail</strong> below.
                    </p>
                    <ul className="critic-report-index">
                      {p.run.block_detail_json.failing_gates.map((g) => (
                        <li key={g.critic_report_id} className="critic-report-index-row">
                          <span>{p.chapterTitleForId(g.chapter_id)}</span>
                          <div className="critic-report-index-actions">
                            <button type="button" className="secondary" onClick={() => p.loadCriticReport(g.critic_report_id)}>
                              View report
                            </button>
                            {g.chapter_id ? (
                              <button
                                type="button"
                                className="secondary"
                                disabled={p.busy}
                                onClick={() => p.goToChapterScene(String(g.chapter_id), null)}
                              >
                                View chapter
                              </button>
                            ) : null}
                          </div>
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : p.blocked && p.run?.block_code === "CRITIC_GATE" && (p.criticGateChapterIds?.length ?? 0) === 0 ? (
                  <p className="subtle" style={{ marginTop: 10 }}>
                    We couldn’t link this stop to specific chapters. Try refreshing the page; if it keeps happening, restart the app services.
                  </p>
                ) : null}
                {p.blocked && p.run?.block_code === "CRITIC_GATE" && p.projectId ? (
                  <div className="critic-gate-fix-card">
                    <h3 style={{ marginTop: 0 }}>What’s blocking export</h3>
                    <p className="subtle" style={{ marginTop: 4 }}>
                      Each row is still waiting on a review or approval. <strong>Open scene</strong> / <strong>Open chapter</strong> jumps there in
                      the editor; <strong>Re-run scene critic</strong> requests another review for that scene only.
                    </p>
                    {!p.phase5Ready ? (
                      <p className="subtle">Loading…</p>
                    ) : !p.phase5Ready.issues?.length ? (
                      <p className="subtle">No checklist rows returned (try Refresh on critic reports, or reload the project).</p>
                    ) : (
                      <ul className="critic-gate-issue-list">
                        {p.phase5Ready.issues.map((iss, idx) => (
                          <li key={`${iss.code}-${iss.chapter_id || ""}-${iss.scene_id || ""}-${idx}`} className="critic-gate-issue-row">
                            <div>
                              <span className="critic-gate-issue-msg">{p.friendlyReadinessIssue(iss)}</span>
                            </div>
                            <div className="critic-gate-chapter-actions">
                              {iss.chapter_id ? (
                                <button
                                  type="button"
                                  className="secondary"
                                  disabled={p.busy}
                                  onClick={() => p.goToChapterScene(iss.chapter_id, iss.scene_id || null)}
                                >
                                  {iss.scene_id ? "Open scene" : "Open chapter"}
                                </button>
                              ) : null}
                              {iss.scene_id ? (
                                <button
                                  type="button"
                                  className="secondary"
                                  disabled={p.busy}
                                  onClick={() => p.postSceneCritique(iss.scene_id)}
                                >
                                  Re-run scene critic
                                </button>
                              ) : null}
                            </div>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                ) : null}
                {p.run?.status === "failed" && (p.failedReadinessIssues?.length ?? 0) > 0 ? (
                  <div className="critic-gate-fix-card">
                    <h3 style={{ marginTop: 0 }}>Why this run stopped</h3>
                    <p className="subtle" style={{ marginTop: 4 }}>
                      Fix each item below, then start or continue the automation again when you’re ready.
                    </p>
                    <ul className="critic-gate-issue-list">
                      {(p.failedReadinessIssues || []).map((iss, idx) => (
                        <li key={`${iss.code}-${iss.chapter_id || ""}-${iss.scene_id || ""}-${idx}`} className="critic-gate-issue-row">
                          <div>
                            <span className="critic-gate-issue-msg">{p.friendlyReadinessIssue(iss)}</span>
                            <ExportAttentionTimelineExtras
                              iss={iss}
                              busy={p.busy}
                              phase5Ready={p.phase5Ready}
                              openSceneForTimelineAttentionAsset={p.openSceneForTimelineAttentionAsset}
                            />
                            <div className="subtle" style={{ marginTop: 4 }}>
                              {iss.chapter_id ? p.chapterTitleForId(iss.chapter_id) : "—"}
                              {iss.scene_id ? ` · ${p.sceneLabelForId(iss.scene_id, iss.chapter_id || null)}` : ""}
                            </div>
                          </div>
                          <div className="critic-gate-chapter-actions">
                            {iss.chapter_id ? (
                              <button
                                type="button"
                                className="secondary"
                                disabled={p.busy}
                                onClick={() => p.goToChapterScene(iss.chapter_id, iss.scene_id || null)}
                              >
                                {iss.scene_id ? "Open scene" : "Open chapter"}
                              </button>
                            ) : null}
                            {iss.scene_id ? (
                              <button
                                type="button"
                                className="secondary"
                                disabled={p.busy}
                                onClick={() => p.postSceneCritique(iss.scene_id)}
                              >
                                Re-run scene critic
                              </button>
                            ) : null}
                            {iss.chapter_id && !iss.scene_id ? (
                              <button
                                type="button"
                                className="secondary"
                                disabled={p.busy}
                                onClick={() => p.postChapterCritique(iss.chapter_id)}
                              >
                                Re-run chapter critic
                              </button>
                            ) : null}
                          </div>
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
                {p.projectId ? (
                  <div className="pipeline-status-block pipeline-status-block--scrollable-lg">
                    <h3 style={{ margin: "0 0 8px", fontSize: "0.72rem", textTransform: "uppercase", letterSpacing: "0.06em", color: "#bdbdbd" }}>
                      Critic reports
                    </h3>
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                      <span className="subtle" style={{ margin: 0 }}>
                        Story vs research reviews and any legacy scene/chapter reports. <strong>View report</strong> opens detail below.
                      </span>
                      <button
                        type="button"
                        className="secondary"
                        disabled={p.busy}
                        onClick={() => {
                          p.loadChapters(p.projectId);
                          p.loadProjectCriticReports(p.projectId);
                        }}
                      >
                        Refresh
                      </button>
                    </div>
                    {p.criticListError ? <p className="err">{p.criticListError}</p> : null}
                    {!p.criticListError && (p.projectCriticReports?.length ?? 0) === 0 ? (
                      <p className="subtle">No reports loaded yet. Press Refresh after an agent run completes the review step.</p>
                    ) : null}
                    {(p.blockedChapterReportHints?.length ?? 0) > 0 ? (
                      <>
                        <h4
                          style={{
                            margin: "10px 0 4px",
                            fontSize: "0.72rem",
                            textTransform: "uppercase",
                            letterSpacing: "0.06em",
                            color: "#bdbdbd",
                          }}
                        >
                          Likely related to the current stop
                        </h4>
                        <CriticReportIndexList
                          reports={p.blockedChapterReportHints || []}
                          busy={p.busy}
                          criticReportTargetLabel={p.criticReportTargetLabel}
                          loadCriticReport={p.loadCriticReport}
                          goToChapterScene={p.goToChapterScene}
                          openSceneForCriticReport={p.openSceneForCriticReport}
                        />
                      </>
                    ) : null}
                    {(p.projectCriticReports?.length ?? 0) > 0 ? (
                      <>
                        <h4
                          style={{
                            margin: (p.blockedChapterReportHints?.length ?? 0) ? "12px 0 4px" : "10px 0 4px",
                            fontSize: "0.72rem",
                            textTransform: "uppercase",
                            letterSpacing: "0.06em",
                            color: "#bdbdbd",
                          }}
                        >
                          All reports (newest first)
                        </h4>
                        <CriticReportIndexList
                          reports={p.projectCriticReports || []}
                          busy={p.busy}
                          criticReportTargetLabel={p.criticReportTargetLabel}
                          loadCriticReport={p.loadCriticReport}
                          goToChapterScene={p.goToChapterScene}
                          openSceneForCriticReport={p.openSceneForCriticReport}
                        />
                      </>
                    ) : null}
                  </div>
                ) : null}
                {p.criticReport?.report ? (
                  <>
                    <h3
                      style={{
                        margin: "14px 0 8px",
                        fontSize: "0.72rem",
                        textTransform: "uppercase",
                        letterSpacing: "0.06em",
                        color: "#bdbdbd",
                      }}
                    >
                      Report detail
                    </h3>
                    <div className="action-row" style={{ flexWrap: "wrap", alignItems: "center" }}>
                      <span className="subtle">{p.criticReportTargetLabel(p.criticReport.report)}</span>
                      {p.criticReport.report.target_type === "chapter" && p.criticReport.report.target_id ? (
                        <button
                          type="button"
                          className="secondary"
                          disabled={p.busy}
                          onClick={() => p.goToChapterScene(String(p.criticReport.report.target_id), null)}
                        >
                          View chapter
                        </button>
                      ) : null}
                      {p.criticReport.report.target_type === "scene" && p.criticReport.report.target_id ? (
                        <button
                          type="button"
                          className="secondary"
                          disabled={p.busy}
                          onClick={() => void p.openSceneForCriticReport(String(p.criticReport.report.target_id))}
                        >
                          View scene
                        </button>
                      ) : null}
                    </div>
                    <p className="critic-report-summary">
                      <strong>Outcome:</strong> {p.criticReport.report.passed ? "Passed" : "Did not pass"}
                      {typeof p.criticReport.report.score === "number" ? (
                        <>
                          {" "}
                          · <strong>Score:</strong> {p.criticReport.report.score.toFixed(2)}
                        </>
                      ) : null}
                    </p>
                    {p.criticReport.report.dimensions_json && typeof p.criticReport.report.dimensions_json === "object" ? (
                      <ul className="critic-dimensions-list">
                        {Object.entries(p.criticReport.report.dimensions_json).map(([k, v]) => (
                          <li key={k}>
                            <span className="critic-dimension-name">{p.humanizeMetaKey(k)}</span>
                            <span className="critic-dimension-val">{typeof v === "number" ? v.toFixed(2) : String(v)}</span>
                          </li>
                        ))}
                      </ul>
                    ) : null}
                    {Array.isArray(p.criticReport.revision_issues) && p.criticReport.revision_issues.length > 0 ? (
                      <div className="critic-revision-notes">
                        <strong>Notes ({Math.min(6, p.criticReport.revision_issues.length)} shown)</strong>
                        <ul>
                          {p.criticReport.revision_issues.slice(0, 6).map((ri, j) => (
                            <li key={j}>
                              {typeof ri === "string"
                                ? ri
                                : [ri?.summary, ri?.message, ri?.text, ri?.detail].find((x) => typeof x === "string" && x.trim()) ||
                                  "See note in source data."}
                            </li>
                          ))}
                        </ul>
                      </div>
                    ) : null}
                  </>
                ) : null}
                {(p.events?.length ?? 0) > 0 ? (
                  <>
                    <h3
                      style={{
                        margin: "14px 0 8px",
                        fontSize: "0.72rem",
                        textTransform: "uppercase",
                        letterSpacing: "0.06em",
                        color: "#bdbdbd",
                      }}
                    >
                      Run activity
                    </h3>
                    <ul className="run-events-list run-activity-scroll">
                      {(p.events || []).slice(-30).map((ev, i) => {
                        const meta = p.friendlyEventMeta(ev);
                        return (
                          <li key={`${ev.at || ""}-${ev.step || ""}-${i}`} className={`run-event run-event--${ev.status || ""}`}>
                            <span className="run-event-step">{p.friendlyPipelineStep(ev.step)}</span>
                            <span>{p.friendlyRunStatus(ev.status)}</span>
                            {ev.at ? <span className="subtle">{String(ev.at).replace("T", " ").slice(0, 19)}</span> : null}
                            {ev.reason ? <span className="subtle">Note: {String(ev.reason)}</span> : null}
                            {meta ? <span className="subtle run-event-detail">{meta}</span> : null}
                          </li>
                        );
                      })}
                    </ul>
                  </>
                ) : null}
              </>
            ) : null,
          },
        ]}
      />
      {p.restartAutomationOpen && Array.isArray(p.restartAutomationSteps) ? (
        <div
          className="restart-automation-modal-backdrop"
          role="presentation"
          onClick={() => p.setRestartAutomationOpen?.(false)}
        >
          <div
            className="panel restart-automation-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="restart-automation-title"
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              if (e.key === "Escape") p.setRestartAutomationOpen?.(false);
            }}
          >
            <h3 id="restart-automation-title">Restart automation</h3>
            <p className="subtle" style={{ marginTop: 6 }}>
              <strong>Checked</strong> phases re-run even when already complete (and regenerate the character bible, scene images, videos, or TTS when those are checked).{" "}
              <strong>Unchecked</strong> phases fast-skip when the project already satisfies them, like <strong>Automate</strong>. Checking any media
              step switches the job to <strong>full video</strong> so timeline and exports can run afterward.
            </p>
            <label className="restart-automation-through-label">
              Target
              <select
                value={p.restartAutomationThrough || "full_video"}
                onChange={(e) => p.setRestartAutomationThrough?.(e.target.value)}
              >
                <option value="critique">Stop after story vs research review</option>
                <option value="full_video">Through final video (character bible, images, TTS, timeline, cuts)</option>
              </select>
            </label>
            <label className="restart-automation-rerun-research" style={{ display: "block", marginTop: 10 }}>
              <input
                type="checkbox"
                checked={Boolean(p.restartRerunWebResearch)}
                onChange={(e) => p.setRestartRerunWebResearch?.(e.target.checked)}
              />
              <span> Re-run web research (Tavily / sources)</span>
            </label>
            <p className="subtle" style={{ marginTop: 4 }}>
              Unchecked skips the research step when a dossier already exists (unless <strong>Research</strong> is checked above). Checked always runs research when the pipeline reaches that step.
            </p>
            <div className="restart-automation-step-grid">
              {p.restartAutomationSteps.map((s) => (
                <label key={s.key} className="restart-automation-step-item">
                  <input
                    type="checkbox"
                    checked={Boolean(p.restartAutomationForce?.[s.key])}
                    onChange={(e) =>
                      p.setRestartAutomationForce?.((prev) => ({
                        ...(prev || {}),
                        [s.key]: e.target.checked,
                      }))
                    }
                  />
                  <span>{s.label}</span>
                </label>
              ))}
            </div>
            <div className="restart-automation-modal-actions">
              <button type="button" className="secondary" onClick={() => p.setRestartAutomationOpen?.(false)}>
                Cancel
              </button>
              <button
                type="button"
                className="secondary"
                onClick={() =>
                  p.setRestartAutomationForce?.(Object.fromEntries(p.restartAutomationSteps.map((x) => [x.key, true])))
                }
              >
                Check all
              </button>
              <button
                type="button"
                className="secondary"
                onClick={() =>
                  p.setRestartAutomationForce?.(Object.fromEntries(p.restartAutomationSteps.map((x) => [x.key, false])))
                }
              >
                Clear all
              </button>
              <button type="button" disabled={p.busy} onClick={() => void p.submitRestartAutomation?.()}>
                Queue run
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
