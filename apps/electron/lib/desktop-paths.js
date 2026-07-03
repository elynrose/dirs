/**
 * Canonical paths for Directely desktop (dev repo vs packaged Windows/macOS install).
 */
import fs from "node:fs";
import path from "node:path";

/** @typedef {{
 *   mode: "dev" | "packaged",
 *   resourcesRoot: string,
 *   apiDir: string,
 *   ffmpegPipelinesDir: string,
 *   webDist: string,
 *   dockerComposeFile: string,
 *   envExample: string,
 *   userData: string,
 *   userEnvFile: string,
 *   storageRoot: string,
 *   venvRoot: string,
 *   bootstrapMarker: string,
 *   logsDir: string,
 *   dockerCliConfig: string,
 * }} DesktopLayout */

/**
 * @param {object} opts
 * @param {boolean} opts.isPackaged
 * @param {string} opts.electronDir  apps/electron (dirname of main.js)
 * @param {string} opts.resourcesPath  process.resourcesPath when packaged
 * @param {string} opts.userData  app.getPath("userData")
 * @param {string} opts.appData  app.getPath("appData") — Roaming on Windows
 */
export function resolveDesktopLayout({ isPackaged, electronDir, resourcesPath, userData, appData }) {
  const repoRoot = path.join(electronDir, "..", "..");

  /** @type {Omit<DesktopLayout, "storageRoot"> & { storageRoot?: string }} */
  const layout = isPackaged
    ? {
        mode: "packaged",
        resourcesRoot: resourcesPath,
        apiDir: path.join(resourcesPath, "apps", "api"),
        ffmpegPipelinesDir: path.join(resourcesPath, "packages", "ffmpeg-pipelines"),
        webDist: path.join(resourcesPath, "web-dist"),
        dockerComposeFile: path.join(resourcesPath, "docker-compose.yml"),
        envExample: path.join(resourcesPath, ".env.example"),
        userData,
        userEnvFile: path.join(userData, ".env"),
        venvRoot: path.join(userData, "backend-venv"),
        bootstrapMarker: path.join(userData, ".director-backend-bootstrap"),
        logsDir: path.join(userData, "logs"),
        dockerCliConfig: path.join(userData, "docker-cli.json"),
      }
    : {
        mode: "dev",
        resourcesRoot: repoRoot,
        apiDir: path.join(repoRoot, "apps", "api"),
        ffmpegPipelinesDir: path.join(repoRoot, "packages", "ffmpeg-pipelines"),
        webDist: path.join(repoRoot, "apps", "web", "dist"),
        dockerComposeFile: path.join(repoRoot, "docker-compose.yml"),
        envExample: path.join(repoRoot, ".env.example"),
        userData,
        userEnvFile: path.join(userData, ".env"),
        venvRoot: path.join(userData, "backend-venv"),
        bootstrapMarker: path.join(userData, ".director-backend-bootstrap"),
        logsDir: path.join(userData, "logs"),
        dockerCliConfig: path.join(userData, "docker-cli.json"),
      };

  layout.storageRoot = resolveStorageRoot({
    isPackaged,
    layout,
    appData,
    repoRoot,
  });

  return /** @type {DesktopLayout} */ (layout);
}

function dirHasFiles(dir) {
  try {
    if (!fs.existsSync(dir)) return false;
    return fs.readdirSync(dir).some((name) => {
      const p = path.join(dir, name);
      try {
        const st = fs.statSync(p);
        return st.isFile() || st.isDirectory();
      } catch {
        return false;
      }
    });
  } catch {
    return false;
  }
}

/**
 * Default media storage:
 * - dev: <repo>/data/storage (matches browser Studio)
 * - packaged: <userData>/storage
 * - packaged first run: reuse dev Electron storage if userData/storage is empty
 */
function resolveStorageRoot({ isPackaged, layout, appData, repoRoot }) {
  const packagedDefault = path.join(layout.userData, "storage");
  if (!isPackaged) {
    return path.join(repoRoot, "data", "storage");
  }

  if (dirHasFiles(packagedDefault)) {
    return packagedDefault;
  }

  // npm start uses package name "director-electron"; NSIS install uses productName "Directely".
  const legacyDevElectron = path.join(appData, "director-electron", "storage");
  if (legacyDevElectron !== packagedDefault && dirHasFiles(legacyDevElectron)) {
    return legacyDevElectron;
  }

  return packagedDefault;
}

export function loadRepoDotEnv(resourcesRoot) {
  const envPath = path.join(resourcesRoot, ".env");
  if (!fs.existsSync(envPath)) return {};
  return parseDotEnv(fs.readFileSync(envPath, "utf8"));
}

export function parseDotEnv(text) {
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

/** First-run `.env` under userData — absolute paths, not the full repo `.env.example`. */
export function buildDefaultUserEnvFileContent(layout) {
  const lines = [
    "# Directely desktop — generated on first launch. Restart the app after edits.",
    `# Mode: ${layout.mode}`,
    "",
    `LOCAL_STORAGE_ROOT=${layout.storageRoot}`,
    "STORAGE_BACKEND=filesystem",
    "",
    "DATABASE_URL=postgresql+psycopg://director:director_dev_change_me@localhost:5433/director",
    "REDIS_URL=redis://localhost:6379/0",
    "",
    "# Run agent jobs in-process (recommended for desktop installs).",
    "CELERY_EAGER=true",
    "",
    "# Local Kokoro TTS: auto-install on first use if missing from the backend venv.",
    "TTS_AUTO_PIP_INSTALL=true",
    "",
    "# Optional: full path when ffmpeg is not on PATH (Windows example below).",
    "# FFMPEG_BIN=C:\\ffmpeg\\bin\\ffmpeg.exe",
    "# FFPROBE_BIN=C:\\ffmpeg\\bin\\ffprobe.exe",
    "# DOCKER_BIN=C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe",
    "",
  ];
  return `${lines.join("\n")}\n`;
}

export function ensureUserEnvFile(layout) {
  fs.mkdirSync(layout.userData, { recursive: true });
  if (fs.existsSync(layout.userEnvFile)) return;
  fs.writeFileSync(layout.userEnvFile, buildDefaultUserEnvFileContent(layout), "utf8");
}

export function appendPathLog(layout, line) {
  try {
    fs.mkdirSync(layout.logsDir, { recursive: true });
    fs.appendFileSync(path.join(layout.logsDir, "directely-paths.log"), `${new Date().toISOString()} ${line}\n`);
  } catch {
    /* ignore */
  }
}

export function augmentPathEnv(base, platform) {
  const additions = [];
  const add = (dir) => {
    if (dir && fs.existsSync(dir)) additions.push(dir);
  };

  if (platform === "win32") {
    const sr = base.SystemRoot || process.env.SystemRoot || "C:\\Windows";
    add(sr);
    add(path.join(sr, "System32"));
    add(path.join(sr, "System32", "Wbem"));
    add(path.join(sr, "System32", "WindowsPowerShell", "v1.0"));
    const pf = base.ProgramFiles || process.env.ProgramFiles;
    const pf86 = base["ProgramFiles(x86)"] || process.env["ProgramFiles(x86)"];
    if (pf) {
      add(path.join(pf, "Docker", "Docker", "resources", "bin"));
      add(path.join(pf, "ffmpeg", "bin"));
      for (const ver of ["Python311", "Python312"]) {
        add(path.join(pf, ver));
      }
    }
    if (pf86) add(path.join(pf86, "Docker", "Docker", "resources", "bin"));
    const la = base.LOCALAPPDATA || process.env.LOCALAPPDATA;
    if (la) {
      for (const ver of ["Python311", "Python312"]) {
        add(path.join(la, "Programs", "Python", ver));
        add(path.join(la, "Programs", "Python", ver, "Scripts"));
      }
    }
    add("C:\\ffmpeg\\bin");
  } else if (platform === "darwin") {
    add("/opt/homebrew/bin");
    add("/opt/homebrew/sbin");
    add("/usr/local/bin");
    add("/usr/local/sbin");
    add("/Applications/Docker.app/Contents/Resources/bin");
    const home = base.HOME || process.env.HOME;
    if (home) {
      add(path.join(home, ".local", "bin"));
    }
  }

  const pathKey = Object.keys(base).find((k) => k.toLowerCase() === "path") || "PATH";
  const cur = base[pathKey] || "";
  const prefix = [...new Set(additions)].join(path.delimiter);
  const merged = prefix ? `${prefix}${path.delimiter}${cur}` : cur;
  base[pathKey] = merged;
  base.PATH = merged;
  return base;
}

export function applyFfmpegEnvDefaults(base, platform) {
  const configured = String(base.FFMPEG_BIN || base.ffmpeg_bin || "").trim();
  if (configured && fs.existsSync(configured)) return base;

  const candidates = [];
  if (platform === "win32") {
    candidates.push("C:\\ffmpeg\\bin\\ffmpeg.exe");
    const pf = process.env.ProgramFiles;
    if (pf) candidates.push(path.join(pf, "ffmpeg", "bin", "ffmpeg.exe"));
  } else {
    candidates.push(
      "/opt/homebrew/bin/ffmpeg",
      "/usr/local/bin/ffmpeg",
      "/usr/bin/ffmpeg",
    );
  }

  const pathKey = Object.keys(base).find((k) => k.toLowerCase() === "path") || "PATH";
  for (const dir of String(base[pathKey] || "").split(path.delimiter)) {
    if (!dir) continue;
    candidates.push(path.join(dir, platform === "win32" ? "ffmpeg.exe" : "ffmpeg"));
  }

  for (const exe of candidates) {
    if (!exe || !fs.existsSync(exe)) continue;
    base.FFMPEG_BIN = exe;
    const probe = exe.replace(/ffmpeg(\.exe)?$/i, platform === "win32" ? "ffprobe.exe" : "ffprobe");
    if (fs.existsSync(probe)) base.FFPROBE_BIN = probe;
    break;
  }
  return base;
}
