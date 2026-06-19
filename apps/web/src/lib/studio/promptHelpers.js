/** Non-nested `[inner]` segments in narration (aligned with API `narration_bracket_visual`). */
export function extractBracketPhrasesFromNarration(narrationText) {
  const out = [];
  if (!narrationText || typeof narrationText !== "string") return out;
  const re = /\[([^\[\]]+)\]/g;
  let m;
  while ((m = re.exec(narrationText)) !== null) {
    const inner = String(m[1] || "").trim();
    if (inner) out.push(inner);
  }
  return out;
}

/** Matches worker `_scene_still_prompt_for_comfy` / `base_image_prompt_from_scene_fields` (bracket hints override package image_prompt). */
export function baseImagePromptFromScene(scene) {
  if (!scene || typeof scene !== "object") return "";
  const narr = String(scene.narration_text || "");
  const phrases = extractBracketPhrasesFromNarration(narr);
  if (phrases.length) {
    const joined = phrases.slice(0, 16).join("; ");
    return (
      `A single photoreal documentary still — abstract tableau: ${joined}. ` +
      "One cohesive composition; clear focal subject and setting implied by the hints."
    ).slice(0, 4000);
  }
  const pp = scene.prompt_package_json;
  const pkg = pp && typeof pp === "object" ? pp : {};
  const im = pkg.image_prompt;
  if (typeof im === "string" && im.trim()) return im.trim();
  return narr.slice(0, 1200);
}

/** Matches worker `video_text_prompt_from_scene_fields` (bracket hints before raw VO when no `video_prompt`). */
export function baseVideoPromptFromScene(scene) {
  if (!scene || typeof scene !== "object") return "";
  const pp = scene.prompt_package_json;
  const pkg = pp && typeof pp === "object" ? pp : {};
  const vp = pkg.video_prompt;
  if (typeof vp === "string" && vp.trim()) return vp.trim();
  const narr = String(scene.narration_text || "").trim();
  const phrases = extractBracketPhrasesFromNarration(narr);
  if (phrases.length) {
    const joined = phrases.slice(0, 16).join("; ");
    return (
      `Cinematic documentary shot: ${joined}. ` + "Subtle natural motion or slow camera move; one coherent beat."
    ).slice(0, 3000);
  }
  if (narr) return narr.slice(0, 3000);
  const p = String(scene.purpose || scene.visual_type || "").trim();
  return p ? p.slice(0, 3000) : "";
}

const resolvedPromptCache = new Map();

/** Fetch worker-resolved prompts from API (cached per scene id). */
export async function fetchResolvedPromptsForScene(sceneId, apiFn) {
  const sid = String(sceneId || "").trim();
  if (!sid) return { image_prompt: "", video_prompt: "" };
  if (resolvedPromptCache.has(sid)) return resolvedPromptCache.get(sid);
  const r = await apiFn(`/v1/scenes/${sid}/resolved-prompts`);
  const body = await r.json();
  if (!r.ok) throw new Error(body?.detail?.message || body?.message || `resolved-prompts ${r.status}`);
  const data = body?.data || {};
  const out = {
    image_prompt: String(data.image_prompt || ""),
    video_prompt: String(data.video_prompt || ""),
  };
  resolvedPromptCache.set(sid, out);
  return out;
}

export function clearResolvedPromptCache(sceneId) {
  if (sceneId) resolvedPromptCache.delete(String(sceneId));
  else resolvedPromptCache.clear();
}
