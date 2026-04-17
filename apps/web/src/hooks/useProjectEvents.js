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
 * @param {number} [reloadKey]  Bump after login / tenant change so the EventSource reconnects with fresh cookies.
 * @returns {{ sseConnected: boolean }}
 */

import { useEffect, useRef, useState } from "react";
import { apiPath } from "../lib/api.js";
import { directorAuthQuerySuffix } from "../lib/directorAuthSession.js";

function parseSseJson(data, eventName) {
  try {
    return JSON.parse(data);
  } catch (e) {
    if (import.meta.env?.DEV) {
      console.warn(`[useProjectEvents] invalid JSON in ${eventName}`, e);
    }
    return null;
  }
}

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
        markConnected();
        const data = parseSseJson(e.data, "jobs_update");
        if (!data) return;
        handlersRef.current.onJobsUpdate?.(data.jobs ?? []);
      });

      source.addEventListener("agent_run_update", (e) => {
        markConnected();
        const data = parseSseJson(e.data, "agent_run_update");
        if (!data) return;
        handlersRef.current.onAgentRunUpdate?.(data.run ?? null);
      });

      source.addEventListener("asset_ready", (e) => {
        const data = parseSseJson(e.data, "asset_ready");
        if (!data) return;
        handlersRef.current.onAssetReady?.(data.asset);
      });

      source.addEventListener("celery_status", (e) => {
        const data = parseSseJson(e.data, "celery_status");
        if (!data) return;
        handlersRef.current.onCeleryStatus?.(Boolean(data.online));
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
