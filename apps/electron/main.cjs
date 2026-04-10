"use strict";

const path = require("path");
const fs = require("fs");
const { app, BrowserWindow, dialog } = require("electron");

const {
  getStudioRoot,
  getApiDir,
  getWebDistDir,
  getComposeFile,
  getVenvDir,
  getStorageDir,
  getLogsDir,
} = require("./lib/paths.cjs");
const { startUiServer } = require("./lib/ui-server.cjs");
const {
  dockerUp,
  dockerDown,
  resolvePythonInvoker,
  venvPythonPath,
  ensureVenv,
  runAlembicUpgrade,
  spawnService,
  waitForApiReady,
  killTree,
  appendLogSync,
} = require("./lib/backend.cjs");
const { loadStudioEnv } = require("./lib/dotenv-lite.cjs");

const API_PORT = parseInt(process.env.API_PORT || "8000", 10);

/** Load packaged UI + proxy only; you run API + Docker yourself. */
const SKIP_BACKEND = process.env.DIRECTOR_SKIP_BACKEND === "1";
/** Open this URL in the window (e.g. Vite dev server); skips local UI server. */
const DIRECTOR_UI_URL = (process.env.DIRECTOR_UI_URL || "").trim();

let mainWindow = null;
let uiHandle = null;
const childProcesses = [];

async function showFatal(title, message) {
  appendLogSync(getLogsDir(), "electron.log", `${title}: ${message}`);
  await dialog.showErrorBox(title, message);
  app.quit();
}

function buildBackendEnv({ storageDir, studioRoot }) {
  fs.mkdirSync(storageDir, { recursive: true });
  const base = loadStudioEnv(studioRoot);
  return {
    ...base,
    LOCAL_STORAGE_ROOT: storageDir,
    API_HOST: base.API_HOST || process.env.API_HOST || "127.0.0.1",
    API_PORT: String(API_PORT),
    API_RELOAD: "0",
    PYTHONUNBUFFERED: "1",
    DATABASE_URL:
      base.DATABASE_URL ||
      process.env.DATABASE_URL ||
      "postgresql+psycopg://director:director_dev_change_me@127.0.0.1:5433/director",
    REDIS_URL: base.REDIS_URL || process.env.REDIS_URL || "redis://127.0.0.1:6379/0",
  };
}

async function startFullStack() {
  const studioRoot = getStudioRoot();
  const apiDir = getApiDir(studioRoot);
  const distDir = getWebDistDir(studioRoot);
  const composeFile = getComposeFile(studioRoot);
  const logsDir = getLogsDir();
  const venvDir = getVenvDir();
  const storageDir = getStorageDir();

  appendLogSync(logsDir, "electron.log", `studioRoot=${studioRoot}`);

  const studioEnv = loadStudioEnv(studioRoot);

  if (!fs.existsSync(composeFile)) {
    await showFatal("Directely Studio", `docker-compose.yml not found:\n${composeFile}`);
    return;
  }
  if (!fs.existsSync(apiDir)) {
    await showFatal("Directely Studio", `API directory missing:\n${apiDir}`);
    return;
  }

  try {
    await dockerUp(studioRoot, composeFile, logsDir, studioEnv);
  } catch (e) {
    await showFatal(
      "Docker",
      `Could not start Docker Compose.\n\nInstall Docker Desktop and ensure \`docker compose\` works.\n\n${e.message || e}`,
    );
    return;
  }

  let invoker;
  try {
    invoker = await resolvePythonInvoker(studioEnv);
  } catch (e) {
    await showFatal("Python", e.message || String(e));
    return;
  }

  const ffmpegDir = path.join(studioRoot, "packages", "ffmpeg-pipelines");
  try {
    await ensureVenv({
      invoker,
      venvDir,
      studioRoot,
      apiDir,
      ffmpegDir,
      logsDir,
      env: studioEnv,
    });
  } catch (e) {
    await showFatal(
      "Backend setup",
      `Failed to create venv or install Python dependencies (first run may download packages).\n\n${e.message || e}`,
    );
    return;
  }

  const vpy = venvPythonPath(venvDir);
  const backendEnv = buildBackendEnv({ storageDir, studioRoot });

  try {
    await runAlembicUpgrade(vpy, apiDir, backendEnv, logsDir);
  } catch (e) {
    await showFatal(
      "Database migrations",
      `Alembic upgrade failed. Is Postgres up on port 5433?\n\n${e.message || e}`,
    );
    return;
  }

  const apiChild = spawnService({
    vpy,
    apiDir,
    env: backendEnv,
    logsDir,
    name: "api",
    args: ["-m", "director_api"],
  });
  childProcesses.push(apiChild);

  const workerChild = spawnService({
    vpy,
    apiDir,
    env: backendEnv,
    logsDir,
    name: "celery-worker",
    args: ["-m", "celery", "-A", "director_api.tasks.celery_app", "worker", "-l", "info"],
  });
  childProcesses.push(workerChild);

  const beatChild = spawnService({
    vpy,
    apiDir,
    env: backendEnv,
    logsDir,
    name: "celery-beat",
    args: ["-m", "celery", "-A", "director_api.tasks.celery_app", "beat", "-l", "info"],
  });
  childProcesses.push(beatChild);

  try {
    await waitForApiReady(API_PORT);
  } catch (e) {
    await showFatal(
      "API not ready",
      `The API did not respond on port ${API_PORT}.\nSee logs in:\n${logsDir}\n\n${e.message || e}`,
    );
    return;
  }

  try {
    uiHandle = await startUiServer({ distDir, apiPort: API_PORT });
  } catch (e) {
    await showFatal("UI server", e.message || String(e));
    return;
  }

  createMainWindow(`http://127.0.0.1:${uiHandle.port}/`);
}

async function startUiOnlyMode() {
  const studioRoot = getStudioRoot();
  const distDir = getWebDistDir(studioRoot);
  const logsDir = getLogsDir();
  try {
    uiHandle = await startUiServer({ distDir, apiPort: API_PORT });
  } catch (e) {
    await showFatal("UI server", e.message || String(e));
    return;
  }
  appendLogSync(
    logsDir,
    "electron.log",
    `DIRECTOR_SKIP_BACKEND=1 — proxying /v1 to http://127.0.0.1:${API_PORT}`,
  );
  createMainWindow(`http://127.0.0.1:${uiHandle.port}/`);
}

function createMainWindow(url) {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  mainWindow.loadURL(url);
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

async function shutdown() {
  const studioRoot = getStudioRoot();
  const composeFile = getComposeFile(studioRoot);
  const logsDir = getLogsDir();
  const studioEnv = loadStudioEnv(studioRoot);

  if (uiHandle) {
    try {
      await uiHandle.close();
    } catch {
      /* ignore */
    }
    uiHandle = null;
  }

  for (const ch of childProcesses.splice(0)) {
    if (ch?.pid) await killTree(ch.pid);
  }

  if (!SKIP_BACKEND && fs.existsSync(composeFile)) {
    await dockerDown(studioRoot, composeFile, logsDir, studioEnv);
  }
}

app.whenReady().then(async () => {
  try {
    if (DIRECTOR_UI_URL) {
      appendLogSync(getLogsDir(), "electron.log", `DIRECTOR_UI_URL=${DIRECTOR_UI_URL}`);
      createMainWindow(DIRECTOR_UI_URL);
      return;
    }
    if (SKIP_BACKEND) {
      await startUiOnlyMode();
    } else {
      await startFullStack();
    }
  } catch (e) {
    await showFatal("Directely Studio", e?.stack || e?.message || String(e));
  }
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", async (e) => {
  if (DIRECTOR_UI_URL) return;
  // Second before-quit from app.exit: allow default quit.
  if (app._directorShuttingDown) return;
  e.preventDefault();
  app._directorShuttingDown = true;
  try {
    await shutdown();
  } finally {
    app.exit(0);
  }
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0 && DIRECTOR_UI_URL) {
    createMainWindow(DIRECTOR_UI_URL);
  }
});
