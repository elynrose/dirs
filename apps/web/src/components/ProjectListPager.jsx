/** Previous / next controls for paginated project sidebars. */
export function ProjectListPager({ page, pageCount, canPrev, canNext, onPrev, onNext, className = "" }) {
  if (pageCount <= 1) return null;
  return (
    <div className={`project-list-pager action-row${className ? ` ${className}` : ""}`} role="navigation" aria-label="Project list pages">
      <button type="button" className="secondary" disabled={!canPrev} onClick={onPrev}>
        Previous
      </button>
      <span className="subtle project-list-pager__label" aria-live="polite">
        {page + 1} / {pageCount}
      </span>
      <button type="button" className="secondary" disabled={!canNext} onClick={onNext}>
        Next
      </button>
    </div>
  );
}
