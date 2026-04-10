"use strict";

const path = require("path");
const fs = require("fs");
const { app } = require("electron");

/**
 * Monorepo root when developing from `apps/electron` (…/director).
 * Packaged: `process.resourcesPath/studio` (mirrors repo: apps/, packages/, docker-compose.yml).
 */
function getStudioRoot() {
  if (process.env.DIRECTOR_STUDIO_ROOT && fs.existsSync(process.env.DIRECTOR_STUDIO_ROOT)) {
    return path.resolve(process.env.DIRECTOR_STUDIO_ROOT);
  }
  if (app && app.isPackaged) {
    return path.join(process.resourcesPath, "studio");
  }
  // apps/electron/lib -> repo root
  return path.resolve(__dirname, "..", "..", "..");
}

function getApiDir(studioRoot) {
  return path.join(studioRoot, "apps", "api");
}

function getWebDistDir(studioRoot) {
  return path.join(studioRoot, "apps", "web", "dist");
}

function getComposeFile(studioRoot) {
  return path.join(studioRoot, "docker-compose.yml");
}

function getVenvDir() {
  return path.join(app.getPath("userData"), "backend-venv");
}

function getStorageDir() {
  return path.join(app.getPath("userData"), "storage");
}

function getLogsDir() {
  return path.join(app.getPath("userData"), "logs");
}

module.exports = {
  getStudioRoot,
  getApiDir,
  getWebDistDir,
  getComposeFile,
  getVenvDir,
  getStorageDir,
  getLogsDir,
};
