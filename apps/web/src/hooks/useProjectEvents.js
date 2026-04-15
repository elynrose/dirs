/**
 * useProjectEvents — subscribes to the /v1/events SSE stream for a project.
 *
 * Returns `{ sseConnected }` so callers can reduce or skip redundant HTTP polling
 * while the stream is live.
 *
 * @param {string|null} projectId  Project UUID, or null to disconnect.
 * @param {Object}      handlers
 * @param {Function}    [handlers.onConnected]       () => void — stream is live
 * @param {Function}    [handlers.onDisconnected]    () => void — stream dropped / reconnecting
 * @param {Function}    [handlers.onJobsUpdate]      (jobs: Job[]) => void
 * @param {Function}    [handlers.onAgentRunUpdate]  (run: object|null) => void
 * @param {Function}    [handlers.onAssetReady]      (asset: object) => void
 * @param {Function}    [handlers.onCeleryStatus]    (online: boolean) => void
 * @param {number} [reloadKey]  Bump after login / tenant change so SSE URL picks up new `access_token` query.
 * @returns {{ sseConnected: boolean }}
 */

import { useEffect, useRef, useState } from "react";
import { apiPath } from "../lib/api.js";
import { directorAuthQuerySuffix } from "../lib/directorAuthSession.js";

export function useProjectEvents(projectId, handlers, reloadKey = 0) {
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  const [sseConnected, setSseConnected] = useState(false);
  // Keep a ref too so reconnect closures see the latest value without re-running the effect.
  const connectedRef = useRef(false);

  useEffect(() => {
    if (!projectId) {
      setSseConnected(false);
      connectedRef.current = false;
      return;
    }

    const streamUrl = () =>
      apiPath(
        `/v1/events?project_id=${encodeURIComponent(projectId)}${directorAuthQuerySuffix()}`,
      );
    let es = new EventSource(streamUrl());
    let reconnectTimer = null;
    let destroyed = false;

    const markConnected = () => {
      if (!connectedRef.current) {
        connectedRef.current = true;
        setSseConnected(true);
        handlersRef.current.onConnected?.();
      }
    };

    const markDisconnected = () => {
      if (connectedRef.current) {
        connectedRef.current = false;
        setSseConnected(false);
        handlersRef.current.onDisconnected?.();
      }
    };

    const reconnect = (delayMs = 4000) => {
      if (reconnectTimer || destroyed) return;
      markDisconnected();
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        if (destroyed) return;
        es?.close();
        es = new EventSource(streamUrl());
        attach(es);
      }, delayMs);
    };

    const attach = (source) => {
      source.addEventListener("connected", () => markConnected());

      source.addEventListener("jobs_update", (e) => {
        try {
          markConnected();
          const data = JSON.parse(e.data);
          handlersRef.current.onJobsUpdate?.(data.jobs ?? []);
        } catch {}
      });

      source.addEventListener("agent_run_update", (e) => {
        try {
          markConnected();
          const data = JSON.parse(e.data);
          handlersRef.current.onAgentRunUpdate?.(data.run ?? null);
        } catch {}
      });

      source.addEventListener("asset_ready", (e) => {
        try {
          const data = JSON.parse(e.data);
          handlersRef.current.onAssetReady?.(data.asset);
        } catch {}
      });

      source.addEventListener("celery_status", (e) => {
        try {
          const data = JSON.parse(e.data);
          handlersRef.current.onCeleryStatus?.(Boolean(data.online));
        } catch {}
      });

      source.addEventListener("stream_end", () => {
        // Server closed intentionally (max lifetime) — reconnect immediately.
        reconnect(200);
      });

      source.onerror = () => {
        // Network error / server unavailable — back off before retrying.
        reconnect(4000);
      };
    };

    attach(es);

    return () => {
      destroyed = true;
      clearTimeout(reconnectTimer);
      es.close();
      // Don't call markDisconnected here — the component may be unmounting;
      // state updates on unmounted components are a no-op in React 18.
      connectedRef.current = false;
    };
  }, [projectId, reloadKey]);

  return { sseConnected };
}
