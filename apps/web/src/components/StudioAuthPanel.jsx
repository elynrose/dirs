import { useCallback, useEffect, useRef, useState } from "react";
import { apiPath } from "../lib/api.js";
import { parseJson } from "../lib/apiHelpers.js";
import {
  completeGoogleRedirectIdToken,
  describeFirebaseAuthError,
  initFirebaseWeb,
  startGoogleRedirectSignIn,
  viteFirebaseWebConfig,
} from "../lib/firebaseWebAuth.js";
import { setDirectorAuthSession } from "../lib/directorAuthSession.js";
import { StudioPricingPanel } from "./StudioPricingPanel.jsx";

/** Public pricing view — readable without signing in (shareable links). */
function shouldShowPricingFromUrl() {
  try {
    const u = new URL(window.location.href);
    if (u.searchParams.get("pricing") === "1" || u.searchParams.get("view") === "pricing") return true;
    const h = u.hash.replace(/^#/, "");
    if (h === "pricing" || h === "/pricing") return true;
    if (u.pathname === "/pricing" || u.pathname.endsWith("/pricing")) return true;
    return false;
  } catch {
    return false;
  }
}

function syncUrlForShell(shell) {
  try {
    const u = new URL(window.location.href);
    if (shell === "pricing") {
      u.searchParams.set("pricing", "1");
    } else {
      u.searchParams.delete("pricing");
      u.searchParams.delete("view");
      if (u.hash === "#pricing" || u.hash === "#/pricing") {
        u.hash = "";
      }
      if (u.pathname.endsWith("/pricing")) {
        const next = u.pathname.slice(0, -"/pricing".length);
        u.pathname = next === "" ? "/" : next;
      }
    }
    window.history.replaceState({}, "", `${u.pathname}${u.search}${u.hash}`);
  } catch {
    /* ignore */
  }
}

/**
 * Full-screen login / register when `DIRECTOR_AUTH_ENABLED=true`.
 */
export function StudioAuthPanel({ onLoggedIn, allowRegistration }) {
  const [shell, setShell] = useState(() => (shouldShowPricingFromUrl() ? "pricing" : "auth"));

  useEffect(() => {
    const onPopState = () => {
      setShell(shouldShowPricingFromUrl() ? "pricing" : "auth");
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);
  const [mode, setMode] = useState("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [tenantName, setTenantName] = useState("My workspace");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  /** Show Google when we have web config; init runs on first click (avoids silent init failures hiding the button). */
  const [firebaseOffered, setFirebaseOffered] = useState(false);
  const firebaseCfgRef = useRef(null);

  const postFirebaseIdToken = useCallback(
    async (idToken) => {
      const r = await fetch(apiPath("/v1/auth/firebase"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          id_token: idToken,
          tenant_name: tenantName.trim() || "My workspace",
        }),
      });
      const raw = await parseJson(r);
      if (!r.ok) {
        if (r.status === 404) {
          setErr(
            "Google sign-in is not enabled on the API. Set DIRECTOR_FIREBASE_CREDENTIALS_PATH to your Firebase service account JSON file and restart the API.",
          );
          return false;
        }
        const msg =
          raw?.error?.message ||
          raw?.detail?.message ||
          (typeof raw?.detail === "string" ? raw.detail : null) ||
          `HTTP ${r.status}`;
        setErr(String(msg));
        return false;
      }
      const d = raw.data;
      if (!d?.access_token || !d?.tenant_id) {
        setErr("Unexpected response from server.");
        return false;
      }
      setDirectorAuthSession({ accessToken: d.access_token, tenantId: d.tenant_id });
      onLoggedIn?.(d);
      return true;
    },
    [tenantName, onLoggedIn],
  );

  useEffect(() => {
    let cancelled = false;

    const tryCompleteGoogleRedirect = async (fb) => {
      if (!fb?.api_key || !fb?.project_id) return;
      try {
        initFirebaseWeb(fb);
      } catch {
        return;
      }
      let idToken = null;
      try {
        idToken = await completeGoogleRedirectIdToken();
      } catch (re) {
        if (!cancelled) setErr(describeFirebaseAuthError(re));
        return;
      }
      if (!idToken || cancelled) return;
      setBusy(true);
      try {
        await postFirebaseIdToken(idToken);
      } catch (x) {
        if (!cancelled) setErr(String(x?.message || x));
      } finally {
        if (!cancelled) setBusy(false);
      }
    };

    (async () => {
      let fb = null;
      try {
        const r = await fetch(apiPath("/v1/auth/config"));
        const raw = await parseJson(r);
        const fromApi = raw?.data?.firebase;
        fb = fromApi?.api_key && fromApi?.project_id ? fromApi : viteFirebaseWebConfig();
      } catch {
        fb = viteFirebaseWebConfig();
      }
      if (cancelled) return;
      firebaseCfgRef.current = fb;
      if (fb?.api_key && fb?.project_id) setFirebaseOffered(true);
      await tryCompleteGoogleRedirect(fb);
    })();

    return () => {
      cancelled = true;
    };
  }, [postFirebaseIdToken]);

  const submit = async (e) => {
    e.preventDefault();
    setErr("");
    setBusy(true);
    try {
      const path = mode === "login" ? "/v1/auth/login" : "/v1/auth/register";
      const body =
        mode === "login"
          ? { email, password }
          : { email, password, tenant_name: tenantName };
      const r = await fetch(apiPath(path), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const raw = await parseJson(r);
      if (!r.ok) {
        const msg =
          raw?.error?.message ||
          raw?.detail?.message ||
          (typeof raw?.detail === "string" ? raw.detail : null) ||
          `HTTP ${r.status}`;
        setErr(String(msg));
        return;
      }
      const d = raw.data;
      if (!d?.access_token || !d?.tenant_id) {
        setErr("Unexpected response from server.");
        return;
      }
      setDirectorAuthSession({ accessToken: d.access_token, tenantId: d.tenant_id });
      onLoggedIn?.(d);
    } catch (x) {
      setErr(String(x?.message || x));
    } finally {
      setBusy(false);
    }
  };

  const signInWithGoogle = async () => {
    setErr("");
    setBusy(true);
    try {
      const fb = firebaseCfgRef.current ?? viteFirebaseWebConfig();
      if (!fb?.api_key || !fb?.project_id) {
        setErr("Google sign-in is not configured (missing Firebase web config).");
        return;
      }
      try {
        initFirebaseWeb(fb);
      } catch (initErr) {
        setErr(String(initErr?.message || initErr));
        return;
      }
      await startGoogleRedirectSignIn();
    } catch (x) {
      const code = x?.code;
      const msg =
        code === "auth/popup-closed-by-user"
          ? "Sign-in was cancelled."
          : describeFirebaseAuthError(x);
      setErr(msg);
    } finally {
      setBusy(false);
    }
  };

  const goPricing = () => {
    setShell("pricing");
    syncUrlForShell("pricing");
  };
  const goAuth = () => {
    setShell("auth");
    syncUrlForShell("auth");
  };

  if (shell === "pricing") {
    return (
      <div className="studio-preauth-shell">
        <div className="studio-preauth-shell__card studio-preauth-glass studio-preauth-shell__card--wide">
          <StudioPricingPanel onBackToSignIn={goAuth} embedded />
        </div>
      </div>
    );
  }

  return (
    <div className="studio-preauth-shell">
      <div className="studio-preauth-shell__card studio-preauth-glass studio-preauth-shell__card--narrow">
      <h1 style={{ fontSize: "1.25rem", marginBottom: 8 }}>Directely Studio</h1>
      <p className="subtle" style={{ marginBottom: 12 }}>
        Sign in to your workspace. The API has multi-tenant authentication enabled.
      </p>
      <p style={{ marginBottom: 20 }}>
        <button type="button" className="secondary" onClick={goPricing}>
          Pricing & plans
        </button>
      </p>
      <div className="action-row" style={{ marginBottom: 16 }}>
        <button type="button" className={mode === "login" ? "" : "secondary"} onClick={() => setMode("login")}>
          Sign in
        </button>
        {allowRegistration ? (
          <button
            type="button"
            className={mode === "register" ? "" : "secondary"}
            onClick={() => setMode("register")}
          >
            Create workspace
          </button>
        ) : null}
      </div>
      {firebaseOffered ? (
        <>
          <button
            type="button"
            className="studio-auth-google"
            disabled={busy}
            onClick={() => void signInWithGoogle()}
          >
            <span className="studio-auth-google__icon" aria-hidden="true">
              <svg width="18" height="18" viewBox="0 0 48 48" focusable="false">
                <path
                  fill="#EA4335"
                  d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"
                />
                <path
                  fill="#4285F4"
                  d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"
                />
                <path
                  fill="#FBBC05"
                  d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"
                />
                <path
                  fill="#34A853"
                  d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"
                />
              </svg>
            </span>
            Continue with Google
          </button>
          <p className="studio-auth-or subtle" style={{ textAlign: "center", margin: "14px 0" }}>
            or email & password
          </p>
        </>
      ) : null}
      <form className="panel" style={{ padding: 16 }} onSubmit={submit}>
        <label style={{ display: "block", marginBottom: 12 }}>
          <span className="subtle" style={{ display: "block", marginBottom: 4 }}>
            Email
          </span>
          <input
            type="email"
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            style={{ width: "100%", boxSizing: "border-box" }}
            required
          />
        </label>
        <label style={{ display: "block", marginBottom: 12 }}>
          <span className="subtle" style={{ display: "block", marginBottom: 4 }}>
            Password
          </span>
          <input
            type="password"
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            style={{ width: "100%", boxSizing: "border-box" }}
            minLength={mode === "login" ? 1 : 8}
            required
          />
        </label>
        {mode === "register" ? (
          <label style={{ display: "block", marginBottom: 12 }}>
            <span className="subtle" style={{ display: "block", marginBottom: 4 }}>
              Workspace name
            </span>
            <input
              type="text"
              value={tenantName}
              onChange={(e) => setTenantName(e.target.value)}
              style={{ width: "100%", boxSizing: "border-box" }}
              required
            />
          </label>
        ) : null}
        {err ? <p className="err" style={{ marginBottom: 12 }}>{err}</p> : null}
        <button type="submit" disabled={busy}>
          {busy ? "Please wait…" : mode === "login" ? "Sign in" : "Create workspace"}
        </button>
      </form>
      </div>
    </div>
  );
}
