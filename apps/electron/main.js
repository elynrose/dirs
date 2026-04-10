/**
 * Directely desktop shell: Docker stack + Python venv (API, Celery worker+beat) + local UI server (/v1 proxy).
 */
import { app, BrowserWindow, dialog } from "electron";
import path from "node:path";
import fs from "node:fs";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import express from "express";
import { createProxyMiddleware } from "http-proxy-middleware";
import getPort from "get-port";
import treeKill from "tree-kill";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

/** Bump when bundled backend layout or install steps change (forces pip reinstall). */
const BACKEND_BOOTSTRAP_VERSION = "1";

const API_HOST = "127.0.0.1";
const API_PORT = 8000;

let mainWindow = null;
/** @type {import('node:child_process').ChildProcess[]} */
const children = [];
let uiServer = null;
let uiPort = null;
let isQuitting = false;

function getPaths() {
  if (app.isPackaged) {
    const r = process.resourcesPath;
    return {
      resourcesRoot: r,
      apiDir: path.join(r, "apps", "api"),
      webDist: path.join(r, "web-dist"),
      dockerComposeFile: path.join(r, "docker-compose.yml"),
      envExample: path.join(r, ".env.example"),
    };
  }
  const repoRoot = path.join(__dirname, "..", "..");
  return {
    resourcesRoot: repoRoot,
    apiDir: path.join(repoRoot, "apps", "api"),
    webDist: path.join(repoRoot, "apps", "web", "dist"),
    dockerComposeFile: path.join(repoRoot, "docker-compose.yml"),
    envExample: path.join(repoRoot, ".env.example"),
  };
}

function userEnvPath() {
  return path.join(app.getPath("userData"), ".env");
}

function userStorageRoot() {
  return path.join(app.getPath("userData"), "storage");
}

function venvRoot() {
  return path.join(app.getPath("userData"), "backend-venv");
}

function bootstrapMarkerPath() {
  return path.join(app.getPath("userData"), ".director-backend-bootstrap");
}

function readBootstrapVersion() {
  try {
    return fs.readFileSync(bootstrapMarkerPath(), "utf8").trim();
  } catch {
    return "";
  }
}

function writeBootstrapVersion() {
  fs.mkdirSync(app.getPath("userData"), { recursive: true });
  fs.writeFileSync(bootstrapMarkerPath(), BACKEND_BOOTSTRAP_VERSION, "utf8");
}

function parseDotEnv(text) {
  const out = {};
  for (const line of text.split("\n")) {
    const t = line.trim();
    if (!t || t.startsWith("#")) continue;
    const i = t.indexOf("=");
    if (i <= 0) continue;
    const key = t.slice(0, i).trim();
    let val = t.slice(i + 1).trim();
    if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
      val = val.slice(1, -1);
    }
    out[key] = val;
  }
  return out;
}

function loadMergedEnv() {
  const base = { ...process.env };
  if (fs.existsSync(userEnvPath())) {
    const parsed = parseDotEnv(fs.readFileSync(userEnvPath(), "utf8"));
    Object.assign(base, parsed);
  }
  const storage = userStorageRoot();
  fs.mkdirSync(storage, { recursive: true });
  base.LOCAL_STORAGE_ROOT = base.LOCAL_STORAGE_ROOT || storage;
  if (!base.DATABASE_URL) {
    base.DATABASE_URL = "postgresql+psycopg://director:director_dev_change_me@localhost:5433/director";
  }
  if (!base.REDIS_URL) {
    base.REDIS_URL = "redis://localhost:6379/0";
  }
  base.API_HOST = API_HOST;
  base.API_PORT = String(API_PORT);
  base.API_RELOAD = "0";
  return base;
}

function ensureUserEnvFile(paths) {
  fs.mkdirSync(app.getPath("userData"), { recursive: true });
  if (!fs.existsSync(userEnvPath()) && fs.existsSync(paths.envExample)) {
    fs.copyFileSync(paths.envExample, userEnvPath());
  }
}

function run(cmd, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, {
      stdio: "inherit",
      ...options,
    });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`${cmd} ${args.join(" ")} exited ${code}`));
    });
  });
}

/** `parts` = executable + optional launcher args, e.g. `['py', '-3.12']` or `['python3.12']`. */
function runPythonVersionCheck(parts) {
  const [cmd, ...pre] = parts;
  return new Promise((resolve) => {
    const child = spawn(cmd, [...pre, "-c", "import sys; sys.exit(0 if sys.version_info[:2]>=(3,11) else 1)"], {
      stdio: "ignore",
    });
    child.on("close", (code) => resolve(code === 0));
    child.on("error", () => resolve(false));
  });
}

/**
 * Directely API requires Python >= 3.11 (see apps/api/pyproject.toml).
 * macOS `/usr/bin/python3` is often 3.9 — prefer `python3.11` / `python3.12` from Homebrew or pyenv.
 */
async function resolveHostPython() {
  /** @type {string[][]} */
  const candidates =
    process.platform === "win32"
      ? [
          ["py", "-3.13"],
          ["py", "-3.12"],
          ["py", "-3.11"],
          ["python"],
          ["python3"],
        ]
      : [
          ["python3.13"],
          ["python3.12"],
          ["python3.11"],
          ["python3"],
        ];
  for (const parts of candidates) {
    if (await runPythonVersionCheck(parts)) return parts;
  }
  throw new Error(
    "Python 3.11+ not found on PATH. Install e.g. `brew install python@3.12` and ensure `python3.12` is on PATH, then restart Directely.",
  );
}

function venvPythonVersionOk() {
  const vpy = venvPython();
  if (!fs.existsSync(vpy)) return Promise.resolve(false);
  return new Promise((resolve) => {
    const child = spawn(vpy, ["-c", "import sys; sys.exit(0 if sys.version_info[:2]>=(3,11) else 1)"], {
      stdio: "ignore",
    });
    child.on("close", (code) => resolve(code === 0));
    child.on("error", () => resolve(false));
  });
}

function venvPython() {
  if (process.platform === "win32") {
    return path.join(venvRoot(), "Scripts", "python.exe");
  }
  return path.join(venvRoot(), "bin", "python");
}

function venvBin(name) {
  if (process.platform === "win32") {
    return path.join(venvRoot(), "Scripts", `${name}.exe`);
  }
  return path.join(venvRoot(), "bin", name);
}

async function ensurePythonVenv(paths) {
  if (fs.existsSync(venvRoot()) && !(await venvPythonVersionOk())) {
    fs.rmSync(venvRoot(), { recursive: true, force: true });
    try {
      fs.unlinkSync(bootstrapMarkerPath());
    } catch {
      /* ignore */
    }
  }

  const pyParts = await resolveHostPython();
  const [pyCmd, ...pyPre] = pyParts;
  if (!fs.existsSync(venvRoot())) {
    fs.mkdirSync(venvRoot(), { recursive: true });
    await run(pyCmd, [...pyPre, "-m", "venv", venvRoot()]);
  }

  const vpy = venvPython();
  if (!fs.existsSync(vpy)) {
    throw new Error(`Python venv is broken (missing ${vpy}). Delete ${venvRoot()} and retry.`);
  }

  const needInstall = readBootstrapVersion() !== BACKEND_BOOTSTRAP_VERSION;
  if (needInstall) {
    await run(vpy, ["-m", "pip", "install", "--upgrade", "pip"], { stdio: "inherit" });
    await run(vpy, ["-m", "pip", "install", "-e", "."], {
      cwd: paths.apiDir,
      stdio: "inherit",
      env: loadMergedEnv(),
    });
    writeBootstrapVersion();
  }
}

async function runMigrations(paths, env) {
  const alembic = venvBin("alembic");
  if (!fs.existsSync(alembic)) {
    await run(venvPython(), ["-m", "pip", "install", "-e", "."], {
      cwd: paths.apiDir,
      stdio: "inherit",
      env,
    });
  }
  await run(alembic, ["upgrade", "head"], { cwd: paths.apiDir, env, stdio: "inherit" });
}

function dockerCompose(paths, args) {
  const composeDir = path.dirname(paths.dockerComposeFile);
  return run("docker", ["compose", "-f", paths.dockerComposeFile, ...args], {
    cwd: composeDir,
    stdio: "inherit",
  });
}

function pushChild(proc) {
  if (proc?.pid) children.push(proc);
}

async function waitForApiReady(timeoutMs = 120_000) {
  const url = `http://${API_HOST}:${API_PORT}/v1/ready`;
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const r = await fetch(url);
      const j = await r.json();
      if (r.ok && j?.data?.status === "ready") return;
    } catch {
      /* not ready */
    }
    await new Promise((r) => setTimeout(r, 800));
  }
  throw new Error("API did not become ready (GET /v1/ready). Check Docker, Postgres, Redis, and API logs.");
}

function startBackendProcesses(env) {
  const apiDir = getPaths().apiDir;
  const vpy = venvPython();

  const api = spawn(vpy, ["-m", "director_api"], {
    cwd: apiDir,
    env,
    stdio: "inherit",
  });
  pushChild(api);

  const celery = venvBin("celery");
  const worker = spawn(celery, ["-A", "director_api.tasks.celery_app", "worker", "-l", "info"], {
    cwd: apiDir,
    env,
    stdio: "inherit",
  });
  pushChild(worker);

  const beat = spawn(celery, ["-A", "director_api.tasks.celery_app", "beat", "-l", "info"], {
    cwd: apiDir,
    env,
    stdio: "inherit",
  });
  pushChild(beat);
}

async function startUiServer(webDist) {
  uiPort = await getPort({ port: [4174, 4175, 4176, 4177, 4178] });
  const exp = express();
  // Do NOT mount at "/v1" — Express strips the mount prefix from req.url, so the API would see
  // "/projects" instead of "/v1/projects" and return 404. Match full path via pathFilter instead.
  exp.use(
    createProxyMiddleware({
      pathFilter: "/v1",
      target: `http://${API_HOST}:${API_PORT}`,
      changeOrigin: true,
    }),
  );
  exp.use(express.static(webDist));
  exp.get("*", (req, res) => {
    res.sendFile(path.join(webDist, "index.html"));
  });
  await new Promise((resolve, reject) => {
    uiServer = exp.listen(uiPort, API_HOST, (err) => {
      if (err) reject(err);
      else resolve();
    });
  });
  return uiPort;
}

function killAllChildren() {
  for (const c of children.splice(0)) {
    if (c?.pid) {
      try {
        treeKill(c.pid, "SIGTERM");
      } catch {
        try {
          c.kill("SIGTERM");
        } catch {
          /* ignore */
        }
      }
    }
  }
}

async function shutdown(paths) {
  return new Promise((resolve) => {
    if (uiServer) {
      uiServer.close(() => resolve());
      uiServer = null;
    } else {
      resolve();
    }
  }).then(async () => {
    killAllChildren();
    try {
      await dockerCompose(paths, ["down"]);
    } catch {
      /* ignore */
    }
  });
}

function showFatal(err) {
  console.error(err);
  dialog.showErrorBox("Directely failed to start", String(err?.message || err));
}

async function boot() {
  const paths = getPaths();
  if (!fs.existsSync(paths.webDist)) {
    throw new Error(
      `Web build missing: ${paths.webDist}\nRun: cd apps/web && npm run build\n(or from apps/electron: npm run build:web)`,
    );
  }
  if (!fs.existsSync(paths.apiDir)) {
    throw new Error(`API bundle missing: ${paths.apiDir}`);
  }
  if (!fs.existsSync(paths.dockerComposeFile)) {
    throw new Error(`docker-compose.yml missing: ${paths.dockerComposeFile}`);
  }

  ensureUserEnvFile(paths);
  const env = loadMergedEnv();

  await dockerCompose(paths, ["up", "-d"]);
  await ensurePythonVenv(paths);
  await runMigrations(paths, env);
  startBackendProcesses(env);
  await waitForApiReady();
  const port = await startUiServer(paths.webDist);
  return `http://${API_HOST}:${port}/`;
}

function createWindow(url) {
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

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  app.whenReady().then(async () => {
    const paths = getPaths();
    try {
      const url = await boot();
      app.on("before-quit", async (e) => {
        if (isQuitting) return;
        e.preventDefault();
        isQuitting = true;
        await shutdown(paths);
        app.exit(0);
      });
      createWindow(url);
    } catch (err) {
      showFatal(err);
      try {
        await shutdown(paths);
      } catch {
        /* ignore */
      }
      app.exit(1);
    }
  });

  app.on("window-all-closed", () => {
    if (process.platform !== "darwin") {
      app.quit();
    }
  });

  app.on("activate", async () => {
    if (BrowserWindow.getAllWindows().length === 0 && uiPort) {
      createWindow(`http://${API_HOST}:${uiPort}/`);
    }
  });
}
