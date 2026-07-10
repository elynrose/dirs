import { useCallback, useEffect, useState } from "react";

function isDesktopApp() {
  return typeof window !== "undefined" && window.directorDesktop?.isDesktop;
}

/** Desktop-only settings (Docker path, app data folder). Shown in Electron builds only. */
export default function SettingsDesktopPanel() {
  const desktop = window.directorDesktop;
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [cfg, setCfg] = useState(null);
  const [dockerPath, setDockerPath] = useState("");

  const refresh = useCallback(async () => {
    if (!desktop?.getDockerConfig) return;
    setLoading(true);
    setErr("");
    try {
      const c = await desktop.getDockerConfig();
      setCfg(c);
      setDockerPath(c?.dockerExe || "");
    } catch (e) {
      setErr(String(e?.message || e));
    } finally {
      setLoading(false);
    }
  }, [desktop]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  if (!isDesktopApp()) {
    return (
      <p className="subtle">Desktop settings are only available in the Directely Windows / macOS app.</p>
    );
  }

  async function onBrowse() {
    setBusy(true);
    setErr("");
    try {
      const r = await desktop.browseDockerExe();
      if (r?.canceled) return;
      setDockerPath(r.dockerExe || "");
      await refresh();
      if (!r.works) {
        setErr(r.testDetail || "Docker path saved but `docker compose version` failed. Start Docker Desktop and test again.");
      }
    } catch (e) {
      setErr(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  }

  async function onTest() {
    setBusy(true);
    setErr("");
    try {
      const r = await desktop.testDocker(dockerPath.trim() || undefined);
      if (!r?.ok) {
        setErr(r?.detail || "Docker compose test failed.");
      } else {
        setErr("");
      }
      await refresh();
    } catch (e) {
      setErr(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  }

  async function onSavePath() {
    const p = dockerPath.trim();
    if (!p) {
      setErr("Enter or browse to a docker executable path.");
      return;
    }
    setBusy(true);
    setErr("");
    try {
      const r = await desktop.setDockerExe(p);
      if (!r?.ok) {
        setErr(r?.error || r?.testDetail || "Could not save Docker path.");
      }
      await refresh();
    } catch (e) {
      setErr(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  }

  async function onClearSaved() {
    setBusy(true);
    setErr("");
    try {
      const r = await desktop.clearDockerExe();
      setDockerPath(r?.dockerExe || "");
      await refresh();
      if (!r?.works) {
        setErr(r?.testDetail || "Cleared saved path; auto-detect did not pass compose test.");
      }
    } catch (e) {
      setErr(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="settings-desktop-panel">
      <h3 style={{ marginTop: 0 }}>Desktop runtime</h3>
      <p className="subtle">
        Directely uses Docker Desktop for PostgreSQL and Redis. If Docker is not installed, the app will ask you to
        install it on startup. If it is installed but not found on PATH, set the full path to <code>docker.exe</code>{" "}
        here. Restart the app after changing.
      </p>

      {loading ? <p className="subtle">Loading…</p> : null}
      {err ? <p className="err">{err}</p> : null}

      {cfg && cfg.installed === false ? (
        <p className="err" style={{ marginBottom: 12 }}>
          Docker Desktop does not appear to be installed.{" "}
          <a href={cfg.downloadUrl || "https://www.docker.com/products/docker-desktop/"} target="_blank" rel="noreferrer">
            Download Docker Desktop
          </a>
          , install it, start it once, then restart Directely.
        </p>
      ) : null}

      {cfg ? (
        <p className="subtle" style={{ marginBottom: 12 }}>
          Status:{" "}
          <strong style={{ color: cfg.works ? "var(--ok, #3d8b5a)" : "var(--err, #c44)" }}>
            {cfg.works ? "Docker Compose OK" : cfg.installed === false ? "Not installed" : "Docker Compose failed"}
          </strong>
          {cfg.source ? ` · resolved via ${cfg.source}` : null}
        </p>
      ) : null}

      <label htmlFor="cfg-docker-bin">Docker CLI path</label>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", marginBottom: 8 }}>
        <input
          id="cfg-docker-bin"
          type="text"
          style={{ flex: "1 1 320px", minWidth: 240 }}
          value={dockerPath}
          onChange={(e) => setDockerPath(e.target.value)}
          placeholder="C:\Program Files\Docker\Docker\resources\bin\docker.exe"
          disabled={busy}
        />
        <button type="button" className="secondary" disabled={busy} onClick={onBrowse}>
          Browse…
        </button>
        <button type="button" disabled={busy} onClick={onSavePath}>
          Save path
        </button>
        <button type="button" className="secondary" disabled={busy} onClick={onTest}>
          Test
        </button>
        <button type="button" className="secondary" disabled={busy} onClick={onClearSaved}>
          Use auto-detect
        </button>
      </div>

      {cfg?.typicalPaths?.length ? (
        <div style={{ marginTop: 12 }}>
          <p className="subtle" style={{ marginBottom: 6 }}>
            Detected on this machine:
          </p>
          <ul className="subtle" style={{ margin: 0, paddingLeft: 18, lineHeight: 1.5 }}>
            {cfg.typicalPaths.map((p) => (
              <li key={p}>
                <code>{p}</code>{" "}
                <button
                  type="button"
                  className="linkish"
                  style={{ fontSize: "0.9em" }}
                  disabled={busy}
                  onClick={() => setDockerPath(p)}
                >
                  use
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <p className="subtle" style={{ marginTop: 16 }}>
        Config files: <code>{cfg?.userEnvPath || "…"}</code> (<code>DOCKER_BIN</code>) and{" "}
        <code>{cfg?.userDataPath ? `${cfg.userDataPath}\\docker-cli.json` : "docker-cli.json"}</code>.
        {" "}
        <button
          type="button"
          className="linkish"
          disabled={busy}
          onClick={() => desktop.openUserDataFolder?.()}
        >
          Open app data folder
        </button>
      </p>
    </div>
  );
}

export { isDesktopApp };
