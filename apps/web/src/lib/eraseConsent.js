/** Matches API ``EraseConfirmationRequired.to_dict()`` / worker ``block_code`` flows. */

function agentRunHttpErrorMessage(body, httpStatus) {
  const stHint = typeof httpStatus === "number" ? ` (HTTP ${httpStatus})` : "";
  const d = body?.detail;
  if (typeof d === "string") return `${d}${stHint}`;
  if (d && typeof d === "object") {
    if (typeof d.message === "string") return d.message;
    if (String(d.code || "") === ERASE_CONFIRMATION_CODE) {
      return "Confirmation required before replacing existing project content.";
    }
  }
  if (typeof body?.message === "string") return body.message;
  return `Request failed${stHint}`;
}

export const ERASE_CONFIRMATION_CODE = "ERASE_CONFIRMATION_REQUIRED";

export function parseEraseConfirmationDetail(detail) {
  if (!detail || typeof detail !== "object") return null;
  if (String(detail.code || "") !== ERASE_CONFIRMATION_CODE) return null;
  const scope = detail.scope && typeof detail.scope === "object" ? detail.scope : {};
  return {
    scopeLabel: String(detail.scope_label || "outline"),
    scope: {
      scene_count: Number(scope.scene_count) || 0,
      image_asset_count: Number(scope.image_asset_count) || 0,
      video_asset_count: Number(scope.video_asset_count) || 0,
      chapter_count: Number(scope.chapter_count) || 0,
      has_content_to_erase: Boolean(scope.has_content_to_erase),
    },
  };
}

export function parseEraseConfirmationFromErrorMessage(raw) {
  if (raw == null) return null;
  const s = String(raw).trim();
  if (!s) return null;
  if (s.startsWith("{")) {
    try {
      return parseEraseConfirmationDetail(JSON.parse(s));
    } catch {
      /* fall through */
    }
  }
  const idx = s.indexOf('{"code"');
  if (idx >= 0) {
    try {
      return parseEraseConfirmationDetail(JSON.parse(s.slice(idx)));
    } catch {
      /* fall through */
    }
  }
  if (s.includes(ERASE_CONFIRMATION_CODE)) {
    const m = s.match(/\{[\s\S]*"code"\s*:\s*"ERASE_CONFIRMATION_REQUIRED"[\s\S]*\}/);
    if (m) {
      try {
        return parseEraseConfirmationDetail(JSON.parse(m[0]));
      } catch {
        return null;
      }
    }
  }
  return null;
}

export function eraseConfirmationFromAgentRun(run) {
  if (!run || typeof run !== "object") return null;
  if (run.block_code === "erase_confirmation_required") {
    return parseEraseConfirmationFromErrorMessage(run.error_message);
  }
  return parseEraseConfirmationFromErrorMessage(run.error_message);
}

export function eraseScopeActionLabel(scopeLabel) {
  const key = String(scopeLabel || "outline").trim().toLowerCase();
  const map = {
    outline: "story outline",
    scenes_replan: "scene plan",
    chapter_scenes_replan: "chapter scenes",
  };
  return map[key] || key.replace(/_/g, " ");
}

export function formatEraseScopeBullets(scope) {
  if (!scope || typeof scope !== "object") return [];
  const bullets = [];
  const chapters = Number(scope.chapter_count) || 0;
  const scenes = Number(scope.scene_count) || 0;
  const images = Number(scope.image_asset_count) || 0;
  const videos = Number(scope.video_asset_count) || 0;
  if (chapters > 0) bullets.push(`${chapters} chapter${chapters === 1 ? "" : "s"}`);
  if (scenes > 0) bullets.push(`${scenes} scene${scenes === 1 ? "" : "s"}`);
  if (images > 0) bullets.push(`${images} image${images === 1 ? "" : "s"}`);
  if (videos > 0) bullets.push(`${videos} video clip${videos === 1 ? "" : "s"}`);
  return bullets;
}

export function withEraseConsentPipelineOptions(pipelineOptions) {
  const base = pipelineOptions && typeof pipelineOptions === "object" ? pipelineOptions : {};
  return { ...base, confirm_erase_assets: true };
}

/** @returns {{ ok: true, body: object, response: Response } | { ok: false, needsEraseConfirmation: true, erase: object, requestBody: object, body: object, response: Response }} */
export async function postAgentRun(apiFetch, parseJsonFn, requestBody) {
  const r = await apiFetch("/v1/agent-runs", {
    method: "POST",
    body: JSON.stringify(requestBody),
  });
  const body = await parseJsonFn(r);
  if (r.status === 409) {
    const erase = parseEraseConfirmationDetail(body?.detail);
    if (erase) {
      return {
        ok: false,
        needsEraseConfirmation: true,
        erase,
        requestBody,
        body,
        response: r,
      };
    }
  }
  if (!r.ok) {
    const err = new Error(agentRunHttpErrorMessage(body, r.status));
    err.apiBody = body;
    err.httpStatus = r.status;
    throw err;
  }
  return { ok: true, body, response: r };
}
