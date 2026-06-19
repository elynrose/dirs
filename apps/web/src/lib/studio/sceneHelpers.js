/** Chapters sorted by `order_index`. LLMs sometimes emit 0-based or 1-based indices — do not use `order_index + 1` for display. */
export function chaptersSorted(list) {
  return [...(list || [])].sort((a, b) => (Number(a?.order_index) || 0) - (Number(b?.order_index) || 0));
}

/** 1-based chapter number for UI labels (first chapter in sort order is always 1). */
export function chapterHumanNumber(list, chapterIdOrRow) {
  const id = typeof chapterIdOrRow === "string" ? chapterIdOrRow : chapterIdOrRow?.id;
  if (!id) return null;
  const i = chaptersSorted(list).findIndex((c) => String(c.id) === String(id));
  return i >= 0 ? i + 1 : null;
}

/** First succeeded image/video in timeline order — for scene list thumbnails. */
export function bestSceneListThumbAsset(rows) {
  if (!Array.isArray(rows) || !rows.length) return null;
  const ordered = rows.filter((a) => a.status !== "rejected");
  ordered.sort((a, b) => {
    const as = a.status === "succeeded" ? 1 : 0;
    const bs = b.status === "succeeded" ? 1 : 0;
    if (bs !== as) return bs - as;
    const seq = Number(a.timeline_sequence ?? 0) - Number(b.timeline_sequence ?? 0);
    if (seq !== 0) return seq;
    return new Date(a.created_at || 0).getTime() - new Date(b.created_at || 0).getTime();
  });
  return (
    ordered.find((r) => {
      if (r.status !== "succeeded") return false;
      const t = String(r.asset_type || "").toLowerCase();
      return t === "image" || t === "video";
    }) || null
  );
}

/** When no thumbnail: choose video vs image placeholder from assets or scene heuristics. */
export function sceneListFallbackThumbKind(scene, rows) {
  const hint = (scene?.visual_type || "").toLowerCase();
  if (/\bvideo\b|motion|footage|clip|b_roll|b roll/.test(hint)) return "video";
  if (/\bimage\b|photo|still/.test(hint)) return "image";
  if (rows?.some((r) => String(r.asset_type || "").toLowerCase() === "video")) return "video";
  return "image";
}
