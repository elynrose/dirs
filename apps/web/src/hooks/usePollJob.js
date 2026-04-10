/**
 * usePollJob — polls GET /v1/jobs/{jobId} on an interval while `active` is true.
 *
 * Extracted from App.jsx so it can be used independently by any component that
 * needs to track a single background job.
 *
 * @param {string|null} jobId  Job UUID string, or null/empty to skip polling.
 * @param {boolean}     active Enable/disable polling (false = paused, no cleanup cost).
 * @param {number}      pollIntervalMs  Milliseconds between requests (default 800).
 * @returns {{ job: object|null, err: string|null }}
 */

import { useEffect, useState } from "react";
import { api } from "../lib/api.js";
import { parseJson } from "../lib/apiHelpers.js";

export function usePollJob(jobId, active, pollIntervalMs = 800) {
  const [job, setJob] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    if (!jobId || !active) return undefined;
    let cancelled = false;

    const tick = async () => {
      try {
        const r = await api(`/v1/jobs/${jobId}`);
        const j = await parseJson(r);
        if (!cancelled) {
          setJob(j.data ?? j);
          setErr(null);
        }
      } catch (e) {
        if (!cancelled) setErr(String(e));
      }
    };

    tick();
    const id = setInterval(tick, pollIntervalMs);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [jobId, active, pollIntervalMs]);

  return { job, err };
}
