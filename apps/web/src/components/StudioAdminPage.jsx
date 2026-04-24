import { useCallback, useEffect, useMemo, useState } from "react";
import { parseJson, apiErrorMessage, formatUserFacingError } from "../lib/apiHelpers.js";
import { apiPath } from "../lib/api.js";
import { adminFetch, getAdminKey, setAdminKey } from "../lib/adminApi.js";

const TABS = [
  { id: "dashboard", label: "Dashboard" },
  { id: "tools", label: "Tools" },
  { id: "database", label: "Database" },
  { id: "users", label: "Users" },
  { id: "tenants", label: "Workspaces" },
  { id: "memberships", label: "Permissions" },
  { id: "plans", label: "Plans" },
  { id: "billing", label: "Subscriptions" },
  { id: "stripe", label: "Stripe" },
  { id: "payments", label: "Payments" },
  { id: "projects", label: "Projects" },
  { id: "runs", label: "Agent runs" },
  { id: "jobs", label: "Jobs" },
];

const LIMIT = 50;

const BUDGET_PIPELINE_LS_KEY = "director_admin_budget_pipeline_v1";
const BUDGET_PIPELINE_STATE_VERSION = 1;
/** Plain UUID string so Continue works after refresh even if `budgetLast` shape is stale. */
const BUDGET_PROJECT_ID_LS_KEY = "director_admin_budget_project_id_v1";

function readSavedBudgetProjectId() {
  try {
    return localStorage.getItem(BUDGET_PROJECT_ID_LS_KEY)?.trim() || "";
  } catch {
    return "";
  }
}

/** Prefer the most recent budget queue that recorded a project id (history → budgetLast → dedicated key). */
function readLastRanBudgetProjectId(persisted) {
  const p = persisted && typeof persisted === "object" ? persisted : null;
  const h = p?.budgetRunHistory;
  if (Array.isArray(h) && h.length) {
    for (let i = h.length - 1; i >= 0; i--) {
      const id = h[i]?.project_id;
      if (id != null && String(id).trim()) return String(id).trim();
    }
  }
  const fromLast = p?.budgetLast?.project?.id;
  if (fromLast != null && String(fromLast).trim()) return String(fromLast).trim();
  return readSavedBudgetProjectId();
}

function persistLastRanBudgetProjectId(projectId) {
  const s = projectId == null ? "" : String(projectId).trim();
  if (!s) return;
  try {
    localStorage.setItem(BUDGET_PROJECT_ID_LS_KEY, s);
  } catch {
    /* ignore */
  }
}

const BUDGET_AGENT_RUN_TERMINAL = new Set([
  "succeeded",
  "failed",
  "cancelled",
  "blocked",
  "not_found",
]);

function budgetAgentRunIsTerminal(st) {
  return BUDGET_AGENT_RUN_TERMINAL.has(String(st || "").trim());
}

function readBudgetPipelinePersisted() {
  try {
    const raw = localStorage.getItem(BUDGET_PIPELINE_LS_KEY);
    if (!raw) return null;
    const d = JSON.parse(raw);
    if (d.v !== BUDGET_PIPELINE_STATE_VERSION || typeof d !== "object") return null;
    return d;
  } catch {
    return null;
  }
}

/** Shorten long ids (UUIDs) for tables; full value in tooltip. */
function formatShortId(value) {
  const s = value == null ? "" : String(value);
  if (s.length <= 14) return s;
  return `${s.slice(0, 6)}…${s.slice(-4)}`;
}

async function copyTextToClipboard(text) {
  const s = text == null ? "" : String(text);
  if (!s) return false;
  try {
    await navigator.clipboard.writeText(s);
    return true;
  } catch {
    try {
      const ta = document.createElement("textarea");
      ta.value = s;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand("copy");
      document.body.removeChild(ta);
      return ok;
    } catch {
      return false;
    }
  }
}

function TruncId({ id, className, copyable, onCopy }) {
  const full = id == null ? "" : String(id);
  const short = formatShortId(full);
  const title = copyable ? `${full} — click to copy` : full;
  const handleCopy = async (e) => {
    if (!copyable || !full) return;
    e.preventDefault();
    e.stopPropagation();
    const ok = await copyTextToClipboard(full);
    if (ok) onCopy?.();
  };
  const kbd = (e) => {
    if (!copyable) return;
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      void handleCopy(e);
    }
  };
  return (
    <span
      className={className || "mono"}
      title={title}
      aria-label={copyable ? `Workspace id ${full}, click to copy` : undefined}
      style={{
        cursor: copyable ? "pointer" : full.length > 14 ? "help" : undefined,
        textDecoration: copyable ? "underline dotted" : undefined,
        textUnderlineOffset: copyable ? 2 : undefined,
      }}
      onClick={copyable ? handleCopy : undefined}
      onKeyDown={copyable ? kbd : undefined}
      role={copyable ? "button" : undefined}
      tabIndex={copyable ? 0 : undefined}
    >
      {short}
    </span>
  );
}

function safeJsonStringify(value, space = 2) {
  try {
    return JSON.stringify(value ?? {}, null, space);
  } catch {
    return "{}";
  }
}

function normalizeAdminList(tab, raw) {
  const d = raw && typeof raw === "object" ? raw : {};
  if (tab === "plans") {
    return {
      ...d,
      plans: Array.isArray(d.plans) ? d.plans : [],
    };
  }
  if (tab === "billing") {
    return {
      ...d,
      items: Array.isArray(d.items) ? d.items : [],
      total_count: typeof d.total_count === "number" ? d.total_count : Array.isArray(d.items) ? d.items.length : 0,
    };
  }
  if (tab === "payments") {
    return {
      ...d,
      events: Array.isArray(d.events) ? d.events : [],
      total_count: typeof d.total_count === "number" ? d.total_count : Array.isArray(d.events) ? d.events.length : 0,
    };
  }
  return d;
}

/** Admin billing: `datetime-local` value from API ISO string. */
function billingIsoToDatetimeLocal(iso) {
  if (!iso || typeof iso !== "string") return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** Returns ISO string or null when empty / invalid. */
function billingDatetimeLocalToIso(local) {
  if (local == null || String(local).trim() === "") return null;
  const d = new Date(local);
  if (Number.isNaN(d.getTime())) return null;
  return d.toISOString();
}

function JsonTextArea({ value, onChange, rows = 6 }) {
  return (
    <textarea
      className="mono"
      style={{ width: "100%", fontSize: "0.8rem", minHeight: 80 }}
      rows={rows}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

function partitionEntitlements(definitions, value) {
  const v = value && typeof value === "object" ? value : {};
  const known = new Set((definitions || []).map((d) => d.key));
  const core = {};
  const extra = {};
  for (const [k, val] of Object.entries(v)) {
    if (known.has(k)) core[k] = val;
    else extra[k] = val;
  }
  return { core, extra };
}

function mergeEntitlementParts(definitions, core, extra) {
  const known = new Set((definitions || []).map((d) => d.key));
  const out = { ...(extra && typeof extra === "object" ? extra : {}) };
  for (const k of known) {
    if (Object.prototype.hasOwnProperty.call(core, k)) out[k] = core[k];
  }
  return out;
}

/** Known keys as toggles/inputs; optional "Additional JSON" for extra keys. Still saves one object as `entitlements_json`. */
function EntitlementEditor({ definitions, value, onChange }) {
  const { core, extra } = partitionEntitlements(definitions, value);

  if (!definitions?.length) {
    return (
      <JsonTextArea
        value={safeJsonStringify(value)}
        onChange={(s) => {
          try {
            onChange(JSON.parse(s || "{}"));
          } catch {
            /* ignore invalid */
          }
        }}
        rows={8}
      />
    );
  }

  const setBool = (key, b) => onChange(mergeEntitlementParts(definitions, { ...core, [key]: b }, extra));
  const setLimit = (key, str) => {
    const t = str.trim();
    const n = t === "" ? null : Math.max(0, parseInt(t, 10) || 0);
    onChange(mergeEntitlementParts(definitions, { ...core, [key]: n }, extra));
  };
  const setExtraText = (s) => {
    try {
      const parsed = JSON.parse(s || "{}");
      onChange(mergeEntitlementParts(definitions, core, parsed));
    } catch {
      /* ignore */
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {definitions.map((d) => {
        if (d.type === "boolean") {
          return (
            <label key={d.key} style={{ display: "flex", gap: 8, alignItems: "flex-start", cursor: "pointer" }}>
              <input
                type="checkbox"
                style={{ marginTop: 3 }}
                checked={core[d.key] === true}
                onChange={(e) => setBool(d.key, e.target.checked)}
              />
              <span>
                <strong>{d.label}</strong>
                <code className="subtle mono" style={{ marginLeft: 6, fontSize: "0.78rem" }}>
                  {d.key}
                </code>
                {d.description ? <span className="subtle" style={{ display: "block", fontSize: "0.82rem", marginTop: 2 }}>{d.description}</span> : null}
              </span>
            </label>
          );
        }
        if (d.type === "limit") {
          const raw = core[d.key];
          const str = raw == null ? "" : String(raw);
          return (
            <label key={d.key} className="subtle" style={{ display: "block" }}>
              <strong>{d.label}</strong>
              <code className="mono" style={{ marginLeft: 6, fontSize: "0.78rem" }}>
                {d.key}
              </code>
              {d.description ? <span style={{ display: "block", fontSize: "0.82rem", marginTop: 2 }}>{d.description}</span> : null}
              <input
                value={str}
                placeholder="empty = unlimited"
                onChange={(e) => setLimit(d.key, e.target.value)}
                className="mono"
                style={{ width: "100%", marginTop: 6, maxWidth: 120 }}
              />
            </label>
          );
        }
        return null;
      })}
      <details style={{ marginTop: 4 }}>
        <summary className="subtle" style={{ cursor: "pointer" }}>
          Additional JSON keys (advanced)
        </summary>
        <p className="subtle" style={{ fontSize: "0.8rem", margin: "6px 0" }}>
          Merge extra key/value pairs into the same object sent to the API. Conflicts with the fields above are overwritten
          by the checkboxes/inputs when you change them.
        </p>
        <JsonTextArea value={safeJsonStringify(extra)} onChange={setExtraText} rows={5} />
      </details>
    </div>
  );
}

export function StudioAdminPage({ showToast, workspaceTenantId = "" }) {
  const [tab, setTab] = useState("dashboard");
  const [keyInput, setKeyInput] = useState(() => getAdminKey());
  const [unlocked, setUnlocked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [dash, setDash] = useState(null);
  const [list, setList] = useState(null);
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [memTenant, setMemTenant] = useState("");
  const [memUser, setMemUser] = useState("");
  const [payTenant, setPayTenant] = useState("");
  const [projTenant, setProjTenant] = useState("");
  const [runTenant, setRunTenant] = useState("");
  const [runProject, setRunProject] = useState("");
  const [runCancelBusyId, setRunCancelBusyId] = useState("");
  const [runCancelAllBusy, setRunCancelAllBusy] = useState(false);
  const [jobTenant, setJobTenant] = useState("");
  const [jobStatus, setJobStatus] = useState("");
  const [entitlementDefs, setEntitlementDefs] = useState(null);

  const [budgetTitle, setBudgetTitle] = useState(() => {
    const p = readBudgetPipelinePersisted();
    return typeof p?.budgetTitle === "string" ? p.budgetTitle : "Budget pipeline test";
  });
  const [budgetTopic, setBudgetTopic] = useState(() => {
    const p = readBudgetPipelinePersisted();
    return typeof p?.budgetTopic === "string" ?
        p.budgetTopic
      : "Smoke test: placeholder images, local FFmpeg video, workspace TTS narration — minimal image API cost.";
  });
  const [budgetRuntime, setBudgetRuntime] = useState(() => {
    const p = readBudgetPipelinePersisted();
    return typeof p?.budgetRuntime === "string" ? p.budgetRuntime : "5";
  });
  const [budgetMode, setBudgetMode] = useState(() => {
    const p = readBudgetPipelinePersisted();
    return p?.budgetMode === "auto" || p?.budgetMode === "hands-off" ? p.budgetMode : "hands-off";
  });
  const [budgetFrameAspect, setBudgetFrameAspect] = useState(() => {
    const p = readBudgetPipelinePersisted();
    return p?.budgetFrameAspect === "9:16" ? "9:16" : "16:9";
  });
  /** When true, POST body sets ``production_media`` — workspace image/video providers + scene auto-videos (costs APIs). */
  const [budgetProductionMedia, setBudgetProductionMedia] = useState(() => {
    const p = readBudgetPipelinePersisted();
    return p?.budgetProductionMedia === true;
  });
  /** Optional; sent as ``tenant_id`` when set (overrides session workspace). */
  const [budgetWorkspaceIdOverride, setBudgetWorkspaceIdOverride] = useState(() => {
    const p = readBudgetPipelinePersisted();
    return typeof p?.budgetWorkspaceIdOverride === "string" ? p.budgetWorkspaceIdOverride : "";
  });
  const [budgetBusy, setBudgetBusy] = useState(false);
  const [budgetErr, setBudgetErr] = useState(() => {
    const p = readBudgetPipelinePersisted();
    return typeof p?.budgetErr === "string" ? p.budgetErr : "";
  });
  const [budgetLast, setBudgetLast] = useState(() => {
    const p = readBudgetPipelinePersisted();
    return p?.budgetLast && typeof p.budgetLast === "object" ? p.budgetLast : null;
  });
  const [budgetRunHistory, setBudgetRunHistory] = useState(() => {
    const p = readBudgetPipelinePersisted();
    return Array.isArray(p?.budgetRunHistory) ? p.budgetRunHistory : [];
  });
  /** Project id for the last budget run that was queued (Run or Continue); same id Continue re-uses. */
  const [budgetProjectId, setBudgetProjectId] = useState(() => readLastRanBudgetProjectId(readBudgetPipelinePersisted()));
  /** agent_run_id → status from admin GET (for Stop vs Delete). */
  const [budgetRunStatuses, setBudgetRunStatuses] = useState({});
  const [budgetRowBusy, setBudgetRowBusy] = useState("");

  const [dbStatus, setDbStatus] = useState(null);
  const [dbBusy, setDbBusy] = useState(false);
  const [dbErr, setDbErr] = useState("");
  const [dbMsg, setDbMsg] = useState("");
  const [restoreConfirm, setRestoreConfirm] = useState("");
  const [restoreFile, setRestoreFile] = useState(null);

  useEffect(() => {
    if (!unlocked) return;
    void (async () => {
      try {
        const r = await adminFetch("/v1/admin/entitlement-definitions");
        const body = await parseJson(r);
        if (r.ok && Array.isArray(body.data?.definitions)) setEntitlementDefs(body.data.definitions);
        else setEntitlementDefs(null);
      } catch {
        setEntitlementDefs(null);
      }
    })();
  }, [unlocked]);

  useEffect(() => {
    if (!unlocked) return;
    try {
      localStorage.setItem(
        BUDGET_PIPELINE_LS_KEY,
        JSON.stringify({
          v: BUDGET_PIPELINE_STATE_VERSION,
          budgetTitle,
          budgetTopic,
          budgetRuntime,
          budgetMode,
          budgetFrameAspect,
          budgetProductionMedia,
          budgetWorkspaceIdOverride,
          budgetLast,
          budgetErr,
          budgetRunHistory,
        }),
      );
    } catch {
      /* quota or private mode */
    }
  }, [
    unlocked,
    budgetTitle,
    budgetTopic,
    budgetRuntime,
    budgetMode,
    budgetFrameAspect,
    budgetProductionMedia,
    budgetWorkspaceIdOverride,
    budgetLast,
    budgetErr,
    budgetRunHistory,
  ]);

  useEffect(() => {
    const id = budgetLast?.project?.id;
    if (id == null || String(id).trim() === "") return;
    const s = String(id).trim();
    setBudgetProjectId(s);
    persistLastRanBudgetProjectId(s);
  }, [budgetLast?.project?.id]);

  useEffect(() => {
    if (!unlocked) return;
    const ids = new Set();
    for (const row of budgetRunHistory || []) {
      const aid = row?.agent_run_id;
      if (aid != null && String(aid).trim()) ids.add(String(aid).trim());
    }
    const lastAid = budgetLast?.agent_run?.id;
    if (lastAid != null && String(lastAid).trim()) ids.add(String(lastAid).trim());

    if (ids.size === 0) {
      setBudgetRunStatuses({});
      return;
    }

    let cancelled = false;
    const poll = async () => {
      const updates = {};
      const missingIds = [];
      await Promise.all(
        [...ids].map(async (rawId) => {
          try {
            const r = await adminFetch(`/v1/admin/agent-runs/${encodeURIComponent(rawId)}`);
            if (r.status === 404) {
              missingIds.push(rawId);
              updates[rawId] = "not_found";
              return;
            }
            const b = await parseJson(r);
            if (r.ok && b?.data?.status != null) updates[rawId] = String(b.data.status);
          } catch {
            /* ignore */
          }
        }),
      );
      if (!cancelled) {
        if (missingIds.length) {
          setBudgetRunHistory((h) =>
            Array.isArray(h) ? h.filter((row) => !missingIds.includes(String(row.agent_run_id || "").trim())) : h,
          );
          setBudgetLast((bl) => {
            if (!bl?.agent_run?.id) return bl;
            const cur = String(bl.agent_run.id).trim();
            if (!missingIds.includes(cur)) return bl;
            return { ...bl, agent_run: null, poll_url: null, hint: bl?.hint };
          });
        }
        setBudgetRunStatuses((prev) => {
          const next = { ...prev };
          for (const [k, v] of Object.entries(updates)) next[k] = v;
          return next;
        });
      }
    };
    void poll();
    const t = setInterval(() => void poll(), 4000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [unlocked, budgetRunHistory, budgetLast?.agent_run?.id]);

  /** Logged-in Studio workspace (from App: auth/me + session tenant); falls back if prop not ready yet. */
  const resolvedBudgetWorkspaceId = useMemo(() => {
    return (workspaceTenantId || "").trim();
  }, [workspaceTenantId]);

  const tryUnlock = useCallback(async () => {
    setErr("");
    setBusy(true);
    const k = keyInput.trim();
    setAdminKey(k);
    try {
      const r = await adminFetch("/v1/admin/health");
      const body = await parseJson(r);
      if (!r.ok) {
        setUnlocked(false);
        setErr(apiErrorMessage(body) || `HTTP ${r.status}`);
        return;
      }
      setUnlocked(true);
      showToast?.("Admin session active", { type: "success" });
    } catch (e) {
      setUnlocked(false);
      setErr(formatUserFacingError(e));
    } finally {
      setBusy(false);
    }
  }, [keyInput, showToast]);

  const runDbBackup = useCallback(async () => {
    setDbErr("");
    setDbMsg("");
    setDbBusy(true);
    try {
      const r = await adminFetch("/v1/admin/db/backup");
      if (!r.ok) {
        const body = await parseJson(r).catch(() => ({}));
        setDbErr(apiErrorMessage(body) || `HTTP ${r.status}`);
        return;
      }
      const blob = await r.blob();
      const cd = r.headers.get("Content-Disposition") || "";
      let name = "director_backup.sql";
      const m = /filename="([^"]+)"/.exec(cd);
      if (m) name = m[1];
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      a.click();
      URL.revokeObjectURL(url);
      setDbMsg(`Downloaded ${name} (${(blob.size / (1024 * 1024)).toFixed(2)} MiB).`);
      showToast?.("Backup downloaded", { type: "success" });
    } catch (e) {
      setDbErr(formatUserFacingError(e));
    } finally {
      setDbBusy(false);
    }
  }, [showToast]);

  const runDbRestore = useCallback(async () => {
    setDbErr("");
    setDbMsg("");
    if (!restoreFile) {
      setDbErr("Choose a .sql dump file first.");
      return;
    }
    if (
      !window.confirm(
        "This will run the SQL file against the live database the API is using. Data can be overwritten or corrupted. Stop other writers and take a backup first. Continue?",
      )
    ) {
      return;
    }
    setDbBusy(true);
    try {
      const fd = new FormData();
      fd.set("confirm", restoreConfirm);
      fd.set("dump", restoreFile, restoreFile.name || "dump.sql");
      const r = await adminFetch("/v1/admin/db/restore", { method: "POST", body: fd });
      const body = await parseJson(r).catch(() => ({}));
      if (!r.ok) {
        setDbErr(apiErrorMessage(body) || `HTTP ${r.status}`);
        return;
      }
      setDbMsg("Restore completed successfully.");
      showToast?.("Database restore completed", { type: "success" });
      setRestoreFile(null);
      try {
        const el = document.querySelector('input[type="file"][data-director-db-restore-file="1"]');
        if (el) el.value = "";
      } catch {
        /* ignore */
      }
    } catch (e) {
      setDbErr(formatUserFacingError(e));
    } finally {
      setDbBusy(false);
    }
  }, [restoreConfirm, restoreFile, showToast]);

  useEffect(() => {
    if (getAdminKey().trim()) {
      void (async () => {
        const r = await adminFetch("/v1/admin/health");
        setUnlocked(r.ok);
      })();
    }
  }, []);

  useEffect(() => {
    setOffset(0);
  }, [tab, q, memTenant, memUser, payTenant, projTenant, runTenant, runProject, jobTenant, jobStatus]);

  const loadTab = useCallback(async () => {
    if (!unlocked) return;
    setErr("");
    if (tab === "database") {
      setDbErr("");
      setDbMsg("");
      setBusy(true);
      setDbStatus(null);
      try {
        const r = await adminFetch("/v1/admin/db/status");
        const body = await parseJson(r);
        if (!r.ok) {
          setErr(apiErrorMessage(body) || "Request failed");
          return;
        }
        setDbStatus(body.data);
      } catch (e) {
        setErr(formatUserFacingError(e));
      } finally {
        setBusy(false);
      }
      return;
    }
    if (tab === "tools") {
      setBusy(false);
      return;
    }
    setBusy(true);
    setList(null);
    try {
      if (tab === "stripe") {
        const r = await adminFetch("/v1/admin/stripe-settings");
        const body = await parseJson(r);
        if (!r.ok) {
          setErr(apiErrorMessage(body) || "Request failed");
          return;
        }
        setList({ stripe: body.data });
        return;
      }
      let path = "";
      const params = new URLSearchParams();
      params.set("limit", String(LIMIT));
      params.set("offset", String(offset));
      if (q.trim()) params.set("q", q.trim());

      if (tab === "dashboard") path = "/v1/admin/dashboard";
      else if (tab === "users") path = `/v1/admin/users?${params}`;
      else if (tab === "tenants") path = `/v1/admin/tenants?${params}`;
      else if (tab === "memberships") {
        const mp = new URLSearchParams();
        mp.set("limit", String(LIMIT));
        mp.set("offset", String(offset));
        if (memTenant.trim()) mp.set("tenant_id", memTenant.trim());
        if (memUser.trim()) mp.set("user_id", memUser.trim());
        path = `/v1/admin/memberships?${mp}`;
      } else if (tab === "plans") path = "/v1/admin/subscription-plans";
      else if (tab === "billing") path = `/v1/admin/tenant-billing?limit=${LIMIT}&offset=${offset}`;
      else if (tab === "payments") {
        const mp = new URLSearchParams();
        mp.set("limit", String(LIMIT));
        mp.set("offset", String(offset));
        if (payTenant.trim()) mp.set("tenant_id", payTenant.trim());
        path = `/v1/admin/payments?${mp}`;
      } else if (tab === "projects") {
        const mp = new URLSearchParams();
        mp.set("limit", String(LIMIT));
        mp.set("offset", String(offset));
        if (q.trim()) mp.set("q", q.trim());
        if (projTenant.trim()) mp.set("tenant_id", projTenant.trim());
        path = `/v1/admin/projects?${mp}`;
      } else if (tab === "runs") {
        const mp = new URLSearchParams();
        mp.set("limit", String(LIMIT));
        mp.set("offset", String(offset));
        if (runTenant.trim()) mp.set("tenant_id", runTenant.trim());
        if (runProject.trim()) mp.set("project_id", runProject.trim());
        path = `/v1/admin/agent-runs?${mp}`;
      } else if (tab === "jobs") {
        const mp = new URLSearchParams();
        mp.set("limit", String(LIMIT));
        mp.set("offset", String(offset));
        if (jobTenant.trim()) mp.set("tenant_id", jobTenant.trim());
        if (jobStatus.trim()) mp.set("status", jobStatus.trim());
        path = `/v1/admin/jobs?${mp}`;
      }

      const r = await adminFetch(path);
      const body = await parseJson(r);
      if (!r.ok) {
        setErr(apiErrorMessage(body) || "Request failed");
        return;
      }
      if (tab === "dashboard") setDash(body.data);
      else setList(normalizeAdminList(tab, body.data));
    } catch (e) {
      setErr(formatUserFacingError(e));
    } finally {
      setBusy(false);
    }
  }, [
    unlocked,
    tab,
    q,
    offset,
    memTenant,
    memUser,
    payTenant,
    projTenant,
    runTenant,
    runProject,
    jobTenant,
    jobStatus,
  ]);

  useEffect(() => {
    void loadTab();
  }, [loadTab]);

  const runBudgetPipeline = useCallback(async () => {
    setBudgetErr("");
    const topic = budgetTopic.trim();
    if (!topic) {
      setBudgetErr("Topic is required.");
      return;
    }
    const tr = Math.min(120, Math.max(2, parseInt(String(budgetRuntime).trim(), 10) || 5));
    setBudgetBusy(true);
    try {
      const payload = {
        title: budgetTitle.trim() || "Budget pipeline test",
        topic,
        target_runtime_minutes: tr,
        mode: budgetMode === "auto" ? "auto" : "hands-off",
        frame_aspect_ratio: budgetFrameAspect === "9:16" ? "9:16" : "16:9",
        production_media: budgetProductionMedia,
      };
      const tid = (budgetWorkspaceIdOverride.trim() || resolvedBudgetWorkspaceId.trim());
      if (tid) payload.tenant_id = tid;
      const r = await adminFetch("/v1/admin/budget-pipeline-test", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      const body = await parseJson(r);
      if (!r.ok) {
        setBudgetErr(apiErrorMessage(body) || `HTTP ${r.status}`);
        return;
      }
      const data = body.data ?? null;
      setBudgetLast(data);
      const projId = data?.project?.id;
      if (projId != null && String(projId).trim()) {
        const ps = String(projId).trim();
        setBudgetProjectId(ps);
        persistLastRanBudgetProjectId(ps);
      }
      const rid = data?.agent_run?.id;
      if (rid) {
        const ridStr = String(rid);
        setBudgetRunStatuses((s) => ({ ...s, [ridStr]: "queued" }));
        setBudgetRunHistory((h) => {
          const next = [
            ...(h || []),
            {
              ts: new Date().toISOString(),
              agent_run_id: ridStr,
              project_id: projId != null ? String(projId).trim() : undefined,
              poll_url: data.poll_url != null ? String(data.poll_url) : null,
            },
          ];
          return next.slice(-50);
        });
      }
      showToast?.("Budget pipeline queued", { type: "success" });
    } catch (e) {
      setBudgetErr(formatUserFacingError(e));
    } finally {
      setBudgetBusy(false);
    }
  }, [
    budgetTitle,
    budgetTopic,
    budgetRuntime,
    budgetMode,
    budgetFrameAspect,
    budgetProductionMedia,
    resolvedBudgetWorkspaceId,
    budgetWorkspaceIdOverride,
    showToast,
  ]);

  const continueBudgetPipeline = useCallback(async () => {
    const pid =
      String(budgetProjectId || "").trim() ||
      readLastRanBudgetProjectId({
        budgetRunHistory,
        budgetLast,
      }) ||
      String(budgetLast?.project?.id || "").trim();
    if (!pid) {
      const msg =
        "Set Project id (below) or run “Run budget pipeline” once — Continue needs a workspace project UUID.";
      setBudgetErr(msg);
      showToast?.(msg, { type: "error" });
      return;
    }
    setBudgetErr("");
    const tr = Math.min(120, Math.max(2, parseInt(String(budgetRuntime).trim(), 10) || 5));
    setBudgetBusy(true);
    try {
      const payload = {
        continue_pipeline: true,
        project_id: String(pid).trim(),
        title: budgetTitle.trim() || "Budget pipeline test",
        topic: "",
        target_runtime_minutes: tr,
        mode: budgetMode === "auto" ? "auto" : "hands-off",
        frame_aspect_ratio: budgetFrameAspect === "9:16" ? "9:16" : "16:9",
        production_media: budgetProductionMedia,
      };
      const tid = (budgetWorkspaceIdOverride.trim() || resolvedBudgetWorkspaceId.trim());
      if (tid) payload.tenant_id = tid;
      const r = await adminFetch("/v1/admin/budget-pipeline-test", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      const body = await parseJson(r);
      if (!r.ok) {
        setBudgetErr(apiErrorMessage(body) || `HTTP ${r.status}`);
        return;
      }
      const data = body.data ?? null;
      setBudgetLast(data);
      const projId = data?.project?.id;
      if (projId != null && String(projId).trim()) {
        const ps = String(projId).trim();
        setBudgetProjectId(ps);
        persistLastRanBudgetProjectId(ps);
      }
      const rid = data?.agent_run?.id;
      if (rid) {
        const ridStr = String(rid);
        setBudgetRunStatuses((s) => ({ ...s, [ridStr]: "queued" }));
        setBudgetRunHistory((h) => {
          const next = [
            ...(h || []),
            {
              ts: new Date().toISOString(),
              agent_run_id: ridStr,
              project_id: projId != null ? String(projId).trim() : undefined,
              poll_url: data.poll_url != null ? String(data.poll_url) : null,
              continued: true,
            },
          ];
          return next.slice(-50);
        });
      }
      showToast?.("Budget pipeline continued (skips completed steps)", { type: "success" });
    } catch (e) {
      setBudgetErr(formatUserFacingError(e));
    } finally {
      setBudgetBusy(false);
    }
  }, [
    budgetProjectId,
    budgetRunHistory,
    budgetLast,
    budgetTitle,
    budgetRuntime,
    budgetMode,
    budgetFrameAspect,
    budgetProductionMedia,
    resolvedBudgetWorkspaceId,
    budgetWorkspaceIdOverride,
    showToast,
  ]);

  const stopBudgetAgentRun = useCallback(
    async (runId) => {
      const id = String(runId || "").trim();
      if (!id) return;
      setBudgetRowBusy(id);
      setBudgetErr("");
      try {
        const r = await adminFetch(`/v1/admin/agent-runs/${encodeURIComponent(id)}/control`, {
          method: "POST",
          body: JSON.stringify({ action: "stop" }),
        });
        const b = await parseJson(r);
        if (r.status === 404) {
          setBudgetRunStatuses((s) => ({ ...s, [id]: "not_found" }));
          setBudgetRunHistory((h) =>
            Array.isArray(h) ? h.filter((row) => String(row.agent_run_id || "").trim() !== id) : h,
          );
          setBudgetLast((bl) => {
            if (!bl?.agent_run?.id || String(bl.agent_run.id).trim() !== id) return bl;
            return { ...bl, agent_run: null, poll_url: null, hint: bl?.hint };
          });
          showToast?.("That run is no longer on the server — removed from this list.", { type: "success" });
          return;
        }
        if (!r.ok) throw new Error(apiErrorMessage(b) || `HTTP ${r.status}`);
        showToast?.("Stop requested for that run", { type: "success" });
        const r2 = await adminFetch(`/v1/admin/agent-runs/${encodeURIComponent(id)}`);
        if (r2.status === 404) {
          setBudgetRunStatuses((s) => ({ ...s, [id]: "not_found" }));
        } else {
          const b2 = await parseJson(r2);
          if (r2.ok && b2?.data?.status != null) {
            setBudgetRunStatuses((s) => ({ ...s, [id]: String(b2.data.status) }));
          }
        }
      } catch (e) {
        const msg = formatUserFacingError(e);
        setBudgetErr(msg);
        showToast?.(msg, { type: "error" });
      } finally {
        setBudgetRowBusy("");
      }
    },
    [showToast],
  );

  const deleteBudgetAgentRun = useCallback(
    async (runId) => {
      const id = String(runId || "").trim();
      if (!id) return;
      setBudgetRowBusy(id);
      setBudgetErr("");
      try {
        const r = await adminFetch(`/v1/admin/agent-runs/${encodeURIComponent(id)}`, { method: "DELETE" });
        if (!r.ok && r.status !== 404) {
          const b = await parseJson(r);
          throw new Error(apiErrorMessage(b) || `HTTP ${r.status}`);
        }
        showToast?.(r.status === 404 ? "Run was already removed" : "Run deleted", {
          type: "success",
        });
        setBudgetRunHistory((h) => (Array.isArray(h) ? h.filter((row) => String(row.agent_run_id) !== id) : h));
        setBudgetRunStatuses((s) => {
          const n = { ...s };
          delete n[id];
          return n;
        });
        setBudgetLast((bl) => (bl?.agent_run?.id != null && String(bl.agent_run.id) === id ? null : bl));
      } catch (e) {
        const msg = formatUserFacingError(e);
        setBudgetErr(msg);
        showToast?.(msg, { type: "error" });
      } finally {
        setBudgetRowBusy("");
      }
    },
    [showToast],
  );

  const cancelAdminAgentRun = useCallback(
    async (runId) => {
      const id = String(runId || "").trim();
      if (!id) return;
      setRunCancelBusyId(id);
      setErr("");
      try {
        const r = await adminFetch(`/v1/admin/agent-runs/${encodeURIComponent(id)}/control`, {
          method: "POST",
          body: JSON.stringify({ action: "stop" }),
        });
        const b = await parseJson(r);
        if (!r.ok) throw new Error(apiErrorMessage(b) || `HTTP ${r.status}`);
        const d = b?.data;
        const st = d?.status;
        const pc = d?.pipeline_control_json;
        const stopReq = pc && typeof pc === "object" && Boolean(pc.stop_requested);
        if (st === "cancelled") {
          showToast?.("Run cancelled", { type: "success" });
        } else if (stopReq) {
          showToast?.(
            'Stop requested — status may stay "running" until the worker exits',
            { type: "success" },
          );
        } else {
          showToast?.("Cancel requested for that run", { type: "success" });
        }
        await loadTab();
      } catch (e) {
        const msg = formatUserFacingError(e);
        setErr(msg);
        showToast?.(msg, { type: "error" });
      } finally {
        setRunCancelBusyId("");
      }
    },
    [loadTab, showToast],
  );

  const cancelAllAdminAgentRuns = useCallback(async () => {
    const tid = runTenant.trim();
    const pid = runProject.trim();
    const filtered = Boolean(tid || pid);
    const ok = window.confirm(
      filtered
        ? `Send stop to every queued / running / paused agent run${tid ? ` for workspace ${tid}` : ""}${pid ? ` for project ${pid}` : ""}?`
        : "Send stop to EVERY queued / running / paused agent run on this platform (all workspaces). Continue?",
    );
    if (!ok) return;
    setRunCancelAllBusy(true);
    setErr("");
    try {
      const qs = new URLSearchParams();
      if (tid) qs.set("tenant_id", tid);
      if (pid) qs.set("project_id", pid);
      const q = qs.toString();
      const r = await adminFetch(`/v1/admin/agent-runs/cancel-all${q ? `?${q}` : ""}`, { method: "POST" });
      const b = await parseJson(r);
      if (!r.ok) throw new Error(apiErrorMessage(b) || `HTTP ${r.status}`);
      const n = Number(b?.data?.stopped_count);
      showToast?.(
        Number.isFinite(n)
          ? `Stop requested for ${n} run(s) — some rows may stay running until workers exit`
          : "Cancel-all completed",
        { type: "success" },
      );
      await loadTab();
    } catch (e) {
      const msg = formatUserFacingError(e);
      setErr(msg);
      showToast?.(msg, { type: "error" });
    } finally {
      setRunCancelAllBusy(false);
    }
  }, [runTenant, runProject, loadTab, showToast]);

  const lock = () => {
    setAdminKey("");
    setUnlocked(false);
    setDash(null);
    setList(null);
  };

  const stripePanelData = tab === "stripe" && list?.stripe ? list.stripe : null;

  const totalCount = list?.total_count;
  const canPrev = offset > 0;
  const canNext = typeof totalCount === "number" && offset + LIMIT < totalCount;

  return (
    <div className="panel admin-console" style={{ padding: 16, maxWidth: 1200 }}>
      <header style={{ marginBottom: 16 }}>
        <h2 style={{ margin: "0 0 8px" }}>Admin console</h2>
        <p className="subtle" style={{ margin: 0 }}>
          Requires <code>DIRECTOR_ADMIN_API_KEY</code> on the API. Sent as <code>X-Director-Admin-Key</code> (stored in
          session for this browser tab).
        </p>
      </header>

      <div className="action-row" style={{ flexWrap: "wrap", gap: 8, marginBottom: 16, alignItems: "center" }}>
        <input
          type="password"
          autoComplete="off"
          placeholder="Admin API key"
          value={keyInput}
          onChange={(e) => setKeyInput(e.target.value)}
          style={{ minWidth: 220 }}
        />
        <button type="button" disabled={busy} onClick={() => void tryUnlock()}>
          {busy ? "…" : "Unlock"}
        </button>
        <button type="button" className="secondary" onClick={lock}>
          Lock
        </button>
      </div>
      {err ? <p className="err">{err}</p> : null}

      {!unlocked ? (
        <p className="subtle">Enter the admin key and choose Unlock.</p>
      ) : (
        <>
          <nav className="admin-console-nav" style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 16 }}>
            {TABS.map((t) => (
              <button
                key={t.id}
                type="button"
                className={tab === t.id ? "" : "secondary"}
                onClick={() => setTab(t.id)}
              >
                {t.label}
              </button>
            ))}
          </nav>

          {tab === "memberships" ? (
            <div className="panel" style={{ padding: 12, marginBottom: 12, display: "flex", flexWrap: "wrap", gap: 8 }}>
              <label className="subtle">
                Filter workspace id{" "}
                <input
                  value={memTenant}
                  onChange={(e) => setMemTenant(e.target.value)}
                  className="mono"
                  style={{ minWidth: 200 }}
                  title="Full id on hover in tables"
                />
              </label>
              <label className="subtle">
                Filter user id{" "}
                <input value={memUser} onChange={(e) => setMemUser(e.target.value)} className="mono" style={{ minWidth: 120 }} />
              </label>
            </div>
          ) : null}

          {tab === "payments" ? (
            <div className="panel" style={{ padding: 12, marginBottom: 12 }}>
              <label className="subtle">
                Filter tenant id{" "}
                <input value={payTenant} onChange={(e) => setPayTenant(e.target.value)} className="mono" style={{ minWidth: 280 }} />
              </label>
            </div>
          ) : null}

          {tab === "projects" ? (
            <div className="panel" style={{ padding: 12, marginBottom: 12, display: "flex", flexWrap: "wrap", gap: 12 }}>
              <label className="subtle">
                Search title/topic{" "}
                <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="filter…" style={{ minWidth: 200 }} />
              </label>
              <label className="subtle">
                Tenant id{" "}
                <input value={projTenant} onChange={(e) => setProjTenant(e.target.value)} className="mono" style={{ minWidth: 280 }} />
              </label>
            </div>
          ) : null}

          {tab === "runs" ? (
            <div className="panel" style={{ padding: 12, marginBottom: 12, display: "flex", flexWrap: "wrap", gap: 12 }}>
              <label className="subtle">
                Tenant id{" "}
                <input value={runTenant} onChange={(e) => setRunTenant(e.target.value)} className="mono" style={{ minWidth: 280 }} />
              </label>
              <label className="subtle">
                Project id (UUID){" "}
                <input value={runProject} onChange={(e) => setRunProject(e.target.value)} className="mono" style={{ minWidth: 280 }} />
              </label>
            </div>
          ) : null}

          {tab === "jobs" ? (
            <div className="panel" style={{ padding: 12, marginBottom: 12, display: "flex", flexWrap: "wrap", gap: 12 }}>
              <label className="subtle">
                Tenant id{" "}
                <input value={jobTenant} onChange={(e) => setJobTenant(e.target.value)} className="mono" style={{ minWidth: 280 }} />
              </label>
              <label className="subtle">
                Status{" "}
                <input value={jobStatus} onChange={(e) => setJobStatus(e.target.value)} placeholder="e.g. pending" style={{ minWidth: 140 }} />
              </label>
            </div>
          ) : null}

          {(tab === "users" || tab === "tenants") && (
            <div style={{ marginBottom: 12 }}>
              <label className="subtle">
                Search{" "}
                <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="filter…" style={{ minWidth: 200 }} />
              </label>
            </div>
          )}

          {tab !== "dashboard" &&
          tab !== "plans" &&
          tab !== "stripe" &&
          tab !== "tools" &&
          tab !== "database" ? (
            <div className="action-row" style={{ marginBottom: 12, flexWrap: "wrap", gap: 8, alignItems: "center" }}>
              <button type="button" className="secondary" disabled={!canPrev} onClick={() => setOffset((o) => Math.max(0, o - LIMIT))}>
                Previous page
              </button>
              <button type="button" className="secondary" disabled={!canNext} onClick={() => setOffset((o) => o + LIMIT)}>
                Next page
              </button>
              <span className="subtle mono">
                offset {offset} · limit {LIMIT}
                {typeof totalCount === "number" ? ` · total ${totalCount}` : ""}
              </span>
            </div>
          ) : null}

          {busy && !dash && !list ? <p className="subtle">Loading…</p> : null}

          {tab === "dashboard" && dash?.counts ? (
            <div className="usage-totals" style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>
              {Object.entries(dash.counts).map(([k, v]) => (
                <div key={k} className="usage-total-card panel" style={{ padding: 12, minWidth: 140 }}>
                  <span className="usage-total-label">{k.replace(/_/g, " ")}</span>
                  <strong>{String(v)}</strong>
                </div>
              ))}
            </div>
          ) : null}

          {tab === "tools" ? (
            <div className="panel" style={{ padding: 12, marginBottom: 12, maxWidth: 640 }}>
              <h3 style={{ margin: "0 0 8px", fontSize: "1rem" }}>Budget pipeline test</h3>
              <p className="subtle" style={{ margin: "0 0 12px", fontSize: "0.88rem" }}>
                Same run as <code className="mono">scripts/budget_pipeline_test.py</code> — placeholder images; scene
                videos off by default (timeline uses stills). Narration uses your workspace default TTS.{" "}
                <code className="mono">full_video</code> pipeline. Requires a Celery worker consuming the{" "}
                <code className="mono">text</code>, <code className="mono">media</code>, and <code className="mono">compile</code>{" "}
                queues (see repo <code className="mono">celery_app.py</code> — systemd and Launch scripts use{" "}
                <code className="mono">-Q text,media,compile</code>). If agent runs stay <code className="mono">queued</code>, the
                worker is usually not bound to those queues or Redis is unreachable.{" "}
                <strong>Continue budget pipeline</strong> re-queues on the last project id with{" "}
                <code className="mono">continue_from_existing</code> so completed phases are skipped.
              </p>
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                <label className="subtle">
                  Title{" "}
                  <input
                    value={budgetTitle}
                    onChange={(e) => setBudgetTitle(e.target.value)}
                    style={{ width: "100%", marginTop: 4 }}
                  />
                </label>
                <label className="subtle">
                  Topic{" "}
                  <textarea
                    value={budgetTopic}
                    onChange={(e) => setBudgetTopic(e.target.value)}
                    rows={4}
                    style={{ width: "100%", marginTop: 4, fontFamily: "inherit" }}
                  />
                </label>
                <label className="subtle">
                  Target runtime (minutes, 2–120){" "}
                  <input
                    type="number"
                    min={2}
                    max={120}
                    value={budgetRuntime}
                    onChange={(e) => setBudgetRuntime(e.target.value)}
                    style={{ width: 120, marginTop: 4 }}
                  />
                </label>
                <label className="subtle" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                  Mode
                  <select value={budgetMode} onChange={(e) => setBudgetMode(e.target.value)} style={{ maxWidth: 220 }}>
                    <option value="hands-off">Hands-off (unattended)</option>
                    <option value="auto">Auto</option>
                  </select>
                </label>
                <label className="subtle" style={{ display: "flex", gap: 8, alignItems: "flex-start", marginTop: 4 }}>
                  <input
                    type="checkbox"
                    checked={budgetProductionMedia}
                    onChange={(e) => setBudgetProductionMedia(e.target.checked)}
                    style={{ marginTop: 3 }}
                  />
                  <span style={{ lineHeight: 1.35 }}>
                    Production media — use workspace image/video providers and enable auto scene videos (matches a
                    typical Studio full_video run; costs paid APIs). Leave off for cheap smoke (placeholder + local
                    FFmpeg, no scene MP4 jobs).
                  </span>
                </label>
                <label className="subtle" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                  Picture frame (aspect ratio)
                  <select
                    value={budgetFrameAspect}
                    onChange={(e) => setBudgetFrameAspect(e.target.value === "9:16" ? "9:16" : "16:9")}
                    style={{ maxWidth: 220 }}
                  >
                    <option value="16:9">16:9 landscape</option>
                    <option value="9:16">9:16 portrait (shorts)</option>
                  </select>
                </label>
                <p className="subtle" style={{ margin: 0, fontSize: "0.85rem" }}>
                  <input type="hidden" name="director-budget-workspace-id" value={resolvedBudgetWorkspaceId} readOnly />
                  Target workspace:{" "}
                  {resolvedBudgetWorkspaceId ? (
                    <code className="mono" title={resolvedBudgetWorkspaceId}>
                      {formatShortId(resolvedBudgetWorkspaceId)}
                    </code>
                  ) : (
                    <span>
                      <em>none in session</em> — API uses server <code className="mono">DEFAULT_TENANT_ID</code>
                    </span>
                  )}
                </p>
                <label className="subtle" style={{ display: "block", marginTop: 8 }}>
                  Override workspace id (optional){" "}
                  <input
                    className="mono"
                    value={budgetWorkspaceIdOverride}
                    onChange={(e) => setBudgetWorkspaceIdOverride(e.target.value)}
                    placeholder="00000000-0000-0000-0000-000000000001"
                    autoComplete="off"
                    spellCheck="false"
                    style={{ width: "100%", marginTop: 4, fontSize: "0.85rem" }}
                  />
                  <span style={{ display: "block", marginTop: 4, fontSize: "0.78rem", lineHeight: 1.4 }}>
                    When set, this is sent as <code className="mono">tenant_id</code> instead of the session workspace.
                    Use if the default tenant is missing in the DB or you need a specific workspace for budget /
                    production runs.
                  </span>
                </label>
                <label className="subtle" style={{ display: "block", marginTop: 6 }}>
                  Project id (last budget run — updates each time you queue Run or Continue; edit or paste if needed){" "}
                  <input
                    className="mono"
                    value={budgetProjectId}
                    onChange={(e) => setBudgetProjectId(e.target.value)}
                    onBlur={() => {
                      const t = budgetProjectId.trim();
                      if (t) persistLastRanBudgetProjectId(t);
                    }}
                    placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
                    autoComplete="off"
                    spellCheck="false"
                    style={{ width: "100%", marginTop: 4, fontSize: "0.85rem" }}
                  />
                </label>
                <div className="action-row" style={{ gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                  <button type="button" disabled={budgetBusy} onClick={() => void runBudgetPipeline()}>
                    {budgetBusy ? "Queueing…" : "Run budget pipeline"}
                  </button>
                  <button
                    type="button"
                    className="secondary"
                    disabled={budgetBusy}
                    title="Enqueue another agent run on the project id above; worker skips steps already done."
                    onClick={() => void continueBudgetPipeline()}
                  >
                    {budgetBusy ? "Queueing…" : "Continue budget pipeline"}
                  </button>
                </div>
                {budgetErr ? <p className="err">{budgetErr}</p> : null}
                {budgetLast?.agent_run?.id ? (
                  <div
                    className="panel"
                    style={{ marginTop: 8, padding: 10, background: "var(--panel-elevated, rgba(0,0,0,0.04))" }}
                  >
                    <p className="subtle" style={{ margin: "0 0 8px", fontSize: "0.85rem" }}>
                      Queued. Poll status until terminal state:
                    </p>
                    <p className="mono" style={{ margin: "4px 0", fontSize: "0.82rem", wordBreak: "break-all" }}>
                      Agent run: {budgetLast.agent_run.id}
                    </p>
                    {budgetLast.project?.id ? (
                      <p className="mono" style={{ margin: "4px 0", fontSize: "0.82rem", wordBreak: "break-all" }}>
                        Project: {budgetLast.project.id}
                      </p>
                    ) : null}
                    {(() => {
                      const aid = String(budgetLast.agent_run.id);
                      const st = budgetRunStatuses[aid] ?? "";
                      const term = budgetAgentRunIsTerminal(st);
                      const rowBusy = budgetRowBusy === aid;
                      return (
                        <div
                          className="action-row"
                          style={{ gap: 8, marginTop: 8, flexWrap: "wrap", alignItems: "center" }}
                        >
                          <span className="subtle" style={{ fontSize: "0.82rem" }}>
                            Status: {st ? <strong>{st}</strong> : <em>loading…</em>}
                          </span>
                          {term ? (
                            <button
                              type="button"
                              className="secondary"
                              disabled={rowBusy}
                              onClick={() => void deleteBudgetAgentRun(aid)}
                            >
                              Delete run
                            </button>
                          ) : (
                            <button
                              type="button"
                              className="secondary"
                              disabled={rowBusy}
                              onClick={() => void stopBudgetAgentRun(aid)}
                            >
                              Stop run
                            </button>
                          )}
                        </div>
                      );
                    })()}
                    {budgetLast.poll_url ? (
                      <p style={{ margin: "8px 0 0" }}>
                        <a href={apiPath(budgetLast.poll_url)} target="_blank" rel="noreferrer">
                          Open {budgetLast.poll_url}
                        </a>
                      </p>
                    ) : null}
                    {budgetLast.hint ? <p className="subtle" style={{ margin: "8px 0 0", fontSize: "0.82rem" }}>{budgetLast.hint}</p> : null}
                  </div>
                ) : null}
                {budgetRunHistory.length > 0 ? (
                  <div style={{ marginTop: 12 }}>
                    <p className="subtle" style={{ margin: "0 0 6px", fontSize: "0.85rem" }}>
                      Recent runs (saved in this browser)
                    </p>
                    <ul style={{ margin: 0, padding: 0, listStyle: "none", fontSize: "0.82rem" }}>
                      {budgetRunHistory
                        .slice()
                        .reverse()
                        .map((row, idx) => {
                          const aid = row.agent_run_id ? String(row.agent_run_id) : "";
                          const st = aid ? budgetRunStatuses[aid] ?? "" : "";
                          const term = budgetAgentRunIsTerminal(st);
                          const rowBusy = aid && budgetRowBusy === aid;
                          return (
                            <li
                              key={`${row.ts}-${row.agent_run_id}-${idx}`}
                              style={{
                                marginBottom: 8,
                                display: "flex",
                                gap: 8,
                                alignItems: "flex-start",
                                flexWrap: "wrap",
                              }}
                            >
                              <div style={{ flex: 1, minWidth: 0 }}>
                                <span className="subtle">{row.ts ? String(row.ts).replace("T", " ").slice(0, 19) : ""}</span>
                                {aid ? (
                                  <>
                                    {" "}
                                    <span className="mono" style={{ wordBreak: "break-all" }}>
                                      {aid}
                                    </span>
                                    {st ? <span className="subtle" style={{ marginLeft: 6 }}>({st})</span> : null}
                                  </>
                                ) : null}
                              </div>
                              {aid ? (
                                term ? (
                                  <button
                                    type="button"
                                    className="secondary"
                                    style={{ fontSize: "0.75rem", flexShrink: 0 }}
                                    disabled={Boolean(rowBusy)}
                                    onClick={() => void deleteBudgetAgentRun(aid)}
                                  >
                                    Delete
                                  </button>
                                ) : (
                                  <button
                                    type="button"
                                    className="secondary"
                                    style={{ fontSize: "0.75rem", flexShrink: 0 }}
                                    disabled={Boolean(rowBusy)}
                                    onClick={() => void stopBudgetAgentRun(aid)}
                                  >
                                    Stop
                                  </button>
                                )
                              ) : null}
                            </li>
                          );
                        })}
                    </ul>
                  </div>
                ) : null}
              </div>
            </div>
          ) : null}

          {tab === "database" ? (
            <div className="panel" style={{ padding: 14, marginBottom: 12, maxWidth: 760 }}>
              <h3 style={{ margin: "0 0 8px", fontSize: "1rem" }}>PostgreSQL backup &amp; restore</h3>
              <p className="subtle" style={{ margin: "0 0 12px", fontSize: "0.88rem", lineHeight: 1.45 }}>
                Uses <code className="mono">pg_dump</code> / <code className="mono">psql</code> on the API host against{" "}
                <code className="mono">DATABASE_URL</code>. Backup and restore require{" "}
                <strong>
                  <code className="mono">X-Director-Admin-Key</code>
                </strong>{" "}
                (the same value as <code className="mono">DIRECTOR_ADMIN_API_KEY</code> in the server env) — paste it in
                the admin key field above. Operators enable features with env flags (see <code className="mono">.env.example</code>
                ).
              </p>
              {dbStatus ? (
                <ul className="subtle" style={{ margin: "0 0 14px", paddingLeft: 18, fontSize: "0.85rem" }}>
                  <li>Backup enabled: {dbStatus.backup_enabled ? "yes" : "no"}</li>
                  <li>Restore enabled: {dbStatus.restore_enabled ? "yes" : "no"}</li>
                  <li>Restore confirm configured: {dbStatus.restore_confirm_configured ? "yes" : "no"}</li>
                  <li>
                    <code className="mono">pg_dump</code> on PATH: {dbStatus.pg_dump_available ? "yes" : "no"}
                  </li>
                  <li>
                    <code className="mono">psql</code> on PATH: {dbStatus.psql_available ? "yes" : "no"}
                  </li>
                  <li>
                    Max restore upload: {(dbStatus.restore_max_bytes / (1024 * 1024)).toFixed(0)} MiB
                  </li>
                </ul>
              ) : (
                <p className="subtle">Loading status…</p>
              )}
              <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                <div className="action-row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                  <button
                    type="button"
                    disabled={dbBusy || !dbStatus?.backup_enabled || !dbStatus?.pg_dump_available}
                    onClick={() => void runDbBackup()}
                  >
                    {dbBusy ? "Working…" : "Download SQL backup"}
                  </button>
                  <span className="subtle" style={{ fontSize: "0.82rem" }}>
                    Plain SQL; may take several minutes for large databases.
                  </span>
                </div>
                {dbStatus && !dbStatus.backup_enabled ? (
                  <p className="subtle" style={{ margin: 0, fontSize: "0.82rem" }}>
                    Backup is off: set <code className="mono">DIRECTOR_ADMIN_DB_BACKUP_ENABLED=1</code> on the API host and restart the API.
                  </p>
                ) : null}
                {dbStatus?.backup_enabled && !dbStatus?.pg_dump_available ? (
                  <p className="subtle" style={{ margin: 0, fontSize: "0.82rem" }}>
                    Install PostgreSQL client tools on the API host so <code className="mono">pg_dump</code> is on{" "}
                    <code className="mono">PATH</code> (e.g. Debian/Ubuntu: <code className="mono">apt install postgresql-client</code>
                    ), then restart the API.
                  </p>
                ) : null}
                <hr style={{ border: 0, borderTop: "1px solid var(--border-subtle, #333)", margin: "4px 0" }} />
                <p className="subtle" style={{ margin: 0, fontSize: "0.85rem", lineHeight: 1.45 }}>
                  <strong>Restore</strong> runs <code className="mono">psql -v ON_ERROR_STOP=1 -f</code> against the live DB.
                  Stop traffic and take a backup first. Paste the exact <code className="mono">DIRECTOR_ADMIN_DB_RESTORE_CONFIRM</code>{" "}
                  phrase from the server environment.
                </p>
                <label className="subtle" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                  Confirmation phrase
                  <input
                    type="password"
                    autoComplete="off"
                    value={restoreConfirm}
                    onChange={(e) => setRestoreConfirm(e.target.value)}
                    placeholder="matches DIRECTOR_ADMIN_DB_RESTORE_CONFIRM"
                    style={{ maxWidth: 480, marginTop: 4 }}
                  />
                </label>
                <label className="subtle" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                  SQL dump file
                  <input
                    data-director-db-restore-file="1"
                    type="file"
                    accept=".sql,text/plain"
                    onChange={(e) => setRestoreFile(e.target.files?.[0] ?? null)}
                    style={{ marginTop: 4 }}
                  />
                </label>
                <div className="action-row" style={{ gap: 8, flexWrap: "wrap" }}>
                  <button
                    type="button"
                    className="secondary"
                    disabled={dbBusy || !dbStatus?.restore_enabled || !dbStatus?.restore_confirm_configured}
                    onClick={() => void runDbRestore()}
                  >
                    {dbBusy ? "Working…" : "Run restore from file"}
                  </button>
                </div>
                {dbErr ? <p className="err">{dbErr}</p> : null}
                {dbMsg ? (
                  <p className="subtle" style={{ margin: 0, color: "var(--ok, #8fd694)" }}>
                    {dbMsg}
                  </p>
                ) : null}
              </div>
            </div>
          ) : null}

          {tab === "users" && list?.users ? (
            <AdminUsersTable data={list} onRefresh={loadTab} showToast={showToast} />
          ) : null}
          {tab === "tenants" && list?.tenants ? (
            <AdminTenantsTable data={list} onRefresh={loadTab} showToast={showToast} />
          ) : null}
          {tab === "memberships" && list?.memberships ? (
            <AdminMembershipsTable data={list} onRefresh={loadTab} showToast={showToast} />
          ) : null}
          {tab === "plans" && list != null ? (
            <AdminPlansTable data={list} onRefresh={loadTab} showToast={showToast} entitlementDefs={entitlementDefs} />
          ) : null}
          {tab === "billing" && list != null ? (
            <AdminBillingTable data={list} onRefresh={loadTab} showToast={showToast} entitlementDefs={entitlementDefs} />
          ) : null}
          {tab === "stripe" && stripePanelData ? (
            <AdminStripePanel data={stripePanelData} onRefresh={loadTab} showToast={showToast} />
          ) : null}
          {tab === "payments" && list != null ? (
            <AdminPaymentsTable events={list.events} totalCount={list.total_count} />
          ) : null}
          {tab === "projects" && list?.projects ? (
            <AdminProjectsTable data={list} onRefresh={loadTab} showToast={showToast} />
          ) : null}
          {tab === "runs" && list?.agent_runs ? (
            <AdminAgentRunsTable
              rows={list.agent_runs}
              onCancelRun={cancelAdminAgentRun}
              cancelRunBusyId={runCancelBusyId}
              onCancelAll={cancelAllAdminAgentRuns}
              cancelAllBusy={runCancelAllBusy}
            />
          ) : null}
          {tab === "jobs" && list?.jobs ? (
            <AdminGenericTable
              rows={list.jobs}
              columns={["id", "tenant_name", "type", "status", "created_at"]}
              getCellTitle={(row, c) =>
                c === "tenant_name" && row.tenant_id ? `Workspace id: ${row.tenant_id}` : undefined
              }
            />
          ) : null}
        </>
      )}
    </div>
  );
}

function AdminStripePanel({ data, onRefresh, showToast }) {
  const eff = data?.effective || {};
  const over = data?.database_overrides || {};
  const [publishable, setPublishable] = useState(over.stripe_publishable_key || "");
  const [successUrl, setSuccessUrl] = useState(over.billing_success_url || "");
  const [cancelUrl, setCancelUrl] = useState(over.billing_cancel_url || "");
  const [priceMonthly, setPriceMonthly] = useState(over.stripe_price_studio_monthly || "");
  const [sk, setSk] = useState("");
  const [wh, setWh] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const o = data?.database_overrides || {};
    setPublishable(o.stripe_publishable_key || "");
    setSuccessUrl(o.billing_success_url || "");
    setCancelUrl(o.billing_cancel_url || "");
    setPriceMonthly(o.stripe_price_studio_monthly || "");
    setSk("");
    setWh("");
  }, [data]);

  const clearSecret = async (key) => {
    setSaving(true);
    try {
      const r = await adminFetch("/v1/admin/stripe-settings", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [key]: "" }),
      });
      const j = await parseJson(r);
      if (!r.ok) {
        showToast?.(apiErrorMessage(j) || "Failed", { type: "error" });
        return;
      }
      showToast?.("Cleared database override", { type: "success" });
      await onRefresh();
    } catch (e) {
      showToast?.(formatUserFacingError(e), { type: "error" });
    } finally {
      setSaving(false);
    }
  };

  const save = async () => {
    setSaving(true);
    try {
      const body = {
        stripe_publishable_key: publishable.trim() === "" ? "" : publishable.trim(),
        billing_success_url: successUrl.trim() === "" ? "" : successUrl.trim(),
        billing_cancel_url: cancelUrl.trim() === "" ? "" : cancelUrl.trim(),
        stripe_price_studio_monthly: priceMonthly.trim() === "" ? "" : priceMonthly.trim(),
      };
      if (sk.trim()) body.stripe_secret_key = sk.trim();
      if (wh.trim()) body.stripe_webhook_secret = wh.trim();
      const r = await adminFetch("/v1/admin/stripe-settings", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const j = await parseJson(r);
      if (!r.ok) {
        showToast?.(apiErrorMessage(j) || "Save failed", { type: "error" });
        return;
      }
      showToast?.("Stripe settings saved", { type: "success" });
      await onRefresh();
    } catch (e) {
      showToast?.(formatUserFacingError(e), { type: "error" });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="panel" style={{ padding: 16, maxWidth: 720 }}>
      <p className="subtle" style={{ marginBottom: 12 }}>
        Saved values override <code>.env</code> for this deployment. Empty override fields fall back to the environment.
        Secret keys are never displayed; set new values below or clear the database override.
      </p>
      <h3 style={{ marginTop: 0 }}>Effective (merged)</h3>
      <ul className="subtle" style={{ fontSize: "0.88rem", marginBottom: 16, paddingLeft: 18 }}>
        <li>
          Publishable key: <code>{eff.stripe_publishable_key || "—"}</code>
        </li>
        <li>Success URL: {eff.billing_success_url || "—"}</li>
        <li>Cancel URL: {eff.billing_cancel_url || "—"}</li>
        <li>Studio monthly Stripe Price id: {eff.stripe_price_studio_monthly || "—"}</li>
      </ul>
      <h3>Database overrides</h3>
      <label style={{ display: "block", marginBottom: 10 }}>
        <span className="subtle" style={{ display: "block", marginBottom: 4 }}>
          Stripe publishable key (pk_…)
        </span>
        <input value={publishable} onChange={(e) => setPublishable(e.target.value)} className="mono" style={{ width: "100%" }} />
      </label>
      <label style={{ display: "block", marginBottom: 10 }}>
        <span className="subtle" style={{ display: "block", marginBottom: 4 }}>
          Checkout success URL
        </span>
        <input value={successUrl} onChange={(e) => setSuccessUrl(e.target.value)} className="mono" style={{ width: "100%" }} />
      </label>
      <label style={{ display: "block", marginBottom: 10 }}>
        <span className="subtle" style={{ display: "block", marginBottom: 4 }}>
          Checkout cancel URL
        </span>
        <input value={cancelUrl} onChange={(e) => setCancelUrl(e.target.value)} className="mono" style={{ width: "100%" }} />
      </label>
      <label style={{ display: "block", marginBottom: 10 }}>
        <span className="subtle" style={{ display: "block", marginBottom: 4 }}>
          Default Price id for <code>studio_monthly</code> (syncs plan on save)
        </span>
        <input value={priceMonthly} onChange={(e) => setPriceMonthly(e.target.value)} className="mono" style={{ width: "100%" }} placeholder="price_…" />
      </label>
      <div style={{ marginBottom: 10 }}>
        <span className="subtle" style={{ display: "block", marginBottom: 4 }}>
          Stripe secret key (sk_…)
          {over.stripe_secret_key_set ? (
            <button type="button" className="secondary" style={{ marginLeft: 8 }} disabled={saving} onClick={() => void clearSecret("stripe_secret_key")}>
              Clear from DB
            </button>
          ) : null}
        </span>
        <input
          type="password"
          autoComplete="off"
          value={sk}
          onChange={(e) => setSk(e.target.value)}
          placeholder={over.stripe_secret_key_set ? "Enter new secret to replace stored value" : "sk_…"}
          className="mono"
          style={{ width: "100%" }}
        />
      </div>
      <div style={{ marginBottom: 16 }}>
        <span className="subtle" style={{ display: "block", marginBottom: 4 }}>
          Webhook signing secret (whsec_…)
          {over.stripe_webhook_secret_set ? (
            <button type="button" className="secondary" style={{ marginLeft: 8 }} disabled={saving} onClick={() => void clearSecret("stripe_webhook_secret")}>
              Clear from DB
            </button>
          ) : null}
        </span>
        <input
          type="password"
          autoComplete="off"
          value={wh}
          onChange={(e) => setWh(e.target.value)}
          placeholder={over.stripe_webhook_secret_set ? "Enter new secret to replace" : "whsec_…"}
          className="mono"
          style={{ width: "100%" }}
        />
      </div>
      <button type="button" disabled={saving} onClick={() => void save()}>
        {saving ? "Saving…" : "Save"}
      </button>
    </div>
  );
}

function adminColumnHeaderLabel(c) {
  if (c === "tenant_name") return "tenant";
  return c;
}

function agentRunStatusCancellable(st) {
  return ["queued", "running", "paused"].includes(String(st || "").trim());
}

function AdminAgentRunsTable({ rows, onCancelRun, cancelRunBusyId, onCancelAll, cancelAllBusy }) {
  const r = Array.isArray(rows) ? rows : [];
  return (
    <div>
      <div className="action-row" style={{ marginBottom: 12, flexWrap: "wrap", gap: 8, alignItems: "center" }}>
        <button
          type="button"
          className="secondary"
          disabled={cancelAllBusy}
          onClick={() => void onCancelAll()}
          title="POST /v1/admin/agent-runs/cancel-all — uses tenant / project filters when set; otherwise all active runs."
        >
          {cancelAllBusy ? "…" : "Cancel all (filtered)"}
        </button>
        <span className="subtle" style={{ fontSize: "0.78rem", maxWidth: 520, lineHeight: 1.4 }}>
          Sends the same stop signal as per-row Cancel. Rows may show &quot;running&quot; with stop requested until workers
          finish. Empty filters = every queued / running / paused run on the server.
        </span>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table className="usage-table" style={{ fontSize: "0.82rem", width: "100%" }}>
          <thead>
            <tr>
              <th style={{ textAlign: "left" }}>id</th>
              <th style={{ textAlign: "left" }}>tenant</th>
              <th style={{ textAlign: "left" }}>project_id</th>
              <th style={{ textAlign: "left" }}>status</th>
              <th style={{ textAlign: "left" }}>created_at</th>
              <th style={{ textAlign: "left" }}>actions</th>
            </tr>
          </thead>
          <tbody>
            {r.map((row) => {
              const rid = row.id != null ? String(row.id) : "";
              const stopRequested = Boolean(row.stop_requested);
              return (
                <tr key={rid || JSON.stringify(row)}>
                  <td title={rid}>
                    {rid.length > 14 ? <TruncId id={rid} /> : rid || "—"}
                  </td>
                  <td className={row.tenant_name ? undefined : "mono"} title={row.tenant_id ? `Workspace id: ${row.tenant_id}` : undefined}>
                    {row.tenant_name || "—"}
                  </td>
                  <td className="mono" title={row.project_id}>
                    {row.project_id != null && String(row.project_id).length > 14 ? (
                      <TruncId id={String(row.project_id)} />
                    ) : (
                      row.project_id ?? "—"
                    )}
                  </td>
                  <td className="mono">
                    {row.status ?? "—"}
                    {stopRequested ? (
                      <span
                        className="subtle"
                        style={{ display: "block", fontSize: "0.75rem", marginTop: 2 }}
                        title="Worker will set status to cancelled at the next checkpoint"
                      >
                        stop requested — finishing
                      </span>
                    ) : null}
                  </td>
                  <td className="mono">{row.created_at ?? "—"}</td>
                  <td>
                    {agentRunStatusCancellable(row.status) ? (
                      stopRequested ? (
                        <span className="subtle" title="Stop already sent; waiting for worker">
                          Stopping…
                        </span>
                      ) : (
                        <button
                          type="button"
                          className="secondary"
                          disabled={cancelRunBusyId === rid}
                          onClick={() => void onCancelRun(rid)}
                        >
                          {cancelRunBusyId === rid ? "…" : "Cancel"}
                        </button>
                      )
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function AdminGenericTable({ rows, columns, getCellTitle }) {
  const r = Array.isArray(rows) ? rows : [];
  const isIdColumn = (c) => c === "id" || (typeof c === "string" && c.endsWith("_id"));

  return (
    <div style={{ overflowX: "auto" }}>
      <table className="usage-table" style={{ fontSize: "0.82rem", width: "100%" }}>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c} style={{ textAlign: "left" }}>
                {adminColumnHeaderLabel(c)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {r.map((row) => (
            <tr key={row.id ?? JSON.stringify(row)}>
              {columns.map((c) => {
                const v = row[c];
                const titleAttr =
                  typeof getCellTitle === "function" ? getCellTitle(row, c) ?? undefined : undefined;
                if (v == null) {
                  return (
                    <td key={c} className="mono" title={titleAttr}>
                      —
                    </td>
                  );
                }
                const s = String(v);
                if (isIdColumn(c) && s.length > 14) {
                  return (
                    <td key={c} title={titleAttr}>
                      <TruncId id={s} />
                    </td>
                  );
                }
                return (
                  <td key={c} className={c === "tenant_name" ? undefined : "mono"} title={titleAttr}>
                    {s}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AdminUsersTable({ data, onRefresh, showToast }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [createFullName, setCreateFullName] = useState("");
  const [createCity, setCreateCity] = useState("");
  const [createState, setCreateState] = useState("");
  const [createCountry, setCreateCountry] = useState("");
  const [createZip, setCreateZip] = useState("");
  const [busy, setBusy] = useState(false);
  const [detailId, setDetailId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [detailBusy, setDetailBusy] = useState(false);
  const [patchEmail, setPatchEmail] = useState("");
  const [patchFullName, setPatchFullName] = useState("");
  const [patchCity, setPatchCity] = useState("");
  const [patchState, setPatchState] = useState("");
  const [patchCountry, setPatchCountry] = useState("");
  const [patchZip, setPatchZip] = useState("");
  const [patchUsePlatformCreds, setPatchUsePlatformCreds] = useState(false);
  const [newPassword, setNewPassword] = useState("");

  const loadDetail = async (id) => {
    setDetailId(id);
    setDetailBusy(true);
    setDetail(null);
    try {
      const r = await adminFetch(`/v1/admin/users/${id}`);
      const body = await parseJson(r);
      if (!r.ok) {
        showToast?.(apiErrorMessage(body) || "Failed", { type: "error" });
        return;
      }
      setDetail(body.data);
      const d = body.data || {};
      setPatchEmail(d.email || "");
      setPatchFullName(d.full_name || "");
      setPatchCity(d.city || "");
      setPatchState(d.state || "");
      setPatchCountry(d.country || "");
      setPatchZip(d.zip_code || "");
      setPatchUsePlatformCreds(Boolean(d.use_platform_api_credentials));
    } catch (e) {
      showToast?.(formatUserFacingError(e), { type: "error" });
    } finally {
      setDetailBusy(false);
    }
  };

  const create = async () => {
    setBusy(true);
    try {
      const payload = {
        email,
        password,
        ...(createFullName.trim() ? { full_name: createFullName.trim() } : {}),
        ...(createCity.trim() ? { city: createCity.trim() } : {}),
        ...(createState.trim() ? { state: createState.trim() } : {}),
        ...(createCountry.trim() ? { country: createCountry.trim() } : {}),
        ...(createZip.trim() ? { zip_code: createZip.trim() } : {}),
      };
      const r = await adminFetch("/v1/admin/users", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      const body = await parseJson(r);
      if (!r.ok) {
        showToast?.(apiErrorMessage(body) || "Failed", { type: "error" });
        return;
      }
      showToast?.("User created", { type: "success" });
      setEmail("");
      setPassword("");
      setCreateFullName("");
      setCreateCity("");
      setCreateState("");
      setCreateCountry("");
      setCreateZip("");
      await onRefresh();
    } catch (e) {
      showToast?.(formatUserFacingError(e), { type: "error" });
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id) => {
    if (!window.confirm(`Delete user ${id}?`)) return;
    const r = await adminFetch(`/v1/admin/users/${id}`, { method: "DELETE" });
    const body = await parseJson(r);
    if (!r.ok) showToast?.(apiErrorMessage(body) || "Failed", { type: "error" });
    else {
      showToast?.("Deleted", { type: "success" });
      if (detailId === id) {
        setDetailId(null);
        setDetail(null);
      }
      await onRefresh();
    }
  };

  const saveEmail = async () => {
    if (!detailId) return;
    const r = await adminFetch(`/v1/admin/users/${detailId}`, {
      method: "PATCH",
      body: JSON.stringify({ email: patchEmail.trim() }),
    });
    const body = await parseJson(r);
    if (!r.ok) showToast?.(apiErrorMessage(body) || "Failed", { type: "error" });
    else {
      showToast?.("Email updated", { type: "success" });
      await loadDetail(detailId);
      await onRefresh();
    }
  };

  const saveProfile = async () => {
    if (!detailId) return;
    const r = await adminFetch(`/v1/admin/users/${detailId}`, {
      method: "PATCH",
      body: JSON.stringify({
        full_name: patchFullName.trim() || null,
        city: patchCity.trim() || null,
        state: patchState.trim() || null,
        country: patchCountry.trim() || null,
        zip_code: patchZip.trim() || null,
      }),
    });
    const body = await parseJson(r);
    if (!r.ok) showToast?.(apiErrorMessage(body) || "Failed", { type: "error" });
    else {
      showToast?.("Profile updated", { type: "success" });
      await loadDetail(detailId);
      await onRefresh();
    }
  };

  const savePlatformCreds = async () => {
    if (!detailId) return;
    const r = await adminFetch(`/v1/admin/users/${detailId}`, {
      method: "PATCH",
      body: JSON.stringify({ use_platform_api_credentials: patchUsePlatformCreds }),
    });
    const body = await parseJson(r);
    if (!r.ok) showToast?.(apiErrorMessage(body) || "Failed", { type: "error" });
    else {
      showToast?.("Platform credentials preference saved", { type: "success" });
      await loadDetail(detailId);
      await onRefresh();
    }
  };

  const savePassword = async () => {
    if (!detailId || newPassword.length < 8) {
      showToast?.("Password must be at least 8 characters", { type: "error" });
      return;
    }
    const r = await adminFetch(`/v1/admin/users/${detailId}/password`, {
      method: "POST",
      body: JSON.stringify({ password: newPassword }),
    });
    const body = await parseJson(r);
    if (!r.ok) showToast?.(apiErrorMessage(body) || "Failed", { type: "error" });
    else {
      showToast?.("Password set", { type: "success" });
      setNewPassword("");
    }
  };

  return (
    <>
      <div className="panel" style={{ padding: 12, marginBottom: 16 }}>
        <strong>Create user</strong>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 8 }}>
          <input placeholder="email" value={email} onChange={(e) => setEmail(e.target.value)} />
          <input type="password" placeholder="password (8+)" value={password} onChange={(e) => setPassword(e.target.value)} />
          <button type="button" disabled={busy} onClick={() => void create()}>
            Create
          </button>
        </div>
        <p className="subtle" style={{ margin: "8px 0 4px" }}>
          Optional profile (stored on user)
        </p>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
          <input placeholder="Full name" value={createFullName} onChange={(e) => setCreateFullName(e.target.value)} style={{ minWidth: 160 }} />
          <input placeholder="City" value={createCity} onChange={(e) => setCreateCity(e.target.value)} style={{ minWidth: 120 }} />
          <input placeholder="State" value={createState} onChange={(e) => setCreateState(e.target.value)} style={{ minWidth: 80 }} />
          <input placeholder="Country" value={createCountry} onChange={(e) => setCreateCountry(e.target.value)} style={{ minWidth: 100 }} />
          <input placeholder="Zip" value={createZip} onChange={(e) => setCreateZip(e.target.value)} style={{ minWidth: 80 }} />
        </div>
      </div>
      <p className="subtle">Total: {data.total_count}</p>
      <div style={{ overflowX: "auto" }}>
        <table className="usage-table" style={{ fontSize: "0.82rem", width: "100%" }}>
          <thead>
            <tr>
              <th>Name</th>
              <th>Email</th>
              <th>Platform keys</th>
              <th>id</th>
              <th>created</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {data.users.map((u) => (
              <tr key={u.id}>
                <td>{u.full_name?.trim() ? u.full_name : "—"}</td>
                <td>{u.email}</td>
                <td className="mono">{u.use_platform_api_credentials ? "yes" : "—"}</td>
                <td>
                  <TruncId id={u.id} />
                </td>
                <td className="mono">{u.created_at ?? "—"}</td>
                <td>
                  <button type="button" className="secondary" style={{ marginRight: 6 }} onClick={() => void loadDetail(u.id)}>
                    Detail
                  </button>
                  <button type="button" className="secondary" onClick={() => void remove(u.id)}>
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {detailId ? (
        <div className="panel" style={{ padding: 12, marginTop: 16 }}>
          <strong>
            User: {detail?.full_name?.trim() || detail?.email || "—"}{" "}
            <span className="subtle" style={{ fontWeight: 400 }}>
              (
              <TruncId id={detail?.id} />)
            </span>
          </strong>
          {!detailBusy && detail ? (
            <p className="subtle" style={{ margin: "6px 0 0", lineHeight: 1.5 }}>
              {detail.memberships?.length ?
                <>
                  Tenant id{detail.memberships.length > 1 ? "s" : ""}:{" "}
                  {detail.memberships.map((m, i) => (
                    <span key={m.membership_id}>
                      {i > 0 ? ", " : null}
                      <code className="mono" title={m.tenant_id}>
                        {m.tenant_id}
                      </code>
                    </span>
                  ))}
                </>
              : "No tenant memberships."}
            </p>
          ) : null}
          {detailBusy ? <p className="subtle">Loading…</p> : null}
          {detail?.memberships?.length ? (
            <div style={{ marginTop: 8 }}>
              <p className="subtle">Memberships</p>
              <div style={{ overflowX: "auto" }}>
                <table className="usage-table" style={{ fontSize: "0.82rem", width: "100%" }}>
                  <thead>
                    <tr>
                      <th>Workspace</th>
                      <th>Workspace id</th>
                      <th>Role</th>
                      <th>Created</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.memberships.map((m) => (
                      <tr key={m.membership_id}>
                        <td>{m.tenant_name ?? "—"}</td>
                        <td>
                          <TruncId id={m.tenant_id} />
                        </td>
                        <td>{m.role}</td>
                        <td className="mono">{m.created_at ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : null}
          {!detailBusy && detail ? (
            <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 8, maxWidth: 520 }}>
              <label className="subtle">
                Email{" "}
                <input value={patchEmail} onChange={(e) => setPatchEmail(e.target.value)} style={{ width: "100%" }} />
              </label>
              <button type="button" onClick={() => void saveEmail()}>
                Save email
              </button>
              <p className="subtle" style={{ margin: "4px 0 0" }}>
                Profile &amp; address
              </p>
              <input placeholder="Full name" value={patchFullName} onChange={(e) => setPatchFullName(e.target.value)} style={{ width: "100%" }} />
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                <input placeholder="City" value={patchCity} onChange={(e) => setPatchCity(e.target.value)} style={{ flex: "1 1 120px" }} />
                <input placeholder="State" value={patchState} onChange={(e) => setPatchState(e.target.value)} style={{ flex: "1 1 80px" }} />
                <input placeholder="Country" value={patchCountry} onChange={(e) => setPatchCountry(e.target.value)} style={{ flex: "1 1 100px" }} />
                <input placeholder="Zip code" value={patchZip} onChange={(e) => setPatchZip(e.target.value)} style={{ flex: "1 1 80px" }} />
              </div>
              <button type="button" className="secondary" onClick={() => void saveProfile()}>
                Save profile
              </button>
              <label style={{ display: "flex", alignItems: "flex-start", gap: 8, marginTop: 4 }}>
                <input
                  type="checkbox"
                  checked={patchUsePlatformCreds}
                  onChange={(e) => setPatchUsePlatformCreds(e.target.checked)}
                />
                <span className="subtle" style={{ lineHeight: 1.45 }}>
                  Use platform API credentials — when enabled, optional API keys from the deployment&apos;s source
                  workspace (<code className="mono">DIRECTOR_PLATFORM_CREDENTIALS_SOURCE_TENANT_ID</code>) apply for
                  this user if their workspace has not saved its own value. Users do not see those keys in Settings.
                </span>
              </label>
              <button type="button" className="secondary" onClick={() => void savePlatformCreds()}>
                Save platform-keys option
              </button>
              <label className="subtle">
                New password (min 8){" "}
                <input
                  type="password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  style={{ width: "100%" }}
                />
              </label>
              <button type="button" className="secondary" onClick={() => void savePassword()}>
                Set password
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
    </>
  );
}

function AdminTenantsTable({ data, onRefresh, showToast }) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(null);
  const [editName, setEditName] = useState("");
  const [editSlug, setEditSlug] = useState("");

  const create = async () => {
    setBusy(true);
    try {
      const r = await adminFetch("/v1/admin/tenants", { method: "POST", body: JSON.stringify({ name }) });
      const body = await parseJson(r);
      if (!r.ok) showToast?.(apiErrorMessage(body) || "Failed", { type: "error" });
      else {
        showToast?.("Workspace created", { type: "success" });
        setName("");
        await onRefresh();
      }
    } finally {
      setBusy(false);
    }
  };

  const startEdit = (t) => {
    setEditing(t.id);
    setEditName(t.name || "");
    setEditSlug(t.slug || "");
  };

  const saveEdit = async () => {
    if (!editing) return;
    const r = await adminFetch(`/v1/admin/tenants/${encodeURIComponent(editing)}`, {
      method: "PATCH",
      body: JSON.stringify({ name: editName.trim(), slug: editSlug.trim() || null }),
    });
    const body = await parseJson(r);
    if (!r.ok) showToast?.(apiErrorMessage(body) || "Failed", { type: "error" });
    else {
      showToast?.("Workspace updated", { type: "success" });
      setEditing(null);
      await onRefresh();
    }
  };

  const remove = async (id) => {
    if (!window.confirm(`Delete workspace ${id}? This may fail if data exists.`)) return;
    const r = await adminFetch(`/v1/admin/tenants/${encodeURIComponent(id)}`, { method: "DELETE" });
    const body = await parseJson(r);
    if (!r.ok) showToast?.(apiErrorMessage(body) || "Failed", { type: "error" });
    else {
      showToast?.("Deleted", { type: "success" });
      await onRefresh();
    }
  };

  return (
    <>
      <div className="panel" style={{ padding: 12, marginBottom: 16 }}>
        <strong>Create workspace</strong>
        <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
          <input placeholder="name" value={name} onChange={(e) => setName(e.target.value)} />
          <button type="button" disabled={busy} onClick={() => void create()}>
            Create
          </button>
        </div>
      </div>
      <p className="subtle">Total: {data.total_count}</p>
      <table className="usage-table" style={{ fontSize: "0.82rem", width: "100%" }}>
        <thead>
          <tr>
            <th>id</th>
            <th>name</th>
            <th>slug</th>
            <th>created</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {data.tenants.map((t) => (
            <tr key={t.id}>
              {editing === t.id ? (
                <>
                  <td>
                    <TruncId
                      id={t.id}
                      copyable
                      onCopy={() => showToast?.("Workspace id copied", { type: "success" })}
                    />
                  </td>
                  <td>
                    <input value={editName} onChange={(e) => setEditName(e.target.value)} />
                  </td>
                  <td>
                    <input value={editSlug} onChange={(e) => setEditSlug(e.target.value)} placeholder="slug" />
                  </td>
                  <td className="mono">{t.created_at ?? "—"}</td>
                  <td>
                    <button type="button" onClick={() => void saveEdit()}>
                      Save
                    </button>
                    <button type="button" className="secondary" onClick={() => setEditing(null)}>
                      Cancel
                    </button>
                  </td>
                </>
              ) : (
                <>
                  <td>
                    <TruncId
                      id={t.id}
                      copyable
                      onCopy={() => showToast?.("Workspace id copied", { type: "success" })}
                    />
                  </td>
                  <td>{t.name}</td>
                  <td>{t.slug ?? "—"}</td>
                  <td className="mono">{t.created_at ?? "—"}</td>
                  <td>
                    <button type="button" className="secondary" style={{ marginRight: 6 }} onClick={() => startEdit(t)}>
                      Edit
                    </button>
                    <button type="button" className="secondary" onClick={() => void remove(t.id)}>
                      Delete
                    </button>
                  </td>
                </>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function AdminMembershipsTable({ data, onRefresh, showToast }) {
  const [userFullName, setUserFullName] = useState("");
  const [tenantName, setTenantName] = useState("");
  const [userId, setUserId] = useState("");
  const [tenantId, setTenantId] = useState("");
  const [role, setRole] = useState("member");

  const add = async () => {
    const bodyPayload = { role };
    const nf = userFullName.trim();
    const nt = tenantName.trim();
    if ((nf && !nt) || (!nf && nt)) {
      showToast?.("Enter both user full name and workspace name, or use ids below", { type: "error" });
      return;
    }
    const byName = nf && nt;
    if (byName) {
      bodyPayload.user_full_name = nf;
      bodyPayload.tenant_name = nt;
    } else {
      const uid = userId.trim();
      const tid = tenantId.trim();
      if (!uid || !tid) {
        showToast?.("Enter full name + workspace name, or both user id and workspace id", { type: "error" });
        return;
      }
      const n = Number(uid);
      if (!Number.isInteger(n)) {
        showToast?.("User id must be an integer", { type: "error" });
        return;
      }
      bodyPayload.user_id = n;
      bodyPayload.tenant_id = tid;
    }
    const r = await adminFetch("/v1/admin/memberships", {
      method: "POST",
      body: JSON.stringify(bodyPayload),
    });
    const body = await parseJson(r);
    if (!r.ok) showToast?.(apiErrorMessage(body) || "Failed", { type: "error" });
    else {
      showToast?.("Membership added", { type: "success" });
      setUserFullName("");
      setTenantName("");
      setUserId("");
      setTenantId("");
      await onRefresh();
    }
  };

  const patchRole = async (id, newRole) => {
    const r = await adminFetch(`/v1/admin/memberships/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ role: newRole }),
    });
    const body = await parseJson(r);
    if (!r.ok) showToast?.(apiErrorMessage(body) || "Failed", { type: "error" });
    else {
      showToast?.("Role updated", { type: "success" });
      await onRefresh();
    }
  };

  const del = async (id) => {
    if (!window.confirm("Remove membership?")) return;
    const r = await adminFetch(`/v1/admin/memberships/${id}`, { method: "DELETE" });
    if (r.ok) {
      showToast?.("Removed", { type: "success" });
      await onRefresh();
    }
  };

  return (
    <>
      <div className="panel" style={{ padding: 12, marginBottom: 16 }}>
        <strong>Add membership</strong>
        <p className="subtle" style={{ margin: "6px 0 4px" }}>
          By display name (exact match on full name and workspace name; use ids if ambiguous)
        </p>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 4, alignItems: "center" }}>
          <input
            placeholder="User full name"
            value={userFullName}
            onChange={(e) => setUserFullName(e.target.value)}
            style={{ minWidth: 180 }}
          />
          <input
            placeholder="Workspace name"
            value={tenantName}
            onChange={(e) => setTenantName(e.target.value)}
            style={{ minWidth: 180 }}
          />
          <select value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="owner">owner</option>
            <option value="admin">admin</option>
            <option value="member">member</option>
          </select>
          <button type="button" onClick={() => void add()}>
            Add
          </button>
        </div>
        <p className="subtle" style={{ margin: "10px 0 4px" }}>
          Or by id
        </p>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
          <input placeholder="User id (integer)" value={userId} onChange={(e) => setUserId(e.target.value)} className="mono" style={{ minWidth: 120 }} />
          <input placeholder="Workspace id" value={tenantId} onChange={(e) => setTenantId(e.target.value)} className="mono" style={{ minWidth: 220 }} />
        </div>
      </div>
      <p className="subtle">Total: {data.total_count}</p>
      <table className="usage-table" style={{ fontSize: "0.82rem", width: "100%" }}>
        <thead>
          <tr>
            <th>User</th>
            <th>User id</th>
            <th>Workspace</th>
            <th>Workspace id</th>
            <th>role</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {data.memberships.map((m) => (
            <tr key={m.id}>
              <td>{m.user_full_name || m.user_email || "—"}</td>
              <td>
                <TruncId id={m.user_id} />
              </td>
              <td>{m.tenant_name ?? "—"}</td>
              <td>
                <TruncId id={m.tenant_id} />
              </td>
              <td>
                <select
                  value={m.role}
                  onChange={(e) => {
                    const v = e.target.value;
                    if (v !== m.role) void patchRole(m.id, v);
                  }}
                >
                  <option value="owner">owner</option>
                  <option value="admin">admin</option>
                  <option value="member">member</option>
                </select>
              </td>
              <td>
                <button type="button" className="secondary" onClick={() => void del(m.id)}>
                  Remove
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function AdminPlansTable({ data, onRefresh, showToast, entitlementDefs }) {
  const [slug, setSlug] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [entitlements, setEntitlements] = useState(() => ({}));
  const plans = Array.isArray(data?.plans) ? data.plans : [];

  const create = async () => {
    const r = await adminFetch("/v1/admin/subscription-plans", {
      method: "POST",
      body: JSON.stringify({
        slug: slug.trim(),
        display_name: displayName.trim(),
        entitlements_json: entitlements && typeof entitlements === "object" ? entitlements : {},
      }),
    });
    const body = await parseJson(r);
    if (!r.ok) showToast?.(apiErrorMessage(body) || "Failed", { type: "error" });
    else {
      showToast?.("Plan created", { type: "success" });
      await onRefresh();
    }
  };

  return (
    <>
      <div className="panel" style={{ padding: 12, marginBottom: 16 }}>
        <strong>Create plan</strong>
        <div style={{ marginTop: 8 }}>
          <input placeholder="slug" value={slug} onChange={(e) => setSlug(e.target.value)} style={{ marginRight: 8 }} />
          <input
            placeholder="display name"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            style={{ minWidth: 200 }}
          />
        </div>
        <p className="subtle" style={{ marginBottom: 6 }}>
          Entitlements (saved as JSON on the plan; keys below match what the API enforces)
        </p>
        <EntitlementEditor definitions={entitlementDefs} value={entitlements} onChange={setEntitlements} />
        <button type="button" style={{ marginTop: 8 }} onClick={() => void create()}>
          Create plan
        </button>
      </div>
      {plans.length === 0 ? (
        <p className="subtle" style={{ marginTop: 8 }}>
          No subscription plans in the database yet. Create one above, or seed plans via migrations / SQL.
        </p>
      ) : (
        plans.map((p) => (
          <PlanEditCard key={p.id} plan={p} onRefresh={onRefresh} showToast={showToast} entitlementDefs={entitlementDefs} />
        ))
      )}
    </>
  );
}

function PlanEditCard({ plan, onRefresh, showToast, entitlementDefs }) {
  const biRaw = String(plan.billing_interval || "month").toLowerCase();
  const billingIntervalSafe = biRaw === "year" ? "year" : "month";
  const [displayName, setDisplayName] = useState(plan.display_name ?? "");
  const [description, setDescription] = useState(plan.description || "");
  const [stripePrice, setStripePrice] = useState(plan.stripe_price_id || "");
  const [stripeProduct, setStripeProduct] = useState(plan.stripe_product_id || "");
  const [billingInterval, setBillingInterval] = useState(billingIntervalSafe);
  const [isActive, setIsActive] = useState(plan.is_active !== false);
  const [sortOrder, setSortOrder] = useState(String(plan.sort_order ?? 0));
  const [entitlements, setEntitlements] = useState(() =>
    plan.entitlements_json && typeof plan.entitlements_json === "object" ? plan.entitlements_json : {},
  );

  useEffect(() => {
    const bi = String(plan.billing_interval || "month").toLowerCase();
    setDisplayName(plan.display_name ?? "");
    setDescription(plan.description || "");
    setStripePrice(plan.stripe_price_id || "");
    setStripeProduct(plan.stripe_product_id || "");
    setBillingInterval(bi === "year" ? "year" : "month");
    setIsActive(plan.is_active !== false);
    setSortOrder(String(plan.sort_order ?? 0));
    setEntitlements(plan.entitlements_json && typeof plan.entitlements_json === "object" ? plan.entitlements_json : {});
  }, [plan.id]);

  const save = async () => {
    const r = await adminFetch(`/v1/admin/subscription-plans/${plan.id}`, {
      method: "PATCH",
      body: JSON.stringify({
        display_name: displayName.trim(),
        description: description.trim() || null,
        stripe_price_id: stripePrice.trim() || null,
        stripe_product_id: stripeProduct.trim() || null,
        billing_interval: billingInterval,
        is_active: isActive,
        sort_order: parseInt(sortOrder, 10) || 0,
        entitlements_json: entitlements,
      }),
    });
    const body = await parseJson(r);
    if (!r.ok) showToast?.(apiErrorMessage(body) || "Failed", { type: "error" });
    else {
      showToast?.("Plan saved", { type: "success" });
      await onRefresh();
    }
  };

  const remove = async () => {
    if (!window.confirm(`Delete plan ${plan.slug}?`)) return;
    const r = await adminFetch(`/v1/admin/subscription-plans/${plan.id}`, { method: "DELETE" });
    const body = await parseJson(r);
    if (!r.ok) showToast?.(apiErrorMessage(body) || "Failed", { type: "error" });
    else {
      showToast?.("Plan deleted", { type: "success" });
      await onRefresh();
    }
  };

  return (
    <details className="panel" style={{ padding: 12, marginBottom: 8 }}>
      <summary>
        <strong>{plan.display_name}</strong> <span className="subtle mono">{plan.slug}</span>{" "}
        <span className="subtle">{plan.is_active ? "active" : "inactive"}</span>
      </summary>
      <div style={{ marginTop: 12, display: "grid", gap: 8, maxWidth: 560 }}>
        <label className="subtle">
          Display name <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} style={{ width: "100%" }} />
        </label>
        <label className="subtle">
          Description <input value={description} onChange={(e) => setDescription(e.target.value)} style={{ width: "100%" }} />
        </label>
        <label className="subtle">
          Stripe price id <input value={stripePrice} onChange={(e) => setStripePrice(e.target.value)} className="mono" style={{ width: "100%" }} />
        </label>
        <label className="subtle">
          Stripe product id <input value={stripeProduct} onChange={(e) => setStripeProduct(e.target.value)} className="mono" style={{ width: "100%" }} />
        </label>
        <label className="subtle">
          Billing interval{" "}
          <select value={billingInterval} onChange={(e) => setBillingInterval(e.target.value)}>
            <option value="month">month</option>
            <option value="year">year</option>
          </select>
        </label>
        <label className="subtle">
          <input type="checkbox" checked={isActive} onChange={(e) => setIsActive(e.target.checked)} /> Active
        </label>
        <label className="subtle">
          Sort order <input value={sortOrder} onChange={(e) => setSortOrder(e.target.value)} style={{ width: 80 }} />
        </label>
        <p className="subtle" style={{ margin: "8px 0 4px" }}>
          Entitlements (stored as JSON)
        </p>
        <EntitlementEditor definitions={entitlementDefs} value={entitlements} onChange={setEntitlements} />
        <div className="action-row" style={{ gap: 8 }}>
          <button type="button" onClick={() => void save()}>
            Save plan
          </button>
          <button type="button" className="secondary" onClick={() => void remove()}>
            Delete plan
          </button>
        </div>
      </div>
    </details>
  );
}

function AdminBillingTable({ data, onRefresh, showToast, entitlementDefs }) {
  const items = Array.isArray(data?.items) ? data.items : [];
  const [lookupQ, setLookupQ] = useState("");
  const [loadBusy, setLoadBusy] = useState(false);
  const [searchHits, setSearchHits] = useState([]);
  const [plans, setPlans] = useState([]);
  const [detailTid, setDetailTid] = useState(null);
  const [detail, setDetail] = useState(null);
  const [billingRowPresent, setBillingRowPresent] = useState(false);
  const [detailBusy, setDetailBusy] = useState(false);
  const [saveBusy, setSaveBusy] = useState(false);
  const [stripeCustomerId, setStripeCustomerId] = useState("");
  const [stripeSubId, setStripeSubId] = useState("");
  const [planId, setPlanId] = useState("");
  const [status, setStatus] = useState("none");
  const [periodEndLocal, setPeriodEndLocal] = useState("");
  const [overrideEnt, setOverrideEnt] = useState({});

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await adminFetch("/v1/admin/subscription-plans");
        const body = await parseJson(r);
        if (!cancelled && r.ok && Array.isArray(body.data?.plans)) setPlans(body.data.plans);
      } catch {
        /* ignore */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const applyDetailFromApi = (row, meta) => {
    setDetail(row);
    setBillingRowPresent(Boolean(meta?.billing_row_present));
    setStripeCustomerId(row?.stripe_customer_id || "");
    setStripeSubId(row?.stripe_subscription_id || "");
    setPlanId(row?.plan_id || "");
    setStatus(String(row?.status || "none").trim() || "none");
    setPeriodEndLocal(billingIsoToDatetimeLocal(row?.current_period_end));
    const o = row?.entitlements_override_json;
    setOverrideEnt(o && typeof o === "object" ? o : {});
  };

  const closeDetail = () => {
    setDetailTid(null);
    setDetail(null);
    setBillingRowPresent(false);
  };

  const fetchBillingForTenant = async (tid) => {
    setDetailBusy(true);
    setDetailTid(tid);
    try {
      const r = await adminFetch(`/v1/admin/tenant-billing/${encodeURIComponent(tid)}`);
      const body = await parseJson(r);
      if (!r.ok) {
        showToast?.(apiErrorMessage(body) || "Failed", { type: "error" });
        closeDetail();
        return false;
      }
      applyDetailFromApi(body.data, body.meta);
      if (body.meta?.billing_row_present === false) {
        showToast?.(
          "No billing row yet — set plan, status, and optional overrides below, then Save to create the subscription.",
          { type: "info", durationMs: 8000 },
        );
      } else {
        showToast?.("Loaded workspace billing", { type: "success" });
      }
      return true;
    } catch (e) {
      showToast?.(formatUserFacingError(e), { type: "error" });
      closeDetail();
      return false;
    } finally {
      setDetailBusy(false);
    }
  };

  const loadOne = async () => {
    const q = lookupQ.trim();
    if (!q) return;
    setLoadBusy(true);
    setSearchHits([]);
    try {
      const sr = await adminFetch(`/v1/admin/tenant-billing/search?q=${encodeURIComponent(q)}`);
      const sb = await parseJson(sr);
      if (sr.ok && Array.isArray(sb.data?.items)) {
        const hits = sb.data.items;
        if (hits.length === 1) {
          await fetchBillingForTenant(hits[0].tenant_id);
          return;
        }
        if (hits.length > 1) {
          setSearchHits(hits);
          showToast?.("Multiple workspaces matched — pick one below.", { type: "info", durationMs: 7000 });
          return;
        }
      }
      await fetchBillingForTenant(q);
    } catch (e) {
      showToast?.(formatUserFacingError(e), { type: "error" });
    } finally {
      setLoadBusy(false);
    }
  };

  const saveBilling = async () => {
    if (!detailTid) return;
    setSaveBusy(true);
    try {
      const payload = {
        stripe_customer_id: stripeCustomerId.trim() === "" ? null : stripeCustomerId.trim(),
        stripe_subscription_id: stripeSubId.trim() === "" ? null : stripeSubId.trim(),
        plan_id: planId.trim() === "" ? null : planId.trim(),
        status: status.trim() === "" ? null : status.trim(),
        entitlements_override_json: overrideEnt,
      };
      const iso = billingDatetimeLocalToIso(periodEndLocal);
      if (iso) payload.current_period_end = iso;

      const r = await adminFetch(`/v1/admin/tenant-billing/${encodeURIComponent(detailTid)}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
      const body = await parseJson(r);
      if (!r.ok) {
        showToast?.(apiErrorMessage(body) || "Failed", { type: "error" });
        return;
      }
      showToast?.("Billing saved", { type: "success" });
      applyDetailFromApi(body.data, { billing_row_present: true });
      await onRefresh();
    } catch (e) {
      showToast?.(formatUserFacingError(e), { type: "error" });
    } finally {
      setSaveBusy(false);
    }
  };

  const primaryContactLabel = (row) => {
    const fn = row?.owner_full_name && String(row.owner_full_name).trim();
    if (fn) return fn;
    if (row?.owner_email) return row.owner_email;
    return "—";
  };

  return (
    <>
      <div className="panel" style={{ padding: 12, marginBottom: 16 }}>
        <strong>Workspace billing</strong>
        <p className="subtle">
          <strong>Load</strong> by <strong>user email</strong>, <strong>user full name</strong>, <strong>workspace name</strong>,{" "}
          <strong>slug</strong>, or exact <strong>workspace id</strong>. If several workspaces match, pick one in the results.
          <strong> Save</strong> creates a billing row when missing (choose a plan and status first), or updates the existing row.
        </p>
        <input
          placeholder="email, user name, workspace name, slug, or workspace id"
          value={lookupQ}
          onChange={(e) => setLookupQ(e.target.value)}
          style={{ width: "100%", marginBottom: 8 }}
        />
        <div className="action-row" style={{ gap: 8, marginBottom: 8 }}>
          <button type="button" className="secondary" disabled={loadBusy} onClick={() => void loadOne()}>
            {loadBusy ? "Loading…" : "Load workspace"}
          </button>
        </div>
        {searchHits.length > 0 ? (
          <div style={{ marginBottom: 12, overflowX: "auto" }}>
            <p className="subtle" style={{ marginBottom: 6 }}>
              Search results ({searchHits.length})
            </p>
            <table className="usage-table" style={{ fontSize: "0.82rem", width: "100%" }}>
              <thead>
                <tr>
                  <th>Workspace</th>
                  <th>User</th>
                  <th>Match</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {searchHits.map((row, idx) => (
                  <tr key={`${row.tenant_id}-${row.user_id ?? "t"}-${idx}`}>
                    <td>
                      <span>{row.tenant_name || "—"}</span>
                      <div className="subtle mono" style={{ fontSize: "0.75rem" }}>
                        {row.tenant_id}
                      </div>
                    </td>
                    <td>
                      {row.user_email ? (
                        <>
                          {row.user_email}
                          {row.user_full_name ? (
                            <div className="subtle" style={{ fontSize: "0.75rem" }}>
                              {row.user_full_name}
                            </div>
                          ) : null}
                          {row.role ? (
                            <div className="subtle" style={{ fontSize: "0.75rem" }}>
                              role: {row.role}
                            </div>
                          ) : null}
                        </>
                      ) : (
                        <span className="subtle">—</span>
                      )}
                    </td>
                    <td className="subtle">{row.match_type ?? "—"}</td>
                    <td>
                      <button
                        type="button"
                        className="secondary"
                        disabled={loadBusy}
                        onClick={() => {
                          void (async () => {
                            setLoadBusy(true);
                            setSearchHits([]);
                            try {
                              await fetchBillingForTenant(row.tenant_id);
                            } finally {
                              setLoadBusy(false);
                            }
                          })();
                        }}
                      >
                        Open
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </div>

      {detailTid ? (
        <div className="panel" style={{ padding: 12, marginBottom: 16 }}>
          <div style={{ display: "flex", flexWrap: "wrap", alignItems: "baseline", justifyContent: "space-between", gap: 8 }}>
            <strong>
              {detail?.tenant_name?.trim() || "Workspace"}{" "}
              <span className="subtle" style={{ fontWeight: 400 }}>
                (
                <TruncId id={detailTid} />)
              </span>
            </strong>
            <button type="button" className="secondary" onClick={closeDetail}>
              Close
            </button>
          </div>
          {!billingRowPresent ? (
            <p className="subtle" style={{ marginTop: 8 }}>
              No billing record yet for this workspace. Set <strong>Plan</strong> and <strong>Status</strong> (e.g. active), then <strong>Save billing</strong> to create it.
            </p>
          ) : (
            <p className="subtle mono" style={{ marginTop: 6 }}>
              Updated {detail?.updated_at ?? "—"}
            </p>
          )}
          {detailBusy ? <p className="subtle">Loading…</p> : null}
          {!detailBusy && detail ? (
            <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 10, maxWidth: 560 }}>
              <p className="subtle" style={{ margin: 0, lineHeight: 1.5 }}>
                <strong>Primary contact:</strong> {primaryContactLabel(detail)}
                {detail?.owner_email && detail?.owner_full_name?.trim() ? (
                  <span className="subtle"> — {detail.owner_email}</span>
                ) : null}
              </p>
              <label className="subtle">
                Stripe customer id{" "}
                <input value={stripeCustomerId} onChange={(e) => setStripeCustomerId(e.target.value)} className="mono" style={{ width: "100%" }} />
              </label>
              <label className="subtle">
                Stripe subscription id{" "}
                <input value={stripeSubId} onChange={(e) => setStripeSubId(e.target.value)} className="mono" style={{ width: "100%" }} />
              </label>
              <label className="subtle">
                Plan{" "}
                <select value={planId} onChange={(e) => setPlanId(e.target.value)} style={{ width: "100%" }}>
                  <option value="">— none —</option>
                  {plans.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.display_name || p.slug} ({p.slug})
                    </option>
                  ))}
                </select>
              </label>
              {detail?.plan_display_name && planId ? (
                <p className="subtle" style={{ margin: "-4px 0 0" }}>
                  Current plan label: {detail.plan_display_name}
                </p>
              ) : null}
              <label className="subtle">
                Status{" "}
                <select value={status} onChange={(e) => setStatus(e.target.value)} style={{ width: "100%" }}>
                  <option value="none">none</option>
                  <option value="active">active</option>
                  <option value="trialing">trialing</option>
                  <option value="canceled">canceled</option>
                  <option value="past_due">past_due</option>
                  <option value="unpaid">unpaid</option>
                  <option value="incomplete">incomplete</option>
                </select>
              </label>
              <label className="subtle">
                Current period end (local){" "}
                <input
                  type="datetime-local"
                  value={periodEndLocal}
                  onChange={(e) => setPeriodEndLocal(e.target.value)}
                  style={{ width: "100%" }}
                />
              </label>
              <p className="subtle" style={{ margin: 0 }}>
                Entitlement overrides (merged on top of the workspace plan)
              </p>
              <EntitlementEditor definitions={entitlementDefs} value={overrideEnt} onChange={setOverrideEnt} />
              <div className="action-row" style={{ gap: 8 }}>
                <button type="button" disabled={saveBusy} onClick={() => void saveBilling()}>
                  {saveBusy ? "Saving…" : "Save billing"}
                </button>
              </div>
            </div>
          ) : null}
        </div>
      ) : null}

      <p className="subtle">Total: {data.total_count ?? items.length}</p>
      {items.length === 0 ? (
        <p className="subtle" style={{ marginTop: 8 }}>
          No tenant billing rows yet. Use <strong>Load workspace</strong> above, or complete Stripe checkout from the Account page.
        </p>
      ) : null}
      <div style={{ overflowX: "auto" }}>
        <table className="usage-table" style={{ fontSize: "0.82rem", width: "100%" }}>
          <thead>
            <tr>
              <th>Primary contact</th>
              <th>Workspace</th>
              <th>Status</th>
              <th>Plan</th>
              <th>Period end</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {items.map((row) => (
              <tr key={row.tenant_id}>
                <td title={row.tenant_id}>
                  {primaryContactLabel(row)}
                  {row.owner_email && row.owner_full_name?.trim() ? (
                    <div className="subtle" style={{ fontSize: "0.75rem" }}>
                      {row.owner_email}
                    </div>
                  ) : null}
                </td>
                <td>
                  {row.tenant_name || "—"}
                  <div className="subtle mono" style={{ fontSize: "0.75rem" }}>
                    <TruncId id={row.tenant_id} />
                  </div>
                </td>
                <td>{row.status ?? "—"}</td>
                <td>{row.plan_display_name || row.plan_id || "—"}</td>
                <td className="mono">{row.current_period_end ? String(row.current_period_end).slice(0, 16) : "—"}</td>
                <td>
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => {
                      void (async () => {
                        setLoadBusy(true);
                        setSearchHits([]);
                        try {
                          await fetchBillingForTenant(row.tenant_id);
                        } finally {
                          setLoadBusy(false);
                        }
                      })();
                    }}
                  >
                    Detail
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function AdminPaymentsTable({ events, totalCount }) {
  const rows = Array.isArray(events) ? events : [];
  return (
    <>
      <p className="subtle">Total: {typeof totalCount === "number" ? totalCount : rows.length}</p>
      {rows.length === 0 ? (
        <p className="subtle" style={{ marginTop: 8 }}>
          No payment events recorded yet. Events appear when the Stripe webhook processes charges (requires{" "}
          <code>STRIPE_WEBHOOK_SECRET</code> and a reachable <code>/v1/billing/stripe/webhook</code>).
        </p>
      ) : (
        <AdminGenericTable
          rows={rows}
          columns={["created_at", "event_type", "tenant_name", "amount_cents", "stripe_event_id"]}
          getCellTitle={(row, c) =>
            c === "tenant_name" && row.tenant_id ? `Workspace id: ${row.tenant_id}` : undefined
          }
        />
      )}
    </>
  );
}

function AdminProjectsTable({ data, onRefresh, showToast }) {
  const [editing, setEditing] = useState(null);
  const [title, setTitle] = useState("");
  const [status, setStatus] = useState("");

  const startEdit = (p) => {
    setEditing(p.id);
    setTitle(p.title || "");
    setStatus(p.status || "");
  };

  const save = async () => {
    if (!editing) return;
    const r = await adminFetch(`/v1/admin/projects/${editing}`, {
      method: "PATCH",
      body: JSON.stringify({ title: title.trim() || undefined, status: status.trim() || undefined }),
    });
    const body = await parseJson(r);
    if (!r.ok) showToast?.(apiErrorMessage(body) || "Failed", { type: "error" });
    else {
      showToast?.("Project updated", { type: "success" });
      setEditing(null);
      await onRefresh();
    }
  };

  const del = async (id) => {
    if (
      !window.confirm(
        `Delete project ${id}? Generated assets and narrations on disk will be removed; files under exports/ for this project are kept. This cannot be undone.`,
      )
    )
      return;
    const r = await adminFetch(`/v1/admin/projects/${id}`, { method: "DELETE" });
    const body = await parseJson(r);
    if (!r.ok) showToast?.(apiErrorMessage(body) || "Failed", { type: "error" });
    else {
      showToast?.("Deleted", { type: "success" });
      await onRefresh();
    }
  };

  return (
    <>
      <p className="subtle">Total: {data.total_count}</p>
      <table className="usage-table" style={{ fontSize: "0.82rem", width: "100%" }}>
        <thead>
          <tr>
            <th>id</th>
            <th>tenant</th>
            <th>title</th>
            <th>phase</th>
            <th>status</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {data.projects.map((p) => (
            <tr key={p.id}>
              {editing === p.id ? (
                <>
                  <td className="mono">{p.id}</td>
                  <td title={p.tenant_id ? `Workspace id: ${p.tenant_id}` : undefined}>
                    {p.tenant_name != null && String(p.tenant_name).trim() !== "" ? p.tenant_name : "—"}
                  </td>
                  <td>
                    <input value={title} onChange={(e) => setTitle(e.target.value)} />
                  </td>
                  <td>{p.workflow_phase}</td>
                  <td>
                    <input value={status} onChange={(e) => setStatus(e.target.value)} placeholder="status" />
                  </td>
                  <td>
                    <button type="button" onClick={() => void save()}>
                      Save
                    </button>
                    <button type="button" className="secondary" onClick={() => setEditing(null)}>
                      Cancel
                    </button>
                  </td>
                </>
              ) : (
                <>
                  <td className="mono">{p.id}</td>
                  <td title={p.tenant_id ? `Workspace id: ${p.tenant_id}` : undefined}>
                    {p.tenant_name != null && String(p.tenant_name).trim() !== "" ? p.tenant_name : "—"}
                  </td>
                  <td>{p.title}</td>
                  <td>{p.workflow_phase}</td>
                  <td>{p.status ?? "—"}</td>
                  <td>
                    <button type="button" className="secondary" style={{ marginRight: 6 }} onClick={() => startEdit(p)}>
                      Edit
                    </button>
                    <button type="button" className="secondary" onClick={() => void del(p.id)}>
                      Delete
                    </button>
                  </td>
                </>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
