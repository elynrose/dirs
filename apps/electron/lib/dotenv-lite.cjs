"use strict";

const fs = require("fs");
const path = require("path");

/**
 * Minimal KEY=VAL .env loader (no multiline values). Merges into `target` and returns it.
 */
function loadEnvFile(filePath, target = {}) {
  if (!fs.existsSync(filePath)) return target;
  const raw = fs.readFileSync(filePath, "utf8");
  for (const line of raw.split(/\n/)) {
    const t = line.trim();
    if (!t || t.startsWith("#")) continue;
    const eq = t.indexOf("=");
    if (eq <= 0) continue;
    const key = t.slice(0, eq).trim();
    let val = t.slice(eq + 1).trim();
    if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
      val = val.slice(1, -1);
    }
    if (key) target[key] = val;
  }
  return target;
}

function loadStudioEnv(studioRoot) {
  const merged = { ...process.env };
  loadEnvFile(path.join(studioRoot, ".env"), merged);
  loadEnvFile(path.join(studioRoot, "apps", "api", ".env"), merged);
  return merged;
}

module.exports = { loadEnvFile, loadStudioEnv };
