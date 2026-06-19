function paramsJsonStringField(pj, key) {
  if (!pj || typeof pj !== "object") return "";
  const v = pj[key];
  return typeof v === "string" && v.trim() ? v.trim() : "";
}

/** Seconds this asset contributes toward scene narration coverage (clip vs planned duration). */
export function estAssetCoverSec(asset, clipSec) {
  const pj = asset?.params_json;
  if (pj && typeof pj === "object") {
    const d = Number(pj.planned_duration_sec);
    if (Number.isFinite(d) && d > 0) return d;
  }
  const t = String(asset?.asset_type || "").toLowerCase();
  if (t === "video" || t === "image") return clipSec;
  return 0;
}

/** Prompt text stored on the asset when it was generated (image/video). */
export function assetGenerationPrompt(asset) {
  const pj = asset?.params_json;
  if (!pj || typeof pj !== "object") return "";
  const at = String(asset?.asset_type || "").toLowerCase();
  if (at === "image") {
    const used = paramsJsonStringField(pj, "image_prompt_used");
    if (used) return used;
    const pp = pj.prompt_package_json;
    if (pp && typeof pp === "object" && typeof pp.image_prompt === "string" && pp.image_prompt.trim()) {
      return pp.image_prompt.trim();
    }
    return "";
  }
  if (at === "video") {
    return (
      paramsJsonStringField(pj, "prompt_used") ||
      paramsJsonStringField(pj, "video_prompt_resolved") ||
      paramsJsonStringField(pj, "video_prompt_base") ||
      ""
    );
  }
  return paramsJsonStringField(pj, "image_prompt_used") || paramsJsonStringField(pj, "prompt_used");
}
