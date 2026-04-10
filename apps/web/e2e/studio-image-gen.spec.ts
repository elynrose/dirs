import { test, expect } from "@playwright/test";

const API = process.env.PLAYWRIGHT_API_URL || "http://127.0.0.1:8000";

type SceneRow = { id: string };
type ChapterRow = { id: string };
type ProjectRow = { id: string };

async function firstProjectChapterScene(): Promise<{ projectId: string; chapterId: string; sceneId: string } | null> {
  const pr = await fetch(`${API}/v1/projects?limit=20`);
  if (!pr.ok) throw new Error(`GET /v1/projects failed: ${pr.status}`);
  const pj = (await pr.json()) as { data?: { projects?: ProjectRow[] } };
  const projects = pj.data?.projects || [];
  for (const p of projects) {
    const cr = await fetch(`${API}/v1/projects/${p.id}/chapters`);
    if (!cr.ok) continue;
    const cj = (await cr.json()) as { data?: { chapters?: ChapterRow[] } };
    const chapters = cj.data?.chapters || [];
    for (const ch of chapters) {
      const sr = await fetch(`${API}/v1/chapters/${ch.id}/scenes`);
      if (!sr.ok) continue;
      const sj = (await sr.json()) as { data?: { scenes?: SceneRow[] } };
      const scenes = sj.data?.scenes || [];
      if (scenes.length > 0) {
        return { projectId: p.id, chapterId: ch.id, sceneId: scenes[0].id };
      }
    }
  }
  return null;
}

test.describe("Studio image generation", () => {
  test("clicks Image and queues generate-image (API + UI)", async ({ page }) => {
    const ids = await firstProjectChapterScene();
    test.skip(!ids, "No project with at least one chapter and one scene — create/open a project in Studio first.");

    await page.addInitScript(
      ([pid, cid, sid]) => {
        localStorage.setItem(
          "director_ui_session",
          JSON.stringify({
            activePage: "editor",
            projectId: pid,
            chapterId: cid,
            expandedScene: sid,
            agentRunId: "",
            timelineVersionId: "",
            mediaJobId: "",
            charactersJobId: "",
          }),
        );
      },
      [ids.projectId, ids.chapterId, ids.sceneId] as const,
    );

    await page.goto("/");
    await page.waitForLoadState("domcontentloaded");

    const imgBtn = page.getByTestId("studio-scene-generate-image");
    await expect(imgBtn).toBeVisible({ timeout: 60_000 });
    await expect(imgBtn).toBeEnabled({ timeout: 60_000 });

    await imgBtn.click();

    const statusLine = page.locator("p").filter({ hasText: /generate-image queued|job cap reached|^Error:/i });
    await expect(statusLine.first()).toBeVisible({ timeout: 45_000 });
    const t = (await statusLine.first().textContent())?.trim() ?? "";
    if (!/generate-image queued/i.test(t)) {
      throw new Error(
        `Image job did not queue. UI: ${t.slice(0, 500)} — if this mentions job cap, restart the API from a fresh repo .env (JOB_CAPS_ENFORCED=false) and ensure only one process listens on :8000.`,
      );
    }
  });
});
