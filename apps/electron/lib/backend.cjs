"use strict";

const fs = require("fs");
const path = require("path");
const { spawn, execFile } = require("child_process");
const { promisify } = require("util");
const treeKill = require("tree-kill");

const execFileAsync = promisify(execFile);

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function appendLogSync(logsDir, name, line) {
  try {
    fs.mkdirSync(logsDir, { recursive: true });
    fs.appendFileSync(path.join(logsDir, name), `${new Date().toISOString()} ${line}\n`);
  } catch {
    /* ignore */
  }
}

function dockerComposeArgs(studioRoot, composeFile, subcmd) {
  return ["compose", "--project-directory", studioRoot, "-f", composeFile, ...subcmd];
}

async function dockerCompose(studioRoot, composeFile, subcmd, logsDir, env = process.env) {
  appendLogSync(logsDir, "docker.log", `docker ${dockerComposeArgs(studioRoot, composeFile, subcmd).join(" ")}`);
  await new Promise((resolve, reject) => {
    const child = spawn("docker", dockerComposeArgs(studioRoot, composeFile, subcmd), {
      cwd: studioRoot,
      env,
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });
    let errBuf = "";
    child.stderr.on("data", (d) => {
      errBuf += d.toString();
    });
    child.stdout.on("data", (d) => appendLogSync(logsDir, "docker.log", d.toString().trimEnd()));
    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`docker compose failed (${code}): ${errBuf.slice(-2000)}`));
    });
  });
}

async function dockerUp(studioRoot, composeFile, logsDir, env = process.env) {
  await dockerCompose(studioRoot, composeFile, ["up", "-d"], logsDir, env);
}

async function dockerDown(studioRoot, composeFile, logsDir, env = process.env) {
  try {
    await dockerCompose(studioRoot, composeFile, ["down"], logsDir, env);
  } catch (e) {
    appendLogSync(logsDir, "docker.log", `docker down warning: ${e}`);
  }
}

/**
 * @returns {Promise<{ exe: string, prefix: string[] }>}
 */
async function resolvePythonInvoker(env = process.env) {
  const tryRun = async (exe, prefix, checkArgs) => {
    try {
      await execFileAsync(exe, [...prefix, ...checkArgs], { windowsHide: true, env });
      return { exe, prefix };
    } catch {
      return null;
    }
  };

  const check = ["-c", "import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)"];

  if (process.platform === "win32") {
    const tries = [
      ["py", ["-3.12"]],
      ["py", ["-3.11"]],
      ["py", ["-3"]],
      ["python", []],
      ["python3", []],
    ];
    for (const [exe, prefix] of tries) {
      const ok = await tryRun(exe, prefix, check);
      if (ok) return ok;
    }
  } else {
    const tries = [["python3.12", []], ["python3.11", []], ["python3", []], ["python", []]];
    for (const [exe, prefix] of tries) {
      const ok = await tryRun(exe, prefix, check);
      if (ok) return ok;
    }
  }
  throw new Error(
    "Python 3.11+ not found on PATH. Install Python 3.11+ and ensure `python3` (or Windows `py`) is available.",
  );
}

function venvPythonPath(venvDir) {
  return process.platform === "win32"
    ? path.join(venvDir, "Scripts", "python.exe")
    : path.join(venvDir, "bin", "python");
}

async function ensureVenv({ invoker, venvDir, studioRoot, apiDir, ffmpegDir, logsDir, env = process.env }) {
  const vpy = venvPythonPath(venvDir);
  if (!fs.existsSync(vpy)) {
    fs.mkdirSync(path.dirname(venvDir), { recursive: true });
    appendLogSync(logsDir, "backend.log", `creating venv: ${venvDir}`);
    await new Promise((resolve, reject) => {
      const child = spawn(invoker.exe, [...invoker.prefix, "-m", "venv", venvDir], {
        cwd: studioRoot,
        env,
        stdio: ["ignore", "pipe", "pipe"],
        windowsHide: true,
      });
      let err = "";
      child.stderr.on("data", (d) => {
        err += d.toString();
      });
      child.on("error", reject);
      child.on("close", (code) => (code === 0 ? resolve() : reject(new Error(`venv failed: ${err}`))));
    });
  }

  const runPip = async (args) => {
    appendLogSync(logsDir, "backend.log", `pip ${args.join(" ")}`);
    await new Promise((resolve, reject) => {
      const child = spawn(vpy, ["-m", "pip", ...args], {
        cwd: apiDir,
        env,
        stdio: ["ignore", "pipe", "pipe"],
        windowsHide: true,
      });
      let out = "";
      child.stdout.on("data", (d) => {
        out += d.toString();
      });
      child.stderr.on("data", (d) => {
        out += d.toString();
      });
      child.on("error", reject);
      child.on("close", (code) =>
        code === 0 ? resolve() : reject(new Error(`pip failed (${code}): ${out.slice(-4000)}`)),
      );
    });
  };

  await runPip(["install", "-U", "pip", "wheel", "setuptools"]);
  if (!fs.existsSync(ffmpegDir)) {
    throw new Error(`Missing ffmpeg-pipelines package at ${ffmpegDir}`);
  }
  await runPip(["install", "-e", ffmpegDir]);
  await runPip(["install", "-e", "."]);
}

async function runAlembicUpgrade(vpy, apiDir, env, logsDir) {
  appendLogSync(logsDir, "backend.log", "alembic upgrade head");
  await new Promise((resolve, reject) => {
    const child = spawn(vpy, ["-m", "alembic", "upgrade", "head"], {
      cwd: apiDir,
      env,
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });
    let out = "";
    child.stderr.on("data", (d) => {
      out += d.toString();
    });
    child.stdout.on("data", (d) => {
      out += d.toString();
    });
    child.on("error", reject);
    child.on("close", (code) =>
      code === 0 ? resolve() : reject(new Error(`alembic failed (${code}): ${out.slice(-4000)}`)),
    );
  });
}

function spawnService({ vpy, apiDir, env, logsDir, name, args }) {
  const logPath = path.join(logsDir, `${name}.log`);
  fs.mkdirSync(logsDir, { recursive: true });
  const fd = fs.openSync(logPath, "a");
  appendLogSync(logsDir, "backend.log", `start ${name}`);
  const child = spawn(vpy, args, {
    cwd: apiDir,
    env,
    stdio: ["ignore", fd, fd],
    windowsHide: true,
    detached: process.platform !== "win32",
  });
  child.on("error", (e) => appendLogSync(logsDir, "backend.log", `${name} error: ${e}`));
  return child;
}

async function waitForApiReady(apiPort, timeoutMs = 180000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`http://127.0.0.1:${apiPort}/v1/ready`);
      const j = await res.json();
      if (j?.data?.status === "ready") return;
    } catch {
      /* retry */
    }
    await sleep(800);
  }
  throw new Error(`Timed out waiting for http://127.0.0.1:${apiPort}/v1/ready`);
}

function killTree(pid) {
  return new Promise((resolve) => {
    if (!pid) return resolve();
    treeKill(pid, "SIGTERM", () => resolve());
  });
}

module.exports = {
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
};
