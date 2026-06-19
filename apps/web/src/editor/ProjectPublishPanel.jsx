import { useProjectPublish } from "../hooks/editor/useProjectPublish.js";
import { PublishCoverTabContent } from "./publish/PublishCoverTabContent.jsx";
import { PublishHookTabContent } from "./publish/PublishHookTabContent.jsx";
import { PublishOutroTabContent } from "./publish/PublishOutroTabContent.jsx";

export { PublishCoverTabContent, PublishHookTabContent, PublishOutroTabContent, useProjectPublish };

/**
 * Thumbnail, YouTube copy, opening hook, and optional outro settings for a project.
 */
export function ProjectPublishPanel({ projectId, busy, setBusy, setError, setMessage, idem, onScenesReload }) {
  const pub = useProjectPublish({ projectId, busy, setBusy, setError, setMessage, idem, onScenesReload });

  if (!projectId) {
    return (
      <p className="subtle" style={{ margin: 0 }}>
        Open a project to edit thumbnail, YouTube copy, and the opening hook.
      </p>
    );
  }

  return (
    <div className="publish-pack-panel">
      <PublishCoverTabContent pub={pub} projectId={projectId} busy={busy} />
      <hr style={{ margin: "16px 0", border: "none", borderTop: "1px solid rgb(255 255 255 / 12%)" }} />
      <PublishHookTabContent pub={pub} projectId={projectId} busy={busy} />
      <hr style={{ margin: "16px 0", border: "none", borderTop: "1px solid rgb(255 255 255 / 12%)" }} />
      <PublishOutroTabContent pub={pub} projectId={projectId} busy={busy} />
    </div>
  );
}
