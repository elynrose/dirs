import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, apiCompiledVideoUrl } from "../lib/api.js";
import { parseJson, apiErrorMessage, formatUserFacingError } from "../lib/apiHelpers.js";
import {
  DEFAULT_NARRATION_PRESET_ID,
  RUN_STEP_LABEL,
  sceneAutomationMediaPipelineOptions,
  briefPreferredMediaProvidersFromAppConfig,
} from "../lib/constants.js";

const CHAT_RUN_STORAGE_PREFIX = "director_chat_agent_run:";
const CHAT_STUDIO_STATE_PREFIX = "director_chat_studio_state:";
const CHAT_STUDIO_LAST_PROJECT_KEY = "director_chat_studio_last_project_id";
const CHAT_STUDIO_STATE_VERSION = 1;

function storageKeyForProject(projectId) {
  return `${CHAT_RUN_STORAGE_PREFIX}${projectId}`;
}

function chatStudioStateKey(projectId) {
  return `${CHAT_STUDIO_STATE_PREFIX}${projectId}`;
}

/** Drop bulky step payloads from localStorage (lines are enough for UI). */
function messageForStorage(m) {
  if (!m || typeof m !== "object") return m;
  const { raw: _r, ...rest } = m;
  return rest;
}

function maxSuffixFromIds(rows, letter) {
  let max = 0;
  const re = new RegExp(`^${letter}-(\\d+)$`);
  for (const row of rows || []) {
    const id = String(row?.id || "");
    const mm = id.match(re);
    if (mm) max = Math.max(max, parseInt(mm[1], 10));
  }
  return max;
}

function readChatStudioPersistedState(projectId) {
  try {
    const raw = localStorage.getItem(chatStudioStateKey(projectId));
    if (!raw) return null;
    const data = JSON.parse(raw);
    if (data.v !== CHAT_STUDIO_STATE_VERSION || !Array.isArray(data.setupMessages)) return null;
    return data;
  } catch {
    return null;
  }
}

function writeChatStudioPersistedState(projectId, payload) {
  try {
    localStorage.setItem(chatStudioStateKey(projectId), JSON.stringify(payload));
  } catch {
    /* quota or private mode */
  }
}

function friendlyStepLine(ev) {
  if (!ev || typeof ev !== "object") return "Update";
  const step = ev.step != null ? String(ev.step) : "pipeline";
  const status = ev.status != null ? String(ev.status) : "";
  const label = RUN_STEP_LABEL[step] || step.replace(/_/g, " ");
  if (status === "running") return `${label}…`;
  if (status === "succeeded") return `${label} — done`;
  if (status === "failed") return `${label} — failed`;
  if (status === "blocked") return `${label} — needs attention`;
  if (status === "retry") return `${label} — retrying`;
  if (status === "skipped") return `${label} — skipped`;
  if (status === "cancelled") return `${label} — stopped`;
  return `${label}${status ? ` — ${status}` : ""}`;
}

const AGENT_RUN_TERMINAL_STATUSES = new Set(["succeeded", "failed", "cancelled", "blocked"]);

/** If non-terminal and ``updated_at`` is older than this, show “idle?” (worker likely not advancing). */
const AGENT_RUN_SIDEBAR_STALE_MS = 4 * 60 * 1000;

function isAgentRunTerminalStatus(st) {
  return AGENT_RUN_TERMINAL_STATUSES.has(String(st || "").trim());
}

function agentRunSidebarLabel(run) {
  const st = String(run?.status ?? "").trim() || "—";
  if (isAgentRunTerminalStatus(st)) return { line: st, stale: false, rowTitle: undefined };
  const raw = run?.updated_at;
  const t = raw ? new Date(raw).getTime() : NaN;
  const idleMs = Number.isFinite(t) ? Date.now() - t : 0;
  const stale = idleMs > AGENT_RUN_SIDEBAR_STALE_MS;
  return {
    line: stale ? `${st} · idle?` : st,
    stale,
    rowTitle: stale
      ? "No server updates for several minutes — this run may be stuck (worker stopped or task lost). Try Stop or restart the worker."
      : undefined,
  };
}

/**
 * Hands-off-only Studio page: project list + setup guide chat + chat-style progress for autonomous runs.
 *
 * @param {string} [studioProjectId]  Main Studio rail’s open project — kept in sync when switching Editor ↔ Chat.
 * @param {(id: string) => void} [onStudioProjectOpen]  When the user picks a production here (or one is created), notify App so ``projectId`` matches everywhere.
 */
export function ChatStudioPage({
  appConfig,
  stylePresets,
  projects,
  onReloadProjects,
  studioProjectId = "",
  onStudioProjectOpen,
}) {
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [title, setTitle] = useState("");
  const [topic, setTopic] = useState("");
  const [runtime, setRuntime] = useState(10);
  const [frameAspectRatio, setFrameAspectRatio] = useState("16:9");
  const [clipFrameFit, setClipFrameFit] = useState("center_crop");
  /** Per-production overrides; empty string = fall back to workspace defaults in `buildBriefPayload`. */
  const [narrationStyleRef, setNarrationStyleRef] = useState("");
  const [visualStyleRef, setVisualStyleRef] = useState("");
  const [audience, setAudience] = useState("general");
  const [tone, setTone] = useState("documentary");
  const [factualStrictness, setFactualStrictness] = useState(null);
  const [musicPreference, setMusicPreference] = useState("");
  const [researchMinSources, setResearchMinSources] = useState("");
  const [setupMessages, setSetupMessages] = useState([]);
  const [setupInput, setSetupInput] = useState("");
  const [setupBusy, setSetupBusy] = useState(false);
  const [setupErr, setSetupErr] = useState("");
  const [pendingCharacterDrafts, setPendingCharacterDrafts] = useState([]);
  const [messages, setMessages] = useState([]);
  const [agentRunId, setAgentRunId] = useState("");
  const [runStatus, setRunStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [timelineVersionId, setTimelineVersionId] = useState("");
  const [finalVideoReady, setFinalVideoReady] = useState(false);
  /** Bumped after project load so persist effect runs once skipPersistRef clears. */
  const [persistVersion, setPersistVersion] = useState(0);
  const [agentRunRows, setAgentRunRows] = useState([]);
  const [agentRunsLoading, setAgentRunsLoading] = useState(false);
  const [agentRunsListTick, setAgentRunsListTick] = useState(0);
  /** Which run id is currently performing stop/delete from the sidebar list. */
  const [runListActionId, setRunListActionId] = useState("");

  const lastStepsLenRef = useRef(0);
  const messageIdRef = useRef(0);
  const setupMsgIdRef = useRef(0);
  const setupThreadRef = useRef(null);
  const doneAnnouncedRef = useRef(false);
  /** Skip persisting while switching projects so we don't clobber saved transcript with empty state. */
  const skipPersistRef = useRef(false);

  const appendMessage = useCallback((role, text, extra = {}) => {
    const id = `m-${++messageIdRef.current}`;
    setMessages((prev) => [...prev, { id, role, text, ...extra }]);
  }, []);

  const resetThread = useCallback(() => {
    lastStepsLenRef.current = 0;
    doneAnnouncedRef.current = false;
    setMessages([]);
    setSetupMessages([]);
    setSetupInput("");
    setSetupErr("");
    setPendingCharacterDrafts([]);
    setAgentRunId("");
    setRunStatus("");
    setTimelineVersionId("");
    setFinalVideoReady(false);
    setError("");
  }, []);

  const loadProjectIntoComposer = useCallback(
    async (pid) => {
      if (!pid) {
        setSelectedProjectId("");
        setTitle("");
        setTopic("");
        setRuntime(10);
        setFrameAspectRatio("16:9");
        setClipFrameFit("center_crop");
        setNarrationStyleRef("");
        setVisualStyleRef("");
        setAudience("general");
        setTone("documentary");
        setFactualStrictness(null);
        setMusicPreference("");
        setResearchMinSources("");
        resetThread();
        return;
      }
      skipPersistRef.current = true;
      setSelectedProjectId(pid);
      resetThread();
      try {
        const r = await api(`/v1/projects/${encodeURIComponent(pid)}`);
        const b = await parseJson(r);
        if (!r.ok) throw new Error(apiErrorMessage(b) || `HTTP ${r.status}`);
        const p = b.data;
        setTitle(String(p.title || ""));
        setTopic(String(p.topic || ""));
        setRuntime(Number(p.target_runtime_minutes) || 10);
        setFrameAspectRatio(p.frame_aspect_ratio === "9:16" ? "9:16" : "16:9");
        setClipFrameFit(p.clip_frame_fit === "letterbox" ? "letterbox" : "center_crop");
        setNarrationStyleRef(p.narration_style != null ? String(p.narration_style) : "");
        setVisualStyleRef(p.visual_style != null ? String(p.visual_style) : "");
        setAudience(p.audience != null && String(p.audience).trim() ? String(p.audience) : "general");
        setTone(p.tone != null && String(p.tone).trim() ? String(p.tone) : "documentary");
        setFactualStrictness(
          p.factual_strictness === "strict" || p.factual_strictness === "balanced" || p.factual_strictness === "creative" ?
            p.factual_strictness
          : null,
        );
        setMusicPreference(p.music_preference != null ? String(p.music_preference) : "");
        setResearchMinSources(
          p.research_min_sources != null && p.research_min_sources !== "" ? Number(p.research_min_sources) : "",
        );
        const stored = (() => {
          try {
            return localStorage.getItem(storageKeyForProject(pid)) || "";
          } catch {
            return "";
          }
        })();
        const lsRun = stored && /^[0-9a-f-]{36}$/i.test(stored.trim()) ? stored.trim() : "";

        const persisted = readChatStudioPersistedState(pid);
        const blobRun =
          persisted?.agentRunId && /^[0-9a-f-]{36}$/i.test(String(persisted.agentRunId).trim()) ?
            String(persisted.agentRunId).trim()
          : "";

        if (persisted) {
          setSetupMessages(Array.isArray(persisted.setupMessages) ? persisted.setupMessages : []);
          if (typeof persisted.setupInput === "string") setSetupInput(persisted.setupInput || "");
          setupMsgIdRef.current =
            Number.isFinite(Number(persisted.setupMsgIdSeq)) && Number(persisted.setupMsgIdSeq) > 0 ?
              Number(persisted.setupMsgIdSeq)
            : maxSuffixFromIds(persisted.setupMessages, "s");
          setMessages(Array.isArray(persisted.messages) ? persisted.messages : []);
          if (Array.isArray(persisted.pendingCharacterDrafts) && persisted.pendingCharacterDrafts.length > 0) {
            setPendingCharacterDrafts(persisted.pendingCharacterDrafts);
          }
          lastStepsLenRef.current =
            Number.isFinite(Number(persisted.lastStepsLen)) && Number(persisted.lastStepsLen) >= 0 ?
              Number(persisted.lastStepsLen)
            : 0;
          doneAnnouncedRef.current = Boolean(persisted.doneAnnounced);
          messageIdRef.current =
            Number.isFinite(Number(persisted.messageIdSeq)) && Number(persisted.messageIdSeq) > 0 ?
              Number(persisted.messageIdSeq)
            : maxSuffixFromIds(persisted.messages, "m");
          if (typeof persisted.runStatus === "string" && persisted.runStatus) setRunStatus(persisted.runStatus);
          if (typeof persisted.timelineVersionId === "string" && persisted.timelineVersionId) {
            setTimelineVersionId(persisted.timelineVersionId);
          }
          if (typeof persisted.finalVideoReady === "boolean") setFinalVideoReady(persisted.finalVideoReady);
        }

        const effectiveRun = blobRun || lsRun;
        if (effectiveRun) {
          setAgentRunId(effectiveRun);
          if (effectiveRun !== lsRun) {
            try {
              localStorage.setItem(storageKeyForProject(pid), effectiveRun);
            } catch {
              /* ignore */
            }
          }
        }

        try {
          localStorage.setItem(CHAT_STUDIO_LAST_PROJECT_KEY, pid);
        } catch {
          /* ignore */
        }
      } catch (e) {
        setError(formatUserFacingError(e));
      } finally {
        queueMicrotask(() => {
          skipPersistRef.current = false;
          setPersistVersion((n) => n + 1);
        });
      }
    },
    [resetThread],
  );

  const pollPipeline = useCallback(async (pid) => {
    if (!pid) return;
    try {
      const r = await api(`/v1/projects/${encodeURIComponent(pid)}/pipeline-status`);
      const b = await parseJson(r);
      if (!r.ok || !b.data) return;
      const lid = b.data.latest_timeline_version_id;
      if (lid) setTimelineVersionId(String(lid));
      const steps = Array.isArray(b.data.steps) ? b.data.steps : [];
      const fc = steps.find((s) => s && s.id === "final_cut");
      setFinalVideoReady(Boolean(fc && fc.status === "done" && lid));
    } catch {
      /* ignore */
    }
  }, []);

  const loadAgentRunIntoPanel = useCallback(
    async (runId) => {
      const rid = String(runId || "").trim();
      if (!rid || !selectedProjectId) return;
      skipPersistRef.current = true;
      setError("");
      try {
        const r = await api(`/v1/agent-runs/${encodeURIComponent(rid)}`);
        const b = await parseJson(r);
        if (!r.ok) throw new Error(apiErrorMessage(b) || `HTTP ${r.status}`);
        const row = b.data;
        const steps = Array.isArray(row.steps_json) ? row.steps_json : [];
        messageIdRef.current = 0;
        const built = steps.map((ev) => ({
          id: `m-${++messageIdRef.current}`,
          role: "assistant",
          text: friendlyStepLine(ev),
          kind: "step",
          raw: ev,
        }));
        lastStepsLenRef.current = steps.length;
        const st = String(row.status || "");
        doneAnnouncedRef.current = st === "succeeded";
        if (st === "succeeded") {
          built.push({
            id: `m-${++messageIdRef.current}`,
            role: "assistant",
            text:
              "Pipeline run finished. If the final cut step completed, you can play or download the video below.",
            kind: "done",
          });
        }
        setMessages(built);
        setAgentRunId(rid);
        setRunStatus(st);
        try {
          localStorage.setItem(storageKeyForProject(selectedProjectId), rid);
        } catch {
          /* ignore */
        }
        await pollPipeline(selectedProjectId);
      } catch (e) {
        setError(formatUserFacingError(e));
      } finally {
        queueMicrotask(() => {
          skipPersistRef.current = false;
          setPersistVersion((n) => n + 1);
        });
      }
    },
    [selectedProjectId, pollPipeline],
  );

  const stopAgentRunInList = useCallback(
    async (runId) => {
      const id = String(runId || "").trim();
      if (!id) return;
      setRunListActionId(id);
      setError("");
      try {
        const r = await api(`/v1/agent-runs/${encodeURIComponent(id)}/control`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ action: "stop" }),
        });
        const b = await parseJson(r);
        if (!r.ok) throw new Error(apiErrorMessage(b) || `HTTP ${r.status}`);
        if (String(agentRunId) === id) {
          appendMessage("assistant", "Stop requested — worker will cancel when safe.", { kind: "system" });
        }
        setAgentRunsListTick((n) => n + 1);
      } catch (e) {
        setError(formatUserFacingError(e));
      } finally {
        setRunListActionId("");
      }
    },
    [agentRunId, appendMessage],
  );

  const deleteAgentRunInList = useCallback(
    async (runId) => {
      const id = String(runId || "").trim();
      if (!id) return;
      setRunListActionId(id);
      setError("");
      try {
        const r = await api(`/v1/agent-runs/${encodeURIComponent(id)}`, { method: "DELETE" });
        if (!r.ok) {
          const b = await parseJson(r);
          throw new Error(apiErrorMessage(b) || `HTTP ${r.status}`);
        }
        if (String(agentRunId) === id) {
          lastStepsLenRef.current = 0;
          doneAnnouncedRef.current = false;
          setAgentRunId("");
          setRunStatus("");
          setMessages([]);
          appendMessage("assistant", "Run deleted. Generate a new run or pick another from the list.", { kind: "system" });
          try {
            if (selectedProjectId) localStorage.removeItem(storageKeyForProject(selectedProjectId));
          } catch {
            /* ignore */
          }
        }
        setAgentRunsListTick((n) => n + 1);
      } catch (e) {
        setError(formatUserFacingError(e));
      } finally {
        setRunListActionId("");
      }
    },
    [agentRunId, appendMessage, selectedProjectId],
  );

  useEffect(() => {
    if (!agentRunId) return;
    let cancelled = false;
    const tick = async () => {
      const r = await api(`/v1/agent-runs/${encodeURIComponent(agentRunId)}`);
      const b = await parseJson(r);
      if (cancelled || !r.ok) return;
      const row = b.data;
      setRunStatus(String(row.status || ""));
      const steps = Array.isArray(row.steps_json) ? row.steps_json : [];
      const prev = lastStepsLenRef.current;
      if (steps.length > prev) {
        const chunk = steps.slice(prev);
        lastStepsLenRef.current = steps.length;
        chunk.forEach((ev) => {
          const line = friendlyStepLine(ev);
          appendMessage("assistant", line, { kind: "step", raw: ev });
        });
      }
      if (row.status === "succeeded" && !doneAnnouncedRef.current) {
        doneAnnouncedRef.current = true;
        appendMessage(
          "assistant",
          "Pipeline run finished. If the final cut step completed, you can play or download the video below.",
          { kind: "done" },
        );
      }
      if (row.project_id && selectedProjectId === String(row.project_id)) {
        try {
          localStorage.setItem(storageKeyForProject(String(row.project_id)), String(agentRunId));
        } catch {
          /* ignore */
        }
      }
    };
    void tick();
    const id = setInterval(() => void tick(), 2500);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [agentRunId, appendMessage, selectedProjectId]);

  useEffect(() => {
    if (!selectedProjectId) return;
    void pollPipeline(selectedProjectId);
    const id = setInterval(() => void pollPipeline(selectedProjectId), 4000);
    return () => clearInterval(id);
  }, [selectedProjectId, pollPipeline]);

  const agentRunSidebarFetchPidRef = useRef("");

  const fetchAgentRunSidebar = useCallback(async (projectId, withSpinner) => {
    const pid = String(projectId || "").trim();
    if (!pid) return;
    if (withSpinner) setAgentRunsLoading(true);
    try {
      const r = await api(`/v1/projects/${encodeURIComponent(pid)}/agent-runs?limit=100&offset=0`);
      const b = await parseJson(r);
      if (!r.ok) {
        setAgentRunRows([]);
        return;
      }
      const rows = Array.isArray(b.data?.agent_runs) ? b.data.agent_runs : [];
      setAgentRunRows(rows);
    } catch {
      setAgentRunRows([]);
    } finally {
      if (withSpinner) setAgentRunsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!selectedProjectId) {
      setAgentRunRows([]);
      agentRunSidebarFetchPidRef.current = "";
      return;
    }
    const withSpinner = agentRunSidebarFetchPidRef.current !== selectedProjectId;
    agentRunSidebarFetchPidRef.current = selectedProjectId;
    void fetchAgentRunSidebar(selectedProjectId, withSpinner);
  }, [selectedProjectId, agentRunsListTick, fetchAgentRunSidebar]);

  useEffect(() => {
    if (!selectedProjectId) return;
    const id = setInterval(() => void fetchAgentRunSidebar(selectedProjectId, false), 5000);
    return () => clearInterval(id);
  }, [selectedProjectId, fetchAgentRunSidebar]);

  /** Persist setup + run transcript and generation snapshot so reload keeps state. */
  useEffect(() => {
    if (!selectedProjectId || skipPersistRef.current) return;
    const payload = {
      v: CHAT_STUDIO_STATE_VERSION,
      agentRunId: String(agentRunId || ""),
      setupMessages,
      setupInput,
      messages: messages.map(messageForStorage),
      lastStepsLen: lastStepsLenRef.current,
      doneAnnounced: doneAnnouncedRef.current,
      messageIdSeq: messageIdRef.current,
      setupMsgIdSeq: setupMsgIdRef.current,
      runStatus: String(runStatus || ""),
      timelineVersionId: String(timelineVersionId || ""),
      finalVideoReady: Boolean(finalVideoReady),
      pendingCharacterDrafts,
    };
    writeChatStudioPersistedState(selectedProjectId, payload);
    try {
      localStorage.setItem(CHAT_STUDIO_LAST_PROJECT_KEY, selectedProjectId);
    } catch {
      /* ignore */
    }
  }, [
    selectedProjectId,
    setupMessages,
    setupInput,
    messages,
    runStatus,
    agentRunId,
    timelineVersionId,
    finalVideoReady,
    pendingCharacterDrafts,
    persistVersion,
  ]);

  const restoredSelectionRef = useRef(false);
  const projectsRef = useRef(projects);
  useEffect(() => {
    projectsRef.current = projects;
  }, [projects]);

  /** Which project rows exist (ids only). Live list polls change ``projects`` every tick; key only changes when ids are added/removed. */
  const projectsListIdentityKey = useMemo(() => {
    if (!Array.isArray(projects) || projects.length === 0) return "";
    return projects
      .map((p) => String(p.id))
      .filter((id) => id && /^[0-9a-f-]{36}$/i.test(id))
      .sort()
      .join("|");
  }, [projects]);

  /** Prefer the main Studio ``projectId``; otherwise one-shot restore from Chat’s last-saved id. */
  useEffect(() => {
    const list = projectsRef.current;
    if (!Array.isArray(list) || list.length === 0) return;
    const sid = String(studioProjectId || "").trim();
    const studioOk = Boolean(sid && /^[0-9a-f-]{36}$/i.test(sid) && list.some((p) => String(p.id) === sid));

    if (studioOk) {
      if (sid !== selectedProjectId) void loadProjectIntoComposer(sid);
      restoredSelectionRef.current = true;
      return;
    }

    if (restoredSelectionRef.current) return;
    let last = "";
    try {
      last = localStorage.getItem(CHAT_STUDIO_LAST_PROJECT_KEY) || "";
    } catch {
      return;
    }
    const pid = last.trim();
    if (!pid || !/^[0-9a-f-]{36}$/i.test(pid)) return;
    if (!list.some((p) => String(p.id) === pid)) return;
    restoredSelectionRef.current = true;
    void loadProjectIntoComposer(pid);
  }, [projectsListIdentityKey, studioProjectId, selectedProjectId, loadProjectIntoComposer]);

  useEffect(() => {
    const el = setupThreadRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [setupMessages]);

  const currentBriefSnapshot = useMemo(
    () => ({
      title: String(title || "").trim(),
      topic: String(topic || "").trim(),
      target_runtime_minutes: Math.min(120, Math.max(2, Number(runtime) || 10)),
      audience: String(audience || "").trim() || "general",
      tone: String(tone || "").trim() || "documentary",
      narration_style: String(narrationStyleRef || "").trim() || undefined,
      visual_style: String(visualStyleRef || "").trim() || undefined,
      factual_strictness: factualStrictness || undefined,
      music_preference: String(musicPreference || "").trim() || undefined,
      research_min_sources:
        researchMinSources !== "" && Number.isFinite(Number(researchMinSources)) ? Number(researchMinSources) : undefined,
      frame_aspect_ratio: frameAspectRatio === "9:16" ? "9:16" : "16:9",
      clip_frame_fit: clipFrameFit === "letterbox" ? "letterbox" : "center_crop",
    }),
    [
      title,
      topic,
      runtime,
      audience,
      tone,
      narrationStyleRef,
      visualStyleRef,
      factualStrictness,
      musicPreference,
      researchMinSources,
      frameAspectRatio,
      clipFrameFit,
    ],
  );

  const applyBriefPatchToState = useCallback((patch) => {
    if (!patch || typeof patch !== "object") return;
    if (typeof patch.title === "string") setTitle(patch.title);
    if (typeof patch.topic === "string") setTopic(patch.topic);
    if (patch.target_runtime_minutes != null) {
      const n = Number(patch.target_runtime_minutes);
      if (Number.isFinite(n)) setRuntime(Math.min(120, Math.max(2, n)));
    }
    if (typeof patch.narration_style === "string") setNarrationStyleRef(patch.narration_style);
    if (typeof patch.visual_style === "string") setVisualStyleRef(patch.visual_style);
    if (typeof patch.audience === "string") setAudience(patch.audience);
    if (typeof patch.tone === "string") setTone(patch.tone);
    if (patch.factual_strictness === "strict" || patch.factual_strictness === "balanced" || patch.factual_strictness === "creative") {
      setFactualStrictness(patch.factual_strictness);
    }
    if (typeof patch.music_preference === "string") setMusicPreference(patch.music_preference);
    if (patch.research_min_sources != null) {
      const n = Number(patch.research_min_sources);
      if (Number.isFinite(n) && n >= 1 && n <= 100) setResearchMinSources(n);
    }
    if (patch.frame_aspect_ratio === "16:9" || patch.frame_aspect_ratio === "9:16") {
      setFrameAspectRatio(patch.frame_aspect_ratio);
    }
    if (patch.clip_frame_fit === "center_crop" || patch.clip_frame_fit === "letterbox") {
      setClipFrameFit(patch.clip_frame_fit);
    }
  }, []);

  const postCharacterDrafts = useCallback(async (projectId, drafts) => {
      if (!projectId || !Array.isArray(drafts) || drafts.length === 0) return;
      for (const d of drafts) {
        if (!d || typeof d.name !== "string" || !d.name.trim()) continue;
        try {
          const r = await api(`/v1/projects/${encodeURIComponent(projectId)}/characters`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              name: d.name.trim().slice(0, 256),
              role_in_story: typeof d.role_in_story === "string" ? d.role_in_story : "",
              visual_description: typeof d.visual_description === "string" ? d.visual_description : "",
              time_place_scope_notes:
                d.time_place_scope_notes != null && d.time_place_scope_notes !== "" ? String(d.time_place_scope_notes) : null,
            }),
          });
          await parseJson(r);
        } catch {
          /* ignore individual failures */
        }
      }
  }, []);

  const sendSetupGuide = async () => {
    const text = String(setupInput || "").trim();
    if (!text) return;
    setSetupBusy(true);
    setSetupErr("");
    const apiMessages = [
      ...setupMessages.map((m) => ({ role: m.role, content: m.text })),
      { role: "user", content: text },
    ];
    const userBubble = { id: `s-${++setupMsgIdRef.current}`, role: "user", text };
    setSetupMessages((prev) => [...prev, userBubble]);
    setSetupInput("");
    try {
      const r = await api("/v1/chat-studio/setup-guide", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: apiMessages,
          current_brief: currentBriefSnapshot,
          project_id: selectedProjectId || undefined,
        }),
      });
      const res = await parseJson(r);
      if (!r.ok) throw new Error(apiErrorMessage(res) || `HTTP ${r.status}`);
      const data = res.data || {};
      const replyText = [data.reply, data.notes_for_user].filter(Boolean).join("\n\n");
      setSetupMessages((prev) => [...prev, { id: `s-${++setupMsgIdRef.current}`, role: "assistant", text: replyText }]);
      const patch = data.brief_patch && typeof data.brief_patch === "object" ? data.brief_patch : {};
      applyBriefPatchToState(patch);
      if (Array.isArray(data.character_drafts) && data.character_drafts.length > 0) {
        if (selectedProjectId) {
          await postCharacterDrafts(selectedProjectId, data.character_drafts);
          setPendingCharacterDrafts([]);
        } else {
          setPendingCharacterDrafts(data.character_drafts);
        }
      } else {
        setPendingCharacterDrafts([]);
      }
      if (selectedProjectId && Object.keys(patch).length > 0) {
        const body = { ...patch };
        const patchR = await api(`/v1/projects/${encodeURIComponent(selectedProjectId)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        const patchB = await parseJson(patchR);
        if (!patchR.ok) throw new Error(apiErrorMessage(patchB) || `HTTP ${patchR.status}`);
      }
      onReloadProjects?.();
    } catch (e) {
      setSetupErr(formatUserFacingError(e));
    } finally {
      setSetupBusy(false);
    }
  };

  const handsOffPipelineOptions = useMemo(() => {
    return {
      through: "full_video",
      unattended: true,
      narration_granularity: "scene",
      ...sceneAutomationMediaPipelineOptions(appConfig),
    };
  }, [appConfig]);

  const buildBriefPayload = useCallback(() => {
    const narPresetFallback = String(
      appConfig.narration_style_preset || stylePresets?.defaults?.narration_style_preset || DEFAULT_NARRATION_PRESET_ID,
    ).trim();
    const overrideNar = String(narrationStyleRef || "").trim();
    let narration_style;
    if (overrideNar) {
      narration_style = overrideNar;
    } else {
      const narRefRaw = String(appConfig.default_narration_style_ref || "").trim();
      narration_style =
        narRefRaw && (narRefRaw.startsWith("preset:") || narRefRaw.startsWith("user:"))
          ? narRefRaw
          : `preset:${narPresetFallback || DEFAULT_NARRATION_PRESET_ID}`;
    }
    const visFallback = String(
      appConfig.visual_style_preset || stylePresets?.defaults?.visual_style_preset || "cinematic_documentary",
    ).trim();
    const overrideVis = String(visualStyleRef || "").trim();
    let visual_style;
    if (overrideVis) {
      visual_style =
        overrideVis.startsWith("preset:") || overrideVis.startsWith("user:") ? overrideVis : `preset:${overrideVis}`;
    } else {
      visual_style = `preset:${visFallback || "cinematic_documentary"}`;
    }
    const payload = {
      title: String(title || "").trim() || "Untitled production",
      topic: String(topic || "").trim(),
      target_runtime_minutes: Math.min(120, Math.max(2, Number(runtime) || 10)),
      audience: String(audience || "").trim() || "general",
      tone: String(tone || "").trim() || "documentary",
      narration_style,
      visual_style,
      frame_aspect_ratio: frameAspectRatio === "9:16" ? "9:16" : "16:9",
      clip_frame_fit: clipFrameFit === "letterbox" ? "letterbox" : "center_crop",
      ...briefPreferredMediaProvidersFromAppConfig(appConfig),
    };
    if (factualStrictness === "strict" || factualStrictness === "balanced" || factualStrictness === "creative") {
      payload.factual_strictness = factualStrictness;
    }
    const mp = String(musicPreference || "").trim();
    if (mp) payload.music_preference = mp;
    if (researchMinSources !== "" && Number.isFinite(Number(researchMinSources))) {
      const n = Number(researchMinSources);
      if (n >= 1 && n <= 100) payload.research_min_sources = n;
    }
    return payload;
  }, [
    appConfig,
    stylePresets,
    title,
    topic,
    runtime,
    audience,
    tone,
    narrationStyleRef,
    visualStyleRef,
    factualStrictness,
    musicPreference,
    researchMinSources,
    frameAspectRatio,
    clipFrameFit,
  ]);

  const onGenerate = async () => {
    const topicTrim = String(topic || "").trim();
    if (topicTrim.length < 8) {
      setError("Enter a description (topic) of at least 8 characters.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      if (selectedProjectId) {
        const patchBody = {
          title: String(title || "").trim() || undefined,
          topic: topicTrim,
          target_runtime_minutes: Math.min(120, Math.max(2, Number(runtime) || 10)),
          audience: String(audience || "").trim() || undefined,
          tone: String(tone || "").trim() || undefined,
        };
        const nar = String(narrationStyleRef || "").trim();
        if (nar) patchBody.narration_style = nar;
        const vis = String(visualStyleRef || "").trim();
        if (vis) patchBody.visual_style = vis;
        if (factualStrictness === "strict" || factualStrictness === "balanced" || factualStrictness === "creative") {
          patchBody.factual_strictness = factualStrictness;
        }
        const mp = String(musicPreference || "").trim();
        if (mp) patchBody.music_preference = mp;
        if (researchMinSources !== "" && Number.isFinite(Number(researchMinSources))) {
          const n = Number(researchMinSources);
          if (n >= 1 && n <= 100) patchBody.research_min_sources = n;
        }
        patchBody.clip_frame_fit = clipFrameFit === "letterbox" ? "letterbox" : "center_crop";
        const patchR = await api(`/v1/projects/${encodeURIComponent(selectedProjectId)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(patchBody),
        });
        const patchB = await parseJson(patchR);
        if (!patchR.ok) throw new Error(apiErrorMessage(patchB) || `HTTP ${patchR.status}`);
      }

      appendMessage("user", `Generate hands-off run:\n${topicTrim.slice(0, 2000)}${topicTrim.length > 2000 ? "…" : ""}`);

      const body =
        selectedProjectId ?
          {
            project_id: selectedProjectId,
            pipeline_options: {
              continue_from_existing: true,
              ...handsOffPipelineOptions,
            },
          }
        : {
            brief: buildBriefPayload(),
            pipeline_options: handsOffPipelineOptions,
          };

      const r = await api("/v1/agent-runs", {
        method: "POST",
        body: JSON.stringify(body),
      });
      const res = await parseJson(r);
      if (!r.ok) throw new Error(apiErrorMessage(res) || `HTTP ${r.status}`);
      const ar = res.data?.agent_run;
      const proj = res.data?.project;
      if (proj?.id) {
        const pid = String(proj.id);
        try {
          localStorage.setItem(storageKeyForProject(pid), String(ar?.id || ""));
        } catch {
          /* ignore */
        }
        await loadProjectIntoComposer(pid);
        onStudioProjectOpen?.(pid);
        if (pendingCharacterDrafts.length > 0) {
          await postCharacterDrafts(pid, pendingCharacterDrafts);
          setPendingCharacterDrafts([]);
        }
      }
      if (ar?.id) {
        lastStepsLenRef.current = 0;
        doneAnnouncedRef.current = false;
        setAgentRunId(String(ar.id));
        appendMessage("assistant", "Run queued — tracking progress below.", { kind: "system" });
        setAgentRunsListTick((n) => n + 1);
      }
      onReloadProjects?.();
    } catch (e) {
      setError(formatUserFacingError(e));
    } finally {
      setBusy(false);
    }
  };

  const onStop = async () => {
    if (!agentRunId) return;
    setBusy(true);
    setError("");
    try {
      const r = await api(`/v1/agent-runs/${encodeURIComponent(agentRunId)}/control`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "stop" }),
      });
      const b = await parseJson(r);
      if (!r.ok) throw new Error(apiErrorMessage(b) || `HTTP ${r.status}`);
      appendMessage("assistant", "Stop requested — worker will cancel when safe.", { kind: "system" });
    } catch (e) {
      setError(formatUserFacingError(e));
    } finally {
      setBusy(false);
    }
  };

  const activeRun = runStatus === "running" || runStatus === "queued" || runStatus === "paused";
  const videoSrc =
    selectedProjectId && timelineVersionId ?
      apiCompiledVideoUrl(selectedProjectId, timelineVersionId, { cacheBust: timelineVersionId })
    : "";
  const videoDownload =
    selectedProjectId && timelineVersionId ?
      apiCompiledVideoUrl(selectedProjectId, timelineVersionId, { download: true, cacheBust: timelineVersionId })
    : "";

  return (
    <div className="chat-studio" data-testid="chat-studio-root">
      <aside className="chat-studio__sidebar panel" aria-label="Projects">
        <div className="chat-studio__sidebar-head">
          <h2 className="chat-studio__title">Chat</h2>
          <p className="subtle chat-studio__subtitle">Hands-off runs only on this page.</p>
          <button
            type="button"
            className="secondary chat-studio__new"
            onClick={() => {
              setSelectedProjectId("");
              setTitle("");
              setTopic("");
              setRuntime(10);
              setNarrationStyleRef("");
              setVisualStyleRef("");
              setAudience("general");
              setTone("documentary");
              setFactualStrictness(null);
              setMusicPreference("");
              setResearchMinSources("");
              resetThread();
              try {
                localStorage.removeItem(CHAT_STUDIO_LAST_PROJECT_KEY);
              } catch {
                /* ignore */
              }
            }}
          >
            New production
          </button>
          <button type="button" className="secondary chat-studio__reload" onClick={() => onReloadProjects?.()}>
            Reload list
          </button>
        </div>
        <ul className="chat-studio__project-list">
          {(projects || []).map((p) => (
            <li key={p.id}>
              <button
                type="button"
                className={`chat-studio__project-row${selectedProjectId === p.id ? " is-active" : ""}`}
                onClick={() => {
                  const id = String(p.id || "").trim();
                  void loadProjectIntoComposer(id);
                  if (id) onStudioProjectOpen?.(id);
                }}
              >
                <span className="chat-studio__project-title">{p.title || "Untitled"}</span>
                <span className="subtle chat-studio__project-meta">{p.workflow_phase || p.status}</span>
              </button>
            </li>
          ))}
        </ul>
        {selectedProjectId ? (
          <div className="chat-studio__prev-runs" style={{ marginTop: 12 }}>
            <strong style={{ fontSize: "0.9rem" }}>Hands-off runs</strong>
            <p className="subtle" style={{ margin: "4px 0 0", fontSize: "0.8rem" }}>
              All runs for this production (newest first). Open one to reload its activity. List refreshes every 5s.
            </p>
            {agentRunsLoading ? (
              <p className="subtle" style={{ margin: "8px 0 0" }}>
                Loading…
              </p>
            ) : agentRunRows.length === 0 ? (
              <p className="subtle" style={{ margin: "8px 0 0" }}>
                No runs yet.
              </p>
            ) : (
              <ul style={{ listStyle: "none", margin: "8px 0 0", padding: 0, maxHeight: 220, overflowY: "auto" }}>
                {agentRunRows.map((run) => {
                  const rid = String(run.id || "");
                  const st = run.status != null ? String(run.status) : "";
                  const terminal = isAgentRunTerminalStatus(st);
                  const { line, stale, rowTitle } = agentRunSidebarLabel(run);
                  const rowBusy = runListActionId === rid;
                  return (
                    <li
                      key={rid}
                      style={{
                        marginBottom: 6,
                        display: "flex",
                        gap: 6,
                        alignItems: "stretch",
                      }}
                    >
                      <button
                        type="button"
                        className={`chat-studio__project-row${String(agentRunId) === rid ? " is-active" : ""}`}
                        style={{ flex: 1, minWidth: 0, textAlign: "left", padding: "6px 8px" }}
                        title={rowTitle}
                        onClick={() => void loadAgentRunIntoPanel(rid)}
                      >
                        <span className="chat-studio__project-title mono" style={{ fontSize: "0.75rem", display: "block" }}>
                          {rid.slice(0, 8)}…
                        </span>
                        <span
                          className={`subtle chat-studio__project-meta${stale ? " chat-studio__run-meta--stale" : ""}`}
                          style={{ fontSize: "0.72rem" }}
                          title={rowTitle}
                        >
                          {line || st || "—"}
                          {run.created_at ? ` · ${String(run.created_at).replace("T", " ").slice(0, 16)}` : ""}
                        </span>
                      </button>
                      {terminal ? (
                        <button
                          type="button"
                          className="chat-studio__run-list-icon-btn"
                          title="Remove this run from the server"
                          aria-label="Delete run"
                          disabled={rowBusy}
                          onClick={(e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            void deleteAgentRunInList(rid);
                          }}
                        >
                          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                            <path
                              d="M3 6h18M9 6V4a1 1 0 011-1h4a1 1 0 011 1v2m2 0v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6h14zM10 11v5M14 11v5"
                              stroke="currentColor"
                              strokeWidth="2"
                              strokeLinecap="round"
                            />
                          </svg>
                        </button>
                      ) : (
                        <button
                          type="button"
                          className="chat-studio__run-list-icon-btn"
                          title="Request stop at next safe point"
                          aria-label="Stop run"
                          disabled={rowBusy}
                          onClick={(e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            void stopAgentRunInList(rid);
                          }}
                        >
                          <svg width="12" height="12" viewBox="0 0 24 24" aria-hidden="true">
                            <rect x="5" y="5" width="14" height="14" rx="2" fill="currentColor" />
                          </svg>
                        </button>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        ) : null}
        <div className="chat-studio__roadmap subtle">
          <strong>Project setup</strong>
          <p className="chat-studio__roadmap-text">
            Use the setup chat in the main panel to tune title, description, runtime, picture frame (16:9 or 9:16),
            narration and visual style, and characters before you press Generate.
          </p>
        </div>
      </aside>

      <main className="chat-studio__main panel">
        <header className="chat-studio__main-head">
          <h2 className="chat-studio__main-title">Hands-off chat</h2>
          {runStatus ? (
            <span className="subtle">
              Run: <code>{agentRunId ? agentRunId.slice(0, 8) : "—"}</code> · {runStatus}
            </span>
          ) : null}
        </header>

        {error ? (
          <p className="err chat-studio__err" role="alert">
            {error}
          </p>
        ) : null}

        <div className="chat-studio__split">
          <section className="chat-studio__setup chat-studio__split-col" aria-labelledby="chat-studio-setup-heading">
            <h3 id="chat-studio-setup-heading" className="chat-studio__section-title">
              Project setup
            </h3>
            <p className="subtle chat-studio__section-hint">
              Chat with the guide to set title, description, length, narration and visual style, picture frame (16:9 vs
              9:16), and characters. This uses your workspace text model (same as the rest of Directely).
            </p>
            {setupErr ? (
              <p className="err chat-studio__err" role="alert">
                {setupErr}
              </p>
            ) : null}
            <div
              ref={setupThreadRef}
              className="chat-studio__setup-thread"
              role="log"
              aria-live="polite"
              data-testid="chat-studio-setup-thread"
            >
              {setupMessages.length === 0 ? (
                <p className="subtle chat-studio__setup-empty">
                  Example: “10-minute film on urban beekeeping for a general audience, warm tone, archival look, two main
                  characters, 16:9 landscape.” (Or 9:16 for vertical — the guide will confirm frame if you don’t specify.)
                </p>
              ) : null}
              {setupMessages.map((m) => (
                <div key={m.id} className={`chat-bubble chat-bubble--${m.role}`}>
                  <div className="chat-bubble__text">{m.text}</div>
                </div>
              ))}
            </div>
            <label className="subtle chat-studio__sr-only" htmlFor="chat-studio-setup-input">
              Setup chat message
            </label>
            <textarea
              id="chat-studio-setup-input"
              data-testid="chat-studio-setup-input"
              className="chat-studio__setup-input"
              rows={3}
              value={setupInput}
              onChange={(e) => setSetupInput(e.target.value)}
              placeholder="Ask questions or describe your production…"
              disabled={setupBusy || busy}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  if (!setupBusy && !busy) void sendSetupGuide();
                }
              }}
              spellCheck
            />
            <div className="chat-studio__setup-actions">
              <button type="button" disabled={setupBusy || busy} onClick={() => void sendSetupGuide()}>
                {setupBusy ? "Thinking…" : "Send"}
              </button>
            </div>
          </section>

          <section className="chat-studio__run chat-studio__split-col" aria-labelledby="chat-studio-run-heading">
            <h3 id="chat-studio-run-heading" className="chat-studio__section-title">
              Run activity
            </h3>
            <div className="chat-studio__thread" role="log" aria-live="polite" aria-relevant="additions">
              {messages.length === 0 ? (
                <p className="subtle chat-studio__empty">
                  After you press <strong>Generate</strong>, pipeline progress appears here. Open a project on the left to
                  continue an existing hands-off run.
                </p>
              ) : null}
              {messages.map((m) => (
                <div key={m.id} className={`chat-bubble chat-bubble--${m.role}`}>
                  <div className="chat-bubble__text">{m.text}</div>
                </div>
              ))}
              {finalVideoReady && videoSrc ? (
                <div className="chat-bubble chat-bubble--assistant chat-bubble--video">
                  <div className="chat-bubble__text">Final video</div>
                  <video className="chat-studio__video" controls src={videoSrc} playsInline />
                  <a className="chat-studio__download" href={videoDownload} download>
                    Download MP4
                  </a>
                </div>
              ) : null}
            </div>

            <p className="chat-studio__sr-summary" aria-live="polite">
              Brief: {title || "Untitled"} · {topic.trim().length} characters in description · {runtime} min ·{" "}
              {frameAspectRatio === "9:16" ? "9:16 portrait" : "16:9 landscape"}.
            </p>

            <div className="chat-studio__composer chat-studio__composer--run">
              {!selectedProjectId ? (
                <div style={{ marginBottom: 10 }}>
                  <label htmlFor="chat-studio-frame" className="subtle" style={{ display: "block", marginBottom: 4 }}>
                    Picture frame (locked when the project is created)
                  </label>
                  <select
                    id="chat-studio-frame"
                    value={frameAspectRatio === "9:16" ? "9:16" : "16:9"}
                    onChange={(e) => setFrameAspectRatio(e.target.value)}
                    disabled={busy}
                  >
                    <option value="16:9">16:9 landscape</option>
                    <option value="9:16">9:16 portrait</option>
                  </select>
                </div>
              ) : null}
              <div className="chat-studio__actions">
                <button type="button" disabled={busy} onClick={() => void onGenerate()}>
                  {busy ? "Working…" : "Generate"}
                </button>
                <button type="button" className="secondary" disabled={busy || !activeRun} onClick={() => void onStop()}>
                  Stop run
                </button>
              </div>
            </div>
          </section>
        </div>
      </main>
    </div>
  );
}
