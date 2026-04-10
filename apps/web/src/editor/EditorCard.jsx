import { useEffect, useMemo, useState } from "react";
import { useEditorLayout } from "./EditorLayoutContext.jsx";
import { InfoTip } from "../components/InfoTip.jsx";

const COLUMN_ACTIVE_TAB_KEY = "director_editor_column_active_v1";
const CENTER_SCENE_TAB_STORAGE_KEY = "director_editor_center_scene_tab_v1";

/**
 * Collapsible card with drag handle to reorder within the same column (HTML5 DnD).
 * `headerMode="minimal"` — body only (optional info row); used with side tab rails (no reorder drag).
 */
export function EditorCard({ column, id, title, info, children, headerMode = "full", accordionPeerIds = null }) {
  const { moveInColumn, appendToColumnEnd, toggleCollapsed, toggleCollapsedAccordion, isCollapsed } =
    useEditorLayout();
  const collapsed = headerMode === "minimal" ? false : isCollapsed(column, id);
  const reorderable = headerMode === "full";

  const onDragStartGrip = (e) => {
    e.stopPropagation();
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("application/director-editor-card", JSON.stringify({ column, id }));
  };

  const onDragOverCard = (e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
  };

  const onDropCard = (e) => {
    e.preventDefault();
    let raw = e.dataTransfer.getData("application/director-editor-card");
    if (!raw) return;
    try {
      const payload = JSON.parse(raw);
      if (payload.column !== column || !payload.id) return;
      moveInColumn(column, payload.id, id);
    } catch {
      /* ignore */
    }
  };

  const onDropTail = (e) => {
    e.preventDefault();
    let raw = e.dataTransfer.getData("application/director-editor-card");
    if (!raw) return;
    try {
      const payload = JSON.parse(raw);
      if (payload.column !== column || !payload.id) return;
      appendToColumnEnd(column, payload.id);
    } catch {
      /* ignore */
    }
  };

  return (
    <div
      className="editor-card"
      data-editor-card={id}
      onDragOver={reorderable ? onDragOverCard : undefined}
      onDrop={reorderable ? onDropCard : undefined}
    >
      {headerMode === "full" ? (
        <div className="editor-card-header">
          <span
            className="editor-card-grip"
            draggable
            onDragStart={onDragStartGrip}
            title="Drag to reorder"
            aria-hidden
          >
            ⠿
          </span>
          <button
            type="button"
            className="editor-card-title-btn"
            onClick={() =>
              accordionPeerIds?.length
                ? toggleCollapsedAccordion(column, id, accordionPeerIds)
                : toggleCollapsed(column, id)
            }
          >
            <span className="editor-card-chevron" aria-hidden>
              {collapsed ? "▸" : "▾"}
            </span>
            {title}
          </button>
          {info ? <InfoTip>{info}</InfoTip> : null}
        </div>
      ) : info ? (
        <div className="editor-card-header editor-card-header--minimal">
          <InfoTip>{info}</InfoTip>
        </div>
      ) : null}
      {!collapsed ? <div className="editor-card-body">{children}</div> : null}
      {reorderable ? (
        <div
          className="editor-card-drop-tail"
          onDragOver={onDragOverCard}
          onDrop={onDropTail}
          title="Drop to move to end of column"
          aria-hidden
        />
      ) : null}
    </div>
  );
}

/**
 * Renders visible cards in persisted order for one column.
 * `sections` is an array of { id, title, show?, children } (show defaults true).
 * `topRowIds`: optional ids rendered side-by-side in a row above the rest (e.g. preview + scenes).
 * `splitPreviewRow`: optional { leftIds, rightIds } — first row is ~65% left / ~35% right (center column).
 *   When set, `topRowIds` is ignored for those ids.
 * `accordionExclusiveIds`: optional ids that share one open panel (only one expanded at a time).
 * `sceneTabIds`: when set (center column), these sections render as horizontal tabs instead of an accordion stack.
 */
export function EditorCardColumn({
  column,
  sections,
  topRowIds,
  splitPreviewRow,
  accordionExclusiveIds = null,
  sceneTabIds = null,
}) {
  const { getOrderedIds, moveInColumn, appendToColumnEnd } = useEditorLayout();
  const visible = sections.filter((s) => s.show !== false);
  const visibleIds = visible.map((s) => s.id);
  const ordered = getOrderedIds(column, visibleIds);
  const byId = Object.fromEntries(visible.map((s) => [s.id, s]));

  const leftSplitSet = splitPreviewRow?.leftIds?.length ? new Set(splitPreviewRow.leftIds) : null;
  const rightSplitSet = splitPreviewRow?.rightIds?.length ? new Set(splitPreviewRow.rightIds) : null;
  const splitActive = Boolean(leftSplitSet && rightSplitSet);
  const splitAllSet =
    splitActive && leftSplitSet && rightSplitSet
      ? new Set([...leftSplitSet, ...rightSplitSet])
      : null;

  const topIdSet =
    splitActive || !topRowIds?.length ? null : new Set(topRowIds);

  const sideTabsMode =
    (column === "left" || column === "right") && !splitActive && !topIdSet;

  const [activeId, setActiveId] = useState(null);
  const orderedKey = ordered.join(",");

  const effectiveActive = useMemo(() => {
    if (!sideTabsMode || ordered.length === 0) return null;
    if (activeId && ordered.includes(activeId)) return activeId;
    try {
      const stored = localStorage.getItem(`${COLUMN_ACTIVE_TAB_KEY}:${column}`);
      if (stored && ordered.includes(stored)) return stored;
    } catch {
      /* ignore */
    }
    return ordered[0];
  }, [sideTabsMode, orderedKey, column, activeId]);

  useEffect(() => {
    if (!sideTabsMode || !effectiveActive) return;
    try {
      localStorage.setItem(`${COLUMN_ACTIVE_TAB_KEY}:${column}`, effectiveActive);
    } catch {
      /* ignore */
    }
  }, [sideTabsMode, column, effectiveActive]);
  const topOrdered = topIdSet ? ordered.filter((id) => topIdSet.has(id)) : [];
  const leftSplitOrdered =
    splitActive && leftSplitSet ? ordered.filter((id) => leftSplitSet.has(id)) : [];
  const rightSplitOrdered =
    splitActive && rightSplitSet ? ordered.filter((id) => rightSplitSet.has(id)) : [];

  const mainOrdered = splitAllSet
    ? ordered.filter((id) => !splitAllSet.has(id))
    : topIdSet
      ? ordered.filter((id) => !topIdSet.has(id))
      : ordered;

  const sceneTabIdSet = sceneTabIds?.length ? new Set(sceneTabIds) : null;
  const sceneTabsOrdered =
    sceneTabIdSet && column === "center" ? mainOrdered.filter((id) => sceneTabIdSet.has(id)) : [];
  const mainStackOrdered =
    sceneTabIdSet && column === "center" ? mainOrdered.filter((id) => !sceneTabIdSet.has(id)) : mainOrdered;

  const [sceneTabActive, setSceneTabActive] = useState(null);
  const sceneTabsKey = sceneTabsOrdered.join(",");

  const effectiveSceneTab = useMemo(() => {
    if (!sceneTabsOrdered.length) return null;
    if (sceneTabActive && sceneTabsOrdered.includes(sceneTabActive)) return sceneTabActive;
    try {
      const stored = localStorage.getItem(CENTER_SCENE_TAB_STORAGE_KEY);
      if (stored && sceneTabsOrdered.includes(stored)) return stored;
    } catch {
      /* ignore */
    }
    return sceneTabsOrdered[0];
  }, [sceneTabsKey, sceneTabActive]);

  useEffect(() => {
    if (!sceneTabsOrdered.length || !effectiveSceneTab) return;
    try {
      localStorage.setItem(CENTER_SCENE_TAB_STORAGE_KEY, effectiveSceneTab);
    } catch {
      /* ignore */
    }
  }, [sceneTabsOrdered.length, effectiveSceneTab]);

  const onSceneTabDragStart = (e, id) => {
    e.stopPropagation();
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("application/director-editor-card", JSON.stringify({ column, id }));
  };

  const onSceneTabDragOver = (e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
  };

  const onSceneTabDropBefore = (e, dropBeforeId) => {
    e.preventDefault();
    let raw = e.dataTransfer.getData("application/director-editor-card");
    if (!raw) return;
    try {
      const payload = JSON.parse(raw);
      if (payload.column !== column || !payload.id) return;
      moveInColumn(column, payload.id, dropBeforeId);
    } catch {
      /* ignore */
    }
  };

  const onSceneTabDropTail = (e) => {
    e.preventDefault();
    let raw = e.dataTransfer.getData("application/director-editor-card");
    if (!raw) return;
    try {
      const payload = JSON.parse(raw);
      if (payload.column !== column || !payload.id) return;
      appendToColumnEnd(column, payload.id);
    } catch {
      /* ignore */
    }
  };

  const accordionPeers =
    sceneTabIds?.length && column === "center"
      ? null
      : accordionExclusiveIds?.length && accordionExclusiveIds.some((x) => ordered.includes(x))
        ? accordionExclusiveIds
        : null;

  const renderCard = (cid) => {
    const s = byId[cid];
    if (!s) return null;
    const peers = accordionPeers && accordionPeers.includes(cid) ? accordionPeers : null;
    return (
      <EditorCard key={cid} column={column} id={cid} title={s.title} info={s.info} accordionPeerIds={peers}>
        {s.children}
      </EditorCard>
    );
  };

  const renderTabCard = (cid) => {
    const s = byId[cid];
    if (!s) return null;
    return (
      <EditorCard key={cid} column={column} id={cid} title={s.title} info={s.info} headerMode="minimal">
        {s.children}
      </EditorCard>
    );
  };

  const renderCenterSceneTabs = () => {
    if (column !== "center" || !sceneTabsOrdered.length) return null;
    const active = effectiveSceneTab;
    const activeSection = active ? byId[active] : null;
    return (
      <div className="editor-scene-tabs" data-editor-scene-tabs>
        <div className="editor-scene-tabs__list-wrap">
          <div className="editor-scene-tabs__list" role="tablist" aria-label="Selected scene panels">
            {sceneTabsOrdered.map((cid) => {
              const s = byId[cid];
              if (!s) return null;
              const isActive = active === cid;
              return (
                <div
                  key={cid}
                  className={`editor-scene-tabs__tab-item${isActive ? " editor-scene-tabs__tab-item--active" : ""}`}
                  onDragOver={onSceneTabDragOver}
                  onDrop={(e) => onSceneTabDropBefore(e, cid)}
                >
                  <span
                    className="editor-card-grip editor-scene-tabs__grip"
                    draggable
                    onDragStart={(e) => onSceneTabDragStart(e, cid)}
                    title="Drag to reorder"
                    aria-hidden
                  >
                    ⠿
                  </span>
                  <button
                    type="button"
                    role="tab"
                    id={`editor-scene-tab-${cid}`}
                    aria-selected={isActive}
                    aria-controls="editor-scene-tab-panel"
                    className="editor-scene-tabs__tab"
                    title={typeof s.title === "string" ? s.title : undefined}
                    aria-label={typeof s.title === "string" ? s.title : undefined}
                    onClick={() => setSceneTabActive(cid)}
                  >
                    <span className="editor-scene-tabs__tab-label">
                      {typeof s.tabShortTitle === "string" && s.tabShortTitle.trim() ? s.tabShortTitle.trim() : s.title}
                    </span>
                  </button>
                </div>
              );
            })}
          </div>
          <div
            className="editor-scene-tabs__drop-tail"
            onDragOver={onSceneTabDragOver}
            onDrop={onSceneTabDropTail}
            title="Drop to move tab to end"
            aria-hidden
          />
        </div>
        {activeSection?.info ? (
          <div className="editor-scene-tabs__info">
            <InfoTip>{activeSection.info}</InfoTip>
          </div>
        ) : null}
        <div
          className="editor-scene-tabs__panel"
          role="tabpanel"
          id="editor-scene-tab-panel"
          aria-labelledby={active ? `editor-scene-tab-${active}` : undefined}
        >
          {activeSection ? activeSection.children : null}
        </div>
      </div>
    );
  };

  const columnRailLabel =
    column === "left" ? "Project and story cards" : column === "right" ? "Pipeline cards" : "Editor cards";

  const renderSideTabsRail = () => (
    <nav className="studio-page-rail studio-page-rail--column" aria-label={columnRailLabel} role="tablist">
      {ordered.map((cid) => {
        const s = byId[cid];
        if (!s) return null;
        return (
          <button
            key={cid}
            type="button"
            role="tab"
            id={`editor-col-tab-${column}-${cid}`}
            aria-selected={effectiveActive === cid}
            aria-controls={`editor-column-panel-${column}`}
            className={`studio-page-rail__tab${effectiveActive === cid ? " studio-page-rail__tab--active" : ""}`}
            onClick={() => setActiveId(cid)}
          >
            <span className="studio-page-rail__tab-label">{s.title}</span>
          </button>
        );
      })}
    </nav>
  );

  if (sideTabsMode) {
    return (
      <div
        className={`editor-card-stack editor-card-stack--side-tabs editor-card-stack--side-tabs-${column === "left" ? "left" : "right"}`}
        data-editor-column={column}
      >
        {column === "right" ? renderSideTabsRail() : null}
        <div
          className="editor-card-stack__side-tabs-pane"
          id={`editor-column-panel-${column}`}
          role="tabpanel"
          aria-labelledby={
            effectiveActive ? `editor-col-tab-${column}-${effectiveActive}` : undefined
          }
        >
          {effectiveActive ? renderTabCard(effectiveActive) : null}
        </div>
        {column === "left" ? renderSideTabsRail() : null}
      </div>
    );
  }

  const renderSplitLead = () => {
    if (!splitActive) return null;
    const hasLeft = leftSplitOrdered.length > 0;
    const hasRight = rightSplitOrdered.length > 0;
    if (!hasLeft && !hasRight) return null;
    if (!hasLeft && hasRight) {
      return (
        <div className="editor-card-row editor-card-split-stack editor-card-split-stack--full">
          {rightSplitOrdered.map((cid) => renderCard(cid))}
        </div>
      );
    }
    return (
      <div className="editor-card-row editor-card-row--preview-split">
        <div className="editor-card-split-col editor-card-split-col--preview">
          {leftSplitOrdered.map((cid) => renderCard(cid))}
        </div>
        <div className="editor-card-split-col editor-card-split-col--side">
          <div className="editor-card-split-stack">{hasRight ? rightSplitOrdered.map((cid) => renderCard(cid)) : null}</div>
        </div>
      </div>
    );
  };

  return (
    <div className="editor-card-stack" data-editor-column={column}>
      {splitActive ? renderSplitLead() : null}
      {!splitActive && topIdSet && topOrdered.length > 0 ? (
        <div className={`editor-card-row${topOrdered.length > 1 ? " editor-card-row--split" : ""}`}>
          {topOrdered.map((cid) => renderCard(cid))}
        </div>
      ) : null}
      {renderCenterSceneTabs()}
      {mainStackOrdered.map((cid) => renderCard(cid))}
    </div>
  );
}
