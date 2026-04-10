/**
 * Loading skeleton components — pulsing placeholder shapes shown while data loads.
 *
 * Replaces blank panels that flash between navigating projects / chapters.
 *
 * Usage:
 *   import { SkeletonText, SkeletonCard, SkeletonAssetGrid } from "./LoadingSkeleton.jsx";
 *
 *   {loading ? <SkeletonCard lines={3} /> : <MyContent />}
 */

/** Single skeleton line. `width` defaults to "100%". */
export function SkeletonLine({ width = "100%", height = "0.85rem", style = {} }) {
  return (
    <div
      className="skeleton-line"
      style={{ width, height, ...style }}
      aria-hidden="true"
    />
  );
}

/** A block of skeleton text lines (paragraph placeholder). */
export function SkeletonText({ lines = 3, lastLineWidth = "65%" }) {
  return (
    <div className="skeleton-text" aria-hidden="true">
      {Array.from({ length: lines }, (_, i) => (
        <SkeletonLine
          key={i}
          width={i === lines - 1 ? lastLineWidth : "100%"}
          style={{ marginBottom: "0.45rem" }}
        />
      ))}
    </div>
  );
}

/** A card-shaped skeleton (title + body lines). */
export function SkeletonCard({ lines = 3, titleWidth = "55%" }) {
  return (
    <div className="skeleton-card panel" aria-hidden="true">
      <SkeletonLine width={titleWidth} height="1rem" style={{ marginBottom: "0.75rem" }} />
      <SkeletonText lines={lines} />
    </div>
  );
}

/** Scene list row skeleton. */
export function SkeletonSceneRow() {
  return (
    <div className="skeleton-scene-row" aria-hidden="true">
      <SkeletonLine width="40%" height="0.8rem" />
      <SkeletonLine width="70%" height="0.7rem" style={{ marginTop: "0.3rem" }} />
    </div>
  );
}

/** Scene list placeholder (N rows). */
export function SkeletonSceneList({ rows = 5 }) {
  return (
    <div className="skeleton-scene-list" aria-hidden="true">
      {Array.from({ length: rows }, (_, i) => (
        <SkeletonSceneRow key={i} />
      ))}
    </div>
  );
}

/** Asset gallery grid skeleton. */
export function SkeletonAssetGrid({ items = 4 }) {
  return (
    <div className="skeleton-asset-grid" aria-hidden="true">
      {Array.from({ length: items }, (_, i) => (
        <div key={i} className="skeleton-asset-thumb" aria-hidden="true" />
      ))}
    </div>
  );
}

/** Media preview canvas placeholder. */
export function SkeletonMediaCanvas() {
  return (
    <div className="skeleton-media-canvas" aria-hidden="true">
      <div className="skeleton-media-canvas__inner" />
    </div>
  );
}
