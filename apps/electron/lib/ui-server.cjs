"use strict";

const path = require("path");
const fs = require("fs");
const express = require("express");
const { createProxyMiddleware } = require("http-proxy-middleware");
const { getFreePort } = require("./ports.cjs");

/**
 * Serves Vite `dist` (absolute `/assets/...`) and proxies `/v1` to the FastAPI port.
 * @returns {Promise<{ port: number, close: () => Promise<void> }>}
 */
async function startUiServer({ distDir, apiPort }) {
  if (!fs.existsSync(path.join(distDir, "index.html"))) {
    throw new Error(`Web dist missing: ${path.join(distDir, "index.html")} — run: cd apps/web && npm run build`);
  }

  const app = express();
  const apiTarget = `http://127.0.0.1:${apiPort}`;

  app.use(
    "/v1",
    createProxyMiddleware({
      target: apiTarget,
      changeOrigin: true,
      ws: true,
      logLevel: "warn",
    }),
  );

  app.use(express.static(distDir, { index: false }));

  app.get("*", (req, res, next) => {
    if (req.path.startsWith("/v1")) return next();
    res.sendFile(path.join(distDir, "index.html"));
  });

  const port = await getFreePort("127.0.0.1");

  await new Promise((resolve, reject) => {
    const server = app.listen(port, "127.0.0.1", () => resolve());
    server.on("error", reject);
    app.locals._server = server;
  });

  return {
    port,
    close: () =>
      new Promise((resolve, reject) => {
        const server = app.locals._server;
        if (!server) return resolve();
        server.close((err) => (err ? reject(err) : resolve()));
      }),
  };
}

module.exports = { startUiServer };
