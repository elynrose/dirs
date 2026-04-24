import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

/**
 * If the shell or a stale .env exports `VITE_API_BASE_URL=http://127.0.0.1:8000`, Vite would bake that into
 * the client bundle — then https://directely.com would try to fetch the *visitor's* loopback (CORS / failed fetch).
 * Production builds must not ship loopback API origins.
 */
function shouldStripLoopbackApiBase(mode, raw) {
  if (mode !== "production") return false;
  const s = String(raw ?? "").trim();
  if (!s) return false;
  try {
    const u = new URL(s);
    if (u.hostname === "127.0.0.1" || u.hostname === "localhost" || u.hostname === "[::1]") return true;
  } catch {
    if (/127\.0\.0\.1|localhost/i.test(s)) return true;
  }
  return false;
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const rawBase = env.VITE_API_BASE_URL;
  const stripLoopback = shouldStripLoopbackApiBase(mode, rawBase);
  /** Dev/preview only: Node on this host proxies /v1 → local API. Browsers still use same-origin `/v1`. */
  const apiProxyPort = env.VITE_API_PROXY_PORT || "8000";
  const apiProxy = {
    "/v1": { target: `http://127.0.0.1:${apiProxyPort}`, changeOrigin: true },
  };

  return {
  plugins: [react()],
  // Dev-only: stale `node_modules/.vite` can make Vite return HTTP 504 ("Outdated Optimize Dep") for
  // `/node_modules/.vite/deps/*.js` — the app script never runs and the browser shows a blank `#root`.
  ...(mode === "development"
    ? {
        optimizeDeps: {
          force: true,
        },
      }
    : {}),
  ...(stripLoopback
    ? {
        define: {
          "import.meta.env.VITE_API_BASE_URL": JSON.stringify(""),
        },
      }
    : {}),
  server: {
    port: Number(env.VITE_LOCAL_DEV_PORT || 5173),
    // Fail fast if something else (e.g. an old Vite) is on 5173 — avoids “wrong UI” on a surprise port.
    strictPort: true,
    // Remote dev (VPS): browsers often use the server IP; a fixed list misses it and Vite rejects the host.
    allowedHosts: true,
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
    allowedHosts: true,
    proxy: apiProxy,
    headers: {
      "Cache-Control": "no-store",
    },
  },
  // Production `dist`: hashed filenames under `/assets` (Vite default). At the reverse proxy or CDN,
  // serve `index.html` with short cache / `no-cache` and `/assets/*` with long `max-age` + `immutable`.
  build: {
    rollupOptions: {
      output: {
        entryFileNames: "assets/[name]-[hash].js",
        chunkFileNames: "assets/[name]-[hash].js",
        assetFileNames: "assets/[name]-[hash][extname]",
      },
    },
  },
};
});
