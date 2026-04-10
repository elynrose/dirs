import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const apiProxy = {
  "/v1": { target: "http://127.0.0.1:8000", changeOrigin: true },
};

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Fail fast if something else (e.g. an old Vite) is on 5173 — avoids “wrong UI” on a surprise port.
    strictPort: true,
    // Allow access via VPS hostname (e.g. srv1360778.hstgr.cloud), custom domain (directely.com), not only localhost.
    allowedHosts: [".hstgr.cloud", ".directely.com", "directely.com", "localhost"],
    proxy: apiProxy,
    headers: {
      // Local dev: stop browsers from serving a cached index.html / stale JS after git pull or restart.
      "Cache-Control": "no-store",
    },
  },
  // `npm run preview` also needs the proxy or every `/v1/...` fetch (including images) goes 404.
  preview: {
    port: 4173,
    strictPort: true,
    allowedHosts: [".hstgr.cloud", ".directely.com", "directely.com", "localhost"],
    proxy: apiProxy,
    headers: {
      "Cache-Control": "no-store",
    },
  },
});
