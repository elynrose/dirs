/**
 * Directely desktop shell: Docker stack + Python venv (API, Celery worker+beat) + local UI server (/v1 proxy).
 */
import { app, BrowserWindow, dialog, ipcMain, Menu, shell } from "electron";
import path from "node:path";
import fs from "node:fs";
import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import express from "express";
import { createProxyMiddleware } from "http-proxy-middleware";
import getPort from "get-port";
import treeKill from "tree-kill";
import {
  clearSavedDockerExe,
  DOCKER_DESKTOP_DOWNLOAD_URL,
  getDockerConfigSummary,
  isDockerInstalled,
  resolveDockerExecutable,
  testDockerCompose,
  writeSavedDockerExe,
} from "./lib/docker-config.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

/** Bump when bundled backend layout or install steps change (forces pip reinstall). */
const BACKEND_BOOTSTRAP_VERSION = "2";

const API_HOST = "127.0.0.1";
const API_PORT = 8000;
/** Stable Compose project name (must match dev repo folder name so ports/containers are shared). */
const DOCKER_COMPOSE_PROJECT = "directely";

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

/**
 * Packaged apps started from the Start menu often inherit a short PATH, so `spawn("docker")`
 * or `spawn("py")` fails with `Error: spawn UNKNOWN` / ENOENT. Prepend well-known install dirs.
 */
function augmentedProcessEnv(extra = {}) {
  const base = { ...process.env, ...extra };
  if (process.platform !== "win32") {
    return base;
  }
  const additions = [];
  const add = (dir) => {
    if (dir && fs.existsSync(dir)) additions.push(dir);
  };
  const sr = process.env.SystemRoot || "C:\\Windows";
  add(sr);
  add(path.join(sr, "System32"));
  add(path.join(sr, "System32", "Wbem"));
  add(path.join(sr, "System32", "WindowsPowerShell", "v1.0"));
  const pf = process.env.ProgramFiles;
  const pf86 = process.env["ProgramFiles(x86)"];
  if (pf) add(path.join(pf, "Docker", "Docker", "resources", "bin"));
  if (pf86) add(path.join(pf86, "Docker", "Docker", "resources", "bin"));
  const la = process.env.LOCALAPPDATA;
  if (la) {
    for (const ver of ["Python314", "Python313", "Python312", "Python311"]) {
      add(path.join(la, "Programs", "Python", ver));
      add(path.join(la, "Programs", "Python", ver, "Scripts"));
    }
  }
  if (pf) {
    add(path.join(pf, "Python311"));
    add(path.join(pf, "Python312"));
    add(path.join(pf, "Python313"));
  }
  const uniq = [...new Set(additions)];
  const prefix = uniq.join(path.delimiter);
  const pathKey = Object.keys(base).find((k) => k.toLowerCase() === "path") || "PATH";
  const cur = base[pathKey] || "";
  const merged = prefix ? `${prefix}${path.delimiter}${cur}` : cur;
  base[pathKey] = merged;
  base.PATH = merged;
  return base;
}

function userDataDir() {
  return app.getPath("userData");
}

function resolvedDockerExe() {
  return resolveDockerExecutable({
    userData: userDataDir(),
    userEnvPath: userEnvPath(),
    mergedEnv: loadMergedEnv(),
  }).exe;
}

function dockerComposeCliWorks(dockerExe) {
  return testDockerCompose(dockerExe, loadMergedEnv()).ok;
}

async function pickDockerExeViaDialog() {
  const defaultPath =
    process.platform === "win32"
      ? path.join(process.env.ProgramFiles || "C:\\Program Files", "Docker", "Docker", "resources", "bin")
      : "/usr/local/bin";

  const openOpts = {
    title: process.platform === "win32" ? "Select docker.exe" : "Select docker CLI",
    defaultPath: fs.existsSync(defaultPath) ? defaultPath : undefined,
    properties: ["openFile"],
  };
  if (process.platform === "win32") {
    openOpts.filters = [{ name: "Docker CLI", extensions: ["exe"] }];
  }
  const { canceled, filePaths } = await dialog.showOpenDialog(openOpts);
  if (canceled || !filePaths?.[0]) return null;

  const picked = path.normalize(filePaths[0]);
  if (process.platform === "win32") {
    const base = path.basename(picked).toLowerCase();
    if (base !== "docker.exe") {
      const confirm = await dialog.showMessageBox({
        type: "question",
        buttons: ["Use this file", "Cancel"],
        defaultId: 0,
        cancelId: 1,
        message: `The selected file is "${path.basename(picked)}". It is usually named docker.exe.`,
      });
      if (confirm.response !== 0) return null;
    }
  }
  return picked;
}

/**
 * Ensure Docker CLI works. If Docker Desktop is not installed, ask the user to install it
 * (open download page). If installed but not on PATH / not running, offer locate docker.exe.
 * Runs before the stack is brought up (no BrowserWindow yet — native dialogs only).
 */
async function ensureDockerCli() {
  const env = loadMergedEnv();
  const installOpts = {
    userData: userDataDir(),
    userEnvPath: userEnvPath(),
    mergedEnv: env,
  };

  // Not installed at all → send user to Docker Desktop download (do not ask to browse for docker.exe).
  if (!isDockerInstalled(installOpts)) {
    for (let attempt = 0; attempt < 8; attempt++) {
      if (isDockerInstalled(installOpts) && dockerComposeCliWorks(resolvedDockerExe())) return;

      const pick = await dialog.showMessageBox({
        type: "warning",
        buttons: ["Open Docker download…", "I installed it — Retry", "Quit"],
        defaultId: 0,
        cancelId: 2,
        title: "Docker required",
        message: "Docker Desktop is not installed.",
        detail:
          "Directely needs Docker Desktop for the local database (PostgreSQL / Redis).\n\n" +
          "1. Install Docker Desktop from the official site.\n" +
          "2. Finish setup and start Docker Desktop once.\n" +
          "3. Return here and click Retry.\n\n" +
          DOCKER_DESKTOP_DOWNLOAD_URL,
      });

      if (pick.response === 2) {
        throw new Error(
          "Docker Desktop is required. Install it from https://www.docker.com/products/docker-desktop/ then start Directely again.",
        );
      }
      if (pick.response === 0) {
        await shell.openExternal(DOCKER_DESKTOP_DOWNLOAD_URL);
      }
      // Retry (response 1) or after opening download — re-check on next loop.
    }
    throw new Error(
      "Docker Desktop is still not available. Install it, start Docker Desktop, then launch Directely again.",
    );
  }

  const envHint =
    process.platform === "win32"
      ? "Typical Docker Desktop path:\nC:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe\n\n" +
        "If Docker Desktop is installed but not running, start it from the Start menu, wait until it says Ready, then Retry.\n\n" +
        "You can also set DOCKER_BIN in Settings → Desktop or in your app .env:\n" +
        userEnvPath()
      : "Start Docker Desktop (or the Docker daemon), or set DOCKER_BIN in Settings → Desktop or your app .env.";

  for (let attempt = 0; attempt < 5; attempt++) {
    const exe = resolvedDockerExe();
    if (dockerComposeCliWorks(exe)) return;

    const detail =
      attempt === 0
        ? `Docker appears installed, but Directely could not run:\n  ${exe} compose version\n\n${envHint}`
        : `Still could not run Docker Compose with:\n  ${exe}\n\n${envHint}`;

    const buttons =
      process.platform === "win32"
        ? ["Retry", "Locate docker.exe…", "Quit"]
        : ["Retry", "Locate docker CLI…", "Quit"];

    const pick = await dialog.showMessageBox({
      type: "warning",
      buttons,
      defaultId: 0,
      cancelId: 2,
      title: "Docker required",
      message: "Docker is required to start the local database stack (PostgreSQL / Redis).",
      detail,
    });

    if (pick.response === 2) {
      throw new Error(
        "Docker is required. Start Docker Desktop, or set DOCKER_BIN in Settings → Desktop or your .env, then start Directely again.",
      );
    }
    if (pick.response === 0) {
      continue; // Retry — engine may still be starting
    }

    const picked = await pickDockerExeViaDialog();
    if (!picked) {
      throw new Error("No Docker executable was selected.");
    }

    writeSavedDockerExe(userDataDir(), picked, { userEnvPath: userEnvPath() });
  }

  throw new Error("Could not configure Docker after multiple attempts.");
}

function registerDesktopIpc() {
  ipcMain.handle("desktop:getDockerConfig", () =>
    getDockerConfigSummary({
      userData: userDataDir(),
      userEnvPath: userEnvPath(),
      mergedEnv: loadMergedEnv(),
    }),
  );

  ipcMain.handle("desktop:testDocker", (_evt, exe) => {
    const target = String(exe || "").trim() || resolvedDockerExe();
    return testDockerCompose(target, loadMergedEnv());
  });

  ipcMain.handle("desktop:setDockerExe", (_evt, exe) => {
    const p = String(exe || "").trim();
    if (!p || !fs.existsSync(p)) {
      return { ok: false, error: "Path does not exist." };
    }
    const saved = writeSavedDockerExe(userDataDir(), p, { userEnvPath: userEnvPath() });
    const test = testDockerCompose(saved, loadMergedEnv());
    return { ok: test.ok, dockerExe: saved, testDetail: test.detail };
  });

  ipcMain.handle("desktop:browseDockerExe", async () => {
    const picked = await pickDockerExeViaDialog();
    if (!picked) return { canceled: true };
    const saved = writeSavedDockerExe(userDataDir(), picked, { userEnvPath: userEnvPath() });
    const test = testDockerCompose(saved, loadMergedEnv());
    return { canceled: false, dockerExe: saved, works: test.ok, testDetail: test.detail };
  });

  ipcMain.handle("desktop:clearDockerExe", () => {
    clearSavedDockerExe(userDataDir());
    const { exe, source } = resolveDockerExecutable({
      userData: userDataDir(),
      userEnvPath: userEnvPath(),
      mergedEnv: loadMergedEnv(),
    });
    const test = testDockerCompose(exe, loadMergedEnv());
    return { dockerExe: exe, source, works: test.ok, testDetail: test.detail };
  });

  ipcMain.handle("desktop:openUserDataFolder", () => {
    shell.openPath(userDataDir());
    return { path: userDataDir() };
  });
}

function buildAppMenu() {
  const template = [
    ...(process.platform === "darwin"
      ? [
          {
            label: app.name,
            submenu: [{ role: "quit" }],
          },
        ]
      : []),
    {
      label: "File",
      submenu: [process.platform === "win32" ? { role: "quit" } : { role: "close" }],
    },
    {
      label: "Directely",
      submenu: [
        {
          label: "Configure Docker…",
          click: async () => {
            const picked = await pickDockerExeViaDialog();
            if (!picked) return;
            writeSavedDockerExe(userDataDir(), picked, { userEnvPath: userEnvPath() });
            const test = testDockerCompose(picked, loadMergedEnv());
            await dialog.showMessageBox({
              type: test.ok ? "info" : "warning",
              title: "Docker",
              message: test.ok ? "Docker path saved and verified." : "Docker path saved but compose test failed.",
              detail: test.ok
                ? picked
                : `${picked}\n\n${test.detail || "Start Docker Desktop and try again."}`,
            });
          },
        },
        {
          label: "Open app data folder",
          click: () => {
            shell.openPath(userDataDir());
          },
        },
      ],
    },
    {
      label: "View",
      submenu: [{ role: "reload" }, { role: "toggleDevTools" }, { role: "resetZoom" }, { role: "zoomIn" }, { role: "zoomOut" }],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

function loadMergedEnv() {
  const base = augmentedProcessEnv();
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
  const { env: envOverride, captureOutput = false, ...spawnRest } = options;
  const env = envOverride ? { ...augmentedProcessEnv(), ...envOverride } : augmentedProcessEnv();
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, {
      stdio: captureOutput ? ["ignore", "pipe", "pipe"] : "inherit",
      ...spawnRest,
      env,
    });
    let out = "";
    let err = "";
    if (captureOutput) {
      child.stdout?.on("data", (d) => {
        out += d;
      });
      child.stderr?.on("data", (d) => {
        err += d;
      });
    }
    child.on("error", (errObj) => {
      reject(
        new Error(
          `Failed to run "${cmd}": ${errObj.message}${errObj.code != null ? ` (${String(errObj.code)})` : ""}. ` +
            (process.platform === "win32"
              ? "Install Docker Desktop and Python 3.11+, then fully quit and reopen Directely (GUI apps may not see a fresh PATH until you sign out or restart)."
              : "Ensure Docker and Python 3.11+ are installed and on PATH."),
        ),
      );
    });
    child.on("close", (code) => {
      if (code === 0) resolve();
      else {
        const tail = (err || out).trim().split(/\r?\n/).slice(-8).join("\n");
        const detail = tail ? `\n\n${tail}` : "";
        reject(new Error(`${cmd} ${args.join(" ")} exited ${code}${detail}`));
      }
    });
  });
}

/** `parts` = executable + optional launcher args, e.g. `['py', '-3.12']` or `['python3.12']`. */
function runPythonVersionCheck(parts) {
  const [cmd, ...pre] = parts;
  const env = augmentedProcessEnv();
  return new Promise((resolve) => {
    const child = spawn(cmd, [...pre, "-c", "import sys; sys.exit(0 if sys.version_info[:2]>=(3,11) else 1)"], {
      stdio: "ignore",
      env,
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
  let candidates;
  if (process.platform === "win32") {
    const la = process.env.LOCALAPPDATA;
    const sr = process.env.SystemRoot || "C:\\Windows";
    const absPythons = [];
    if (la) {
      for (const ver of ["Python314", "Python313", "Python312", "Python311"]) {
        const exe = path.join(la, "Programs", "Python", ver, "python.exe");
        if (fs.existsSync(exe)) absPythons.push([exe]);
      }
    }
    candidates = [
      ...absPythons,
      [path.join(sr, "py.exe")],
      ["py", "-3.13"],
      ["py", "-3.12"],
      ["py", "-3.11"],
      ["python"],
      ["python3"],
    ];
  } else {
    candidates = [
      ["python3.13"],
      ["python3.12"],
      ["python3.11"],
      ["python3"],
    ];
  }
  for (const parts of candidates) {
    if (await runPythonVersionCheck(parts)) return parts;
  }
  throw new Error(
    process.platform === "win32"
      ? "Python 3.11+ not found. Install from https://www.python.org/downloads/ (check 'Add to PATH'), restart Windows, then launch Directely again."
      : "Python 3.11+ not found on PATH. Install e.g. `brew install python@3.12` and ensure `python3.12` is on PATH, then restart Directely.",
  );
}

function venvPythonVersionOk() {
  const vpy = venvPython();
  if (!fs.existsSync(vpy)) return Promise.resolve(false);
  return new Promise((resolve) => {
    const child = spawn(vpy, ["-c", "import sys; sys.exit(0 if sys.version_info[:2]>=(3,11) else 1)"], {
      stdio: "ignore",
      env: augmentedProcessEnv(),
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
  const dockerExe = resolvedDockerExe();
  const env = { ...loadMergedEnv(), COMPOSE_PROJECT_NAME: DOCKER_COMPOSE_PROJECT };
  return run(
    dockerExe,
    ["compose", "-p", DOCKER_COMPOSE_PROJECT, "-f", paths.dockerComposeFile, ...args],
    {
      cwd: composeDir,
      captureOutput: true,
      env,
    },
  );
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

function celeryWorkerArgs() {
  const args = [
    "-A",
    "director_api.tasks.celery_app",
    "worker",
    "-Q",
    "text,media,compile",
    "-l",
    "info",
  ];
  if (process.platform === "win32") {
    args.push("--pool=solo");
  }
  return args;
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
  const worker = spawn(celery, celeryWorkerArgs(), {
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

  await ensureDockerCli();
  await dockerCompose(paths, ["up", "-d"]);
  await ensurePythonVenv(paths);
  await runMigrations(paths, env);
  startBackendProcesses(env);
  await waitForApiReady();
  const port = await startUiServer(paths.webDist);
  return `http://${API_HOST}:${port}/`;
}

function appIconPath() {
  const assets = path.join(__dirname, "build-assets");
  const ico = path.join(assets, "icon.ico");
  const png = path.join(assets, "icon.png");
  if (process.platform === "win32" && fs.existsSync(ico)) return ico;
  if (fs.existsSync(png)) return png;
  return undefined;
}

function createWindow(url) {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    icon: appIconPath(),
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      preload: path.join(__dirname, "preload.cjs"),
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
    registerDesktopIpc();
    buildAppMenu();
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
