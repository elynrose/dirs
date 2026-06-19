import { PHASE5_TIMELINE_UUID_RE } from "../constants.js";

/**
 * Worker preflight uses the same timeline id string. Using rough_cut avoids final-cut-only issues
 * (e.g. missing prior MP4) while debugging a first render.
 */
export function buildPhase5ReadinessFetchOpts(pipelineMode, timelineVersionIdRaw, exportStage = "rough_cut") {
  const allowUnapprovedMedia = pipelineMode === "unattended";
  const raw = String(timelineVersionIdRaw || "").trim();
  const tv = PHASE5_TIMELINE_UUID_RE.test(raw) ? raw : null;
  return {
    allowUnapprovedMedia,
    ...(tv ? { timelineVersionId: tv, exportStage } : {}),
  };
}

/** User-facing text for export-readiness rows (hides raw API codes). */
export function friendlyReadinessIssue(iss) {
  if (!iss || typeof iss !== "object") return "Something still needs attention before export.";
  if (iss.code === "CHAPTER_GATE") {
    return "This chapter’s review gate isn’t cleared yet. Run a chapter review, or ask an admin to waive the gate if your workflow allows.";
  }
  if (iss.code === "SCENE_CRITIC") {
    return "This scene hasn’t passed its review yet (or needs a waiver).";
  }
  if (iss.code === "missing_scene_narration") {
    return "One or more scenes have script (VO) text but no synthesized scene audio yet — run scene VO or Automate (per-scene narration).";
  }
  if (iss.code === "scene_narration_audio_missing_on_disk") {
    return "A scene narration file is missing on disk — regenerate scene VO for that scene.";
  }
  if (iss.code === "missing_approved_scene_image") {
    return "Every scene needs at least one approved, succeeded image or video (not only audio). Approve the row you use on the timeline — highlighted scenes still need that.";
  }
  if (iss.code === "missing_succeeded_scene_image") {
    return "One or more scenes have no succeeded image or video yet — generate or fix media on those scenes before export.";
  }
  if (iss.code === "timeline_asset_not_approved") {
    return "The timeline uses media that isn’t approved yet — approve those assets or pick approved clips.";
  }
  if (iss.code === "timeline_asset_not_in_project") {
    return "The timeline references an unknown asset ID or one from another tenant — fix the clip, or ensure that asset exists in this workspace.";
  }
  if (iss.code === "timeline_clip_not_visual_asset") {
    return "This timeline clip points at a row that isn’t an image or video (wrong asset type). Reconcile tries to swap in scene media; otherwise fix the clip or regenerate.";
  }
  if (iss.code === "timeline_asset_rejected_or_failed") {
    return "The timeline still points at this exact asset row (check the asset id). Approving a different video in the gallery does not change the clip — click Approve on this row, or use Reconcile timeline clips to swap in current scene media.";
  }
  if (iss.code === "timeline_asset_not_succeeded") {
    return "Legacy checklist code — refresh readiness; if you still see this, tell the team.";
  }
  if (iss.code === "timeline_asset_file_missing") {
    return "A timeline media file is missing on disk — regenerate that asset or fix storage.";
  }
  const raw = (iss.message || "").trim();
  if (raw.toLowerCase().includes("post ") || raw.includes("POST ")) {
    return "This item still needs a review or approval before you can export.";
  }
  return raw || "Something still needs attention before export.";
}

/** Rough/final/export job errors: if set, show approval gate dialog instead of raw log text. */
export function parsePhase5GateModalPayload(errorMessage, jobResult) {
  const knownCodes = [
    "missing_approved_scene_image",
    "missing_succeeded_scene_image",
    "timeline_asset_not_approved",
    "timeline_asset_not_in_project",
    "timeline_clip_not_visual_asset",
    "timeline_asset_rejected_or_failed",
    "timeline_asset_not_succeeded",
    "timeline_asset_file_missing",
    "timeline_empty_clips",
    "invalid_timeline_json",
  ];
  const gate =
    jobResult && typeof jobResult === "object" && jobResult.phase5_gate && typeof jobResult.phase5_gate === "object"
      ? jobResult.phase5_gate
      : null;
  if (gate) {
    const label = String(gate.code || "");
    if (label !== "PHASE5_NOT_READY" && label !== "AUTO_ROUGH_NOT_READY") {
      return null;
    }
    const codes = new Set();
    const issues = Array.isArray(gate.issues) ? gate.issues : [];
    for (const it of issues) {
      if (it && typeof it.code === "string" && it.code) codes.add(it.code);
    }
    const approvalRelated = ["missing_approved_scene_image", "timeline_asset_not_approved"];
    const offerBulkApprove = approvalRelated.some((c) => codes.has(c));
    const summaryBullets = [];
    for (const code of knownCodes) {
      if (codes.has(code)) summaryBullets.push(friendlyReadinessIssue({ code }));
    }
    for (const c of codes) {
      if (knownCodes.includes(c)) continue;
      if (c === "export_preflight_missing_context") continue;
      summaryBullets.push(friendlyReadinessIssue({ code: c, message: c.replace(/_/g, " ") }));
    }
    return {
      offerBulkApprove,
      summaryBullets: summaryBullets.slice(0, 12),
    };
  }
  const t = String(errorMessage || "");
  if (!/\bPHASE5_NOT_READY\b/.test(t) && !/\bAUTO_ROUGH_NOT_READY\b/.test(t)) {
    return null;
  }
  const codes = new Set();
  const re = /[•\u2022\-]\s*([a-z0-9_]+)\s*:/gi;
  let m;
  while ((m = re.exec(t)) !== null) {
    codes.add(m[1]);
  }
  for (const c of knownCodes) {
    if (t.includes(c)) codes.add(c);
  }
  const approvalRelated = ["missing_approved_scene_image", "timeline_asset_not_approved"];
  const offerBulkApprove = approvalRelated.some((c) => codes.has(c));
  const bulletOrder = knownCodes;
  const summaryBullets = [];
  for (const code of bulletOrder) {
    if (codes.has(code)) summaryBullets.push(friendlyReadinessIssue({ code }));
  }
  for (const c of codes) {
    if (bulletOrder.includes(c)) continue;
    if (c === "export_preflight_missing_context") continue;
    summaryBullets.push(friendlyReadinessIssue({ code: c, message: c.replace(/_/g, " ") }));
  }
  return {
    offerBulkApprove,
    summaryBullets: summaryBullets.slice(0, 12),
  };
}

export function pipelineStatusPollSnapshot(d) {
  if (!d || typeof d !== "object") return "";
  const steps = Array.isArray(d.steps) ? d.steps : [];
  const issues = Array.isArray(d.phase5_issues) ? d.phase5_issues : [];
  return JSON.stringify({
    wf: String(d.workflow_phase ?? ""),
    pr: d.phase_rank,
    cc: d.chapter_count,
    sc: d.scene_count,
    p5: Boolean(d.phase5_ready),
    p5n: issues.length,
    p5h: issues
      .slice(0, 12)
      .map((x) => (x && typeof x === "object" ? String(x.code ?? x.type ?? x.id ?? "") : ""))
      .join("|"),
    lid: String(d.latest_timeline_version_id ?? ""),
    st: steps
      .map((s) => `${String(s?.id ?? "")}:${String(s?.status ?? "")}:${String(s?.detail ?? "")}`)
      .join(";"),
  });
}
