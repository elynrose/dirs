/**
 * Docker CLI resolution for the Directely desktop shell (Windows / macOS / Linux).
 */
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";

/** Official Docker Desktop download page (Windows / macOS). */
export const DOCKER_DESKTOP_DOWNLOAD_URL = "https://www.docker.com/products/docker-desktop/";

export function dockerCliConfigPath(userData) {
  return path.join(userData, "docker-cli.json");
}

export function readSavedDockerExe(userData) {
  try {
    const j = JSON.parse(fs.readFileSync(dockerCliConfigPath(userData), "utf8"));
    const exe = typeof j.dockerExe === "string" ? j.dockerExe.trim() : "";
    if (exe && fs.existsSync(exe)) return path.normalize(exe);
  } catch {
    /* missing or invalid */
  }
  return null;
}

function upsertEnvLine(text, key, value) {
  const lines = text.split("\n");
  const prefix = `${key}=`;
  let found = false;
  const out = lines.map((line) => {
    if (line.trimStart().startsWith(prefix)) {
      found = true;
      return `${key}=${value}`;
    }
    return line;
  });
  if (!found) {
    if (out.length && out[out.length - 1] !== "") out.push("");
    out.push(`# Docker CLI (set via Settings → Desktop or first-run picker)`);
    out.push(`${key}=${value}`);
  }
  return `${out.join("\n").replace(/\n*$/, "")}\n`;
}

/** Persist docker.exe path to docker-cli.json and sync DOCKER_BIN in user .env. */
export function writeSavedDockerExe(userData, exe, { userEnvPath } = {}) {
  const normalized = path.normalize(String(exe || "").trim());
  fs.mkdirSync(userData, { recursive: true });
  fs.writeFileSync(dockerCliConfigPath(userData), JSON.stringify({ dockerExe: normalized }, null, 2), "utf8");
  if (userEnvPath) {
    try {
      const cur = fs.existsSync(userEnvPath) ? fs.readFileSync(userEnvPath, "utf8") : "";
      fs.writeFileSync(userEnvPath, upsertEnvLine(cur, "DOCKER_BIN", normalized), "utf8");
    } catch {
      /* ignore */
    }
  }
  return normalized;
}

export function clearSavedDockerExe(userData) {
  try {
    fs.unlinkSync(dockerCliConfigPath(userData));
  } catch {
    /* ignore */
  }
}

function readWindowsRegistryInstallPath() {
  if (process.platform !== "win32") return [];
  const keys = [
    ["HKLM\\SOFTWARE\\Docker Inc.\\Docker Desktop", "InstallPath"],
    ["HKLM\\SOFTWARE\\WOW6432Node\\Docker Inc.\\Docker Desktop", "InstallPath"],
    ["HKCU\\SOFTWARE\\Docker Inc.\\Docker Desktop", "InstallPath"],
  ];
  const out = [];
  for (const [key, valueName] of keys) {
    const r = spawnSync("reg", ["query", key, "/v", valueName], {
      encoding: "utf8",
      windowsHide: true,
      timeout: 10_000,
    });
    if (r.status !== 0 || !r.stdout) continue;
    const m = r.stdout.match(new RegExp(`${valueName}\\s+REG_\\w+\\s+(.+)`, "i"));
    if (!m) continue;
    const install = m[1].trim();
    if (install) out.push(install);
  }
  return out;
}

/** Well-known docker / docker.exe locations (existing files only). */
export function discoverDockerExeCandidates(platform = process.platform) {
  const candidates = [];
  const add = (p) => {
    if (!p) return;
    const norm = path.normalize(p);
    if (fs.existsSync(norm)) candidates.push(norm);
  };

  if (platform === "win32") {
    const pf = process.env.ProgramFiles;
    const pf86 = process.env["ProgramFiles(x86)"];
    const la = process.env.LOCALAPPDATA;
    if (pf) add(path.join(pf, "Docker", "Docker", "resources", "bin", "docker.exe"));
    if (pf86) add(path.join(pf86, "Docker", "Docker", "resources", "bin", "docker.exe"));
    if (la) {
      add(path.join(la, "Docker", "resources", "bin", "docker.exe"));
      add(path.join(la, "Programs", "Docker", "Docker", "resources", "bin", "docker.exe"));
    }
    for (const install of readWindowsRegistryInstallPath()) {
      add(path.join(install, "resources", "bin", "docker.exe"));
      add(path.join(install, "docker.exe"));
    }
  } else if (platform === "darwin") {
    add("/usr/local/bin/docker");
    add("/opt/homebrew/bin/docker");
    add("/Applications/Docker.app/Contents/Resources/bin/docker");
  } else {
    add("/usr/bin/docker");
    add("/usr/local/bin/docker");
  }

  return [...new Set(candidates)];
}

/** Docker Desktop GUI app path when installed (Windows / macOS). */
export function discoverDockerDesktopApp(platform = process.platform) {
  const candidates = [];
  const add = (p) => {
    if (!p) return;
    const norm = path.normalize(p);
    if (fs.existsSync(norm)) candidates.push(norm);
  };

  if (platform === "win32") {
    const pf = process.env.ProgramFiles;
    const pf86 = process.env["ProgramFiles(x86)"];
    if (pf) add(path.join(pf, "Docker", "Docker", "Docker Desktop.exe"));
    if (pf86) add(path.join(pf86, "Docker", "Docker", "Docker Desktop.exe"));
    for (const install of readWindowsRegistryInstallPath()) {
      add(path.join(install, "Docker Desktop.exe"));
    }
  } else if (platform === "darwin") {
    add("/Applications/Docker.app");
  }

  return candidates[0] || null;
}

/**
 * True when Docker CLI or Docker Desktop appears installed on this machine
 * (file discovery, registry, or `docker` on PATH that responds to `compose version`).
 */
export function isDockerInstalled({ userData, userEnvPath, mergedEnv = {} } = {}) {
  if (discoverDockerExeCandidates().length > 0) return true;
  if (discoverDockerDesktopApp()) return true;

  const saved = userData ? readSavedDockerExe(userData) : null;
  if (saved) return true;

  const fromEnv = String(mergedEnv.DOCKER_BIN || "")
    .trim()
    .replace(/^["']|["']$/g, "");
  if (fromEnv && fs.existsSync(fromEnv)) return true;

  const onPath = spawnSync("docker", ["compose", "version"], {
    encoding: "utf8",
    timeout: 15_000,
    windowsHide: true,
    env: mergedEnv,
  });
  return onPath.status === 0;
}

/**
 * Resolve docker CLI: saved path → DOCKER_BIN in .env → auto-discover → `docker` on PATH.
 * @returns {{ exe: string, source: "saved"|"env"|"discovered"|"path" }}
 */
export function resolveDockerExecutable({ userData, userEnvPath, mergedEnv = {} }) {
  const saved = readSavedDockerExe(userData);
  if (saved) return { exe: saved, source: "saved" };

  const fromEnv = String(mergedEnv.DOCKER_BIN || "")
    .trim()
    .replace(/^["']|["']$/g, "");
  if (fromEnv && fs.existsSync(fromEnv)) {
    return { exe: path.normalize(fromEnv), source: "env" };
  }

  for (const p of discoverDockerExeCandidates()) {
    return { exe: p, source: "discovered" };
  }

  return { exe: "docker", source: "path" };
}

export function testDockerCompose(dockerExe, env = process.env) {
  const r = spawnSync(dockerExe, ["compose", "version"], {
    encoding: "utf8",
    timeout: 30_000,
    windowsHide: true,
    env,
  });
  const ok = r.status === 0;
  const detail = (r.stderr || r.stdout || "").trim().slice(-500);
  return { ok, detail, status: r.status ?? -1 };
}

export function getDockerConfigSummary({ userData, userEnvPath, mergedEnv = {} }) {
  const { exe, source } = resolveDockerExecutable({ userData, userEnvPath, mergedEnv });
  const test = testDockerCompose(exe, mergedEnv);
  const installed = isDockerInstalled({ userData, userEnvPath, mergedEnv });
  return {
    dockerExe: exe,
    source,
    works: test.ok,
    testDetail: test.detail,
    installed,
    dockerDesktopApp: discoverDockerDesktopApp(),
    downloadUrl: DOCKER_DESKTOP_DOWNLOAD_URL,
    userDataPath: userData,
    userEnvPath,
    typicalPaths: discoverDockerExeCandidates(),
  };
}
