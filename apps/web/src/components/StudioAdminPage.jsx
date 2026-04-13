import { useCallback, useEffect, useMemo, useState } from "react";
import { parseJson, apiErrorMessage, formatUserFacingError } from "../lib/apiHelpers.js";
import { apiPath } from "../lib/api.js";
import { adminFetch, getAdminKey, setAdminKey } from "../lib/adminApi.js";
import { getDirectorTenantId } from "../lib/directorAuthSession.js";

const TABS = [
  { id: "dashboard", label: "Dashboard" },
  { id: "tools", label: "Tools" },
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

function TruncId({ id, className }) {
  const full = id == null ? "" : String(id);
  const short = formatShortId(full);
  return (
    <span className={className || "mono"} title={full} style={{ cursor: full.length > 14 ? "help" : undefined }}>
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
          budgetLast,
          budgetErr,
          budgetRunHistory,
        }),
      );
    } catch {
      /* quota or private mode */
    }
  }, [unlocked, budgetTitle, budgetTopic, budgetRuntime, budgetMode, budgetLast, budgetErr, budgetRunHistory]);

  useEffect(() => {
    const id = budgetLast?.project?.id;
    if (id == null || String(id).trim() === "") return;
    const s = String(id).trim();
    setBudgetProjectId(s);
    persistLastRanBudgetProjectId(s);
  }, [budgetLast?.project?.id]);

  /** Logged-in Studio workspace (from App: auth/me + session tenant); falls back if prop not ready yet. */
  const resolvedBudgetWorkspaceId = useMemo(() => {
    const fromProp = (workspaceTenantId || "").trim();
    if (fromProp) return fromProp;
    return getDirectorTenantId().trim();
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
      };
      const tid = resolvedBudgetWorkspaceId.trim();
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
        setBudgetRunHistory((h) => {
          const next = [
            ...(h || []),
            {
              ts: new Date().toISOString(),
              agent_run_id: String(rid),
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
  }, [budgetTitle, budgetTopic, budgetRuntime, budgetMode, resolvedBudgetWorkspaceId, showToast]);

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
      };
      const tid = resolvedBudgetWorkspaceId.trim();
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
        setBudgetRunHistory((h) => {
          const next = [
            ...(h || []),
            {
              ts: new Date().toISOString(),
              agent_run_id: String(rid),
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
    resolvedBudgetWorkspaceId,
    showToast,
  ]);

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

          {tab !== "dashboard" && tab !== "plans" && tab !== "stripe" && tab !== "tools" ? (
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
                <code className="mono">full_video</code> pipeline. Requires a Celery worker.{" "}
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
                    <ul style={{ margin: 0, paddingLeft: 18, fontSize: "0.82rem" }}>
                      {budgetRunHistory
                        .slice()
                        .reverse()
                        .map((row, idx) => (
                          <li key={`${row.ts}-${row.agent_run_id}-${idx}`} style={{ marginBottom: 4 }}>
                            <span className="subtle">{row.ts ? String(row.ts).replace("T", " ").slice(0, 19) : ""}</span>
                            {row.agent_run_id ? (
                              <span className="mono" style={{ marginLeft: 8 }}>
                                {String(row.agent_run_id)}
                              </span>
                            ) : null}
                          </li>
                        ))}
                    </ul>
                  </div>
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
            <AdminGenericTable
              rows={list.agent_runs}
              columns={["id", "tenant_name", "project_id", "status", "created_at"]}
              getCellTitle={(row, c) =>
                c === "tenant_name" && row.tenant_id ? `Workspace id: ${row.tenant_id}` : undefined
              }
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
                    <TruncId id={t.id} />
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
                    <TruncId id={t.id} />
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
  const [tenantId, setTenantId] = useState("");
  const [patchJson, setPatchJson] = useState("{}");
  const [loadBusy, setLoadBusy] = useState(false);

  const patchObj = useMemo(() => {
    try {
      return JSON.parse(patchJson || "{}");
    } catch {
      return {};
    }
  }, [patchJson]);

  const setOverrideEnt = (o) => {
    try {
      const base = JSON.parse(patchJson || "{}");
      if (typeof base !== "object" || base === null) throw new Error("bad");
      base.entitlements_override_json = o;
      setPatchJson(safeJsonStringify(base));
    } catch {
      setPatchJson(safeJsonStringify({ entitlements_override_json: o }));
    }
  };

  const loadOne = async () => {
    const tid = tenantId.trim();
    if (!tid) return;
    setLoadBusy(true);
    try {
      const r = await adminFetch(`/v1/admin/tenant-billing/${encodeURIComponent(tid)}`);
      const body = await parseJson(r);
      if (!r.ok) {
        showToast?.(apiErrorMessage(body) || "Failed", { type: "error" });
        setPatchJson("{}");
        return;
      }
      setPatchJson(safeJsonStringify(body.data));
      if (body.meta?.billing_row_present === false) {
        showToast?.("No billing row yet — edit JSON or entitlements, then PATCH to create the subscription.", {
          type: "info",
          durationMs: 7000,
        });
      } else {
        showToast?.("Loaded billing row", { type: "success" });
      }
    } catch (e) {
      showToast?.(formatUserFacingError(e), { type: "error" });
    } finally {
      setLoadBusy(false);
    }
  };

  const save = async () => {
    let bodyObj = {};
    try {
      bodyObj = JSON.parse(patchJson || "{}");
    } catch {
      showToast?.("Invalid JSON", { type: "error" });
      return;
    }
    const r = await adminFetch(`/v1/admin/tenant-billing/${encodeURIComponent(tenantId.trim())}`, {
      method: "PATCH",
      body: JSON.stringify(bodyObj),
    });
    const body = await parseJson(r);
    if (!r.ok) showToast?.(apiErrorMessage(body) || "Failed", { type: "error" });
    else {
      showToast?.("Billing updated", { type: "success" });
      await onRefresh();
    }
  };

  return (
    <>
      <div className="panel" style={{ padding: 12, marginBottom: 16 }}>
        <strong>Tenant billing</strong>
        <p className="subtle">
          Load returns the billing row if it exists; if the workspace has no row yet, you get a blank template (same
          shape). PATCH creates the row when missing — set <code>plan_id</code>, <code>status</code> (e.g. active or
          trialing), and/or <code>entitlements_override_json</code> without Stripe.
        </p>
        <input
          placeholder="tenant id"
          value={tenantId}
          onChange={(e) => setTenantId(e.target.value)}
          className="mono"
          style={{ width: "100%", marginBottom: 8 }}
        />
        <div className="action-row" style={{ gap: 8, marginBottom: 8 }}>
          <button type="button" className="secondary" disabled={loadBusy} onClick={() => void loadOne()}>
            {loadBusy ? "Loading…" : "Load"}
          </button>
        </div>
        <p className="subtle" style={{ marginBottom: 6 }}>
          <strong>entitlements_override_json</strong> — same permission keys as plans; merged on top of the subscribed
          plan for this workspace.
        </p>
        <EntitlementEditor
          definitions={entitlementDefs}
          value={patchObj.entitlements_override_json && typeof patchObj.entitlements_override_json === "object" ? patchObj.entitlements_override_json : {}}
          onChange={setOverrideEnt}
        />
        <p className="subtle" style={{ margin: "12px 0 4px" }}>
          Full PATCH body (JSON). The fields above update <code>entitlements_override_json</code> inside this object.
        </p>
        <JsonTextArea value={patchJson} onChange={setPatchJson} rows={8} />
        <button type="button" style={{ marginTop: 8 }} onClick={() => void save()}>
          PATCH
        </button>
      </div>
      <p className="subtle">Total: {data.total_count ?? items.length}</p>
      {items.length === 0 ? (
        <p className="subtle" style={{ marginTop: 8 }}>
          No tenant billing rows in the list yet. Use Load + PATCH above for a workspace id (row is created on first PATCH
          if missing), or complete Stripe checkout from the Account page.
        </p>
      ) : null}
      <div style={{ overflowX: "auto" }}>
        <table className="usage-table" style={{ fontSize: "0.82rem", width: "100%" }}>
          <thead>
            <tr>
              <th>tenant_id</th>
              <th>status</th>
              <th>plan_id</th>
              <th>stripe_subscription_id</th>
              <th>current_period_end</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {items.map((row) => (
              <tr key={row.tenant_id}>
                <td className="mono">{row.tenant_id}</td>
                <td>{row.status ?? "—"}</td>
                <td className="mono">{row.plan_id ?? "—"}</td>
                <td className="mono">{row.stripe_subscription_id ?? "—"}</td>
                <td className="mono">{row.current_period_end ?? "—"}</td>
                <td>
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => {
                      setTenantId(row.tenant_id);
                      void (async () => {
                        setLoadBusy(true);
                        try {
                          const r = await adminFetch(`/v1/admin/tenant-billing/${encodeURIComponent(row.tenant_id)}`);
                          const body = await parseJson(r);
                          if (r.ok) setPatchJson(safeJsonStringify(body.data));
                        } finally {
                          setLoadBusy(false);
                        }
                      })();
                    }}
                  >
                    Load
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
