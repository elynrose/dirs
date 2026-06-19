import { test, expect } from "@playwright/test";

/** Panel error boundary copy — if visible, the active page crashed at render time. */
const PANEL_ERROR = "This page hit an error";

async function openRailTab(page: import("@playwright/test").Page, label: string) {
  await page.getByRole("button", { name: label, exact: true }).click();
}

async function expectNoPanelError(page: import("@playwright/test").Page) {
  await expect(page.getByRole("heading", { name: PANEL_ERROR })).toHaveCount(0);
}

test.describe("Studio pages", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("director-app-root")).toBeVisible({ timeout: 60_000 });
  });

  test("Editor tab renders workspace without panel error", async ({ page }) => {
    await openRailTab(page, "Editor");
    await expectNoPanelError(page);
    await expect(page.locator(".workspace-grid")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole("heading", { name: "Project & story" })).toBeVisible();
  });

  test("Research & scripts tab renders without panel error", async ({ page }) => {
    await openRailTab(page, "Research & scripts");
    await expectNoPanelError(page);
    await expect(page.getByRole("heading", { name: /Research dossier/i })).toBeVisible();
  });

  test("Characters tab renders without panel error", async ({ page }) => {
    await openRailTab(page, "Characters");
    await expectNoPanelError(page);
    await expect(page.getByRole("heading", { name: "Characters" })).toBeVisible();
  });

  test("Settings tab renders without panel error", async ({ page }) => {
    await openRailTab(page, "Settings");
    await expectNoPanelError(page);
    await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
  });

  test("Usage tab renders without panel error", async ({ page }) => {
    await openRailTab(page, "Usage");
    await expectNoPanelError(page);
    await expect(page.getByRole("heading", { name: "Usage" })).toBeVisible();
  });

  test("Prompts tab renders without panel error", async ({ page }) => {
    await openRailTab(page, "Prompts");
    await expectNoPanelError(page);
    await expect(page.getByRole("heading", { name: "Prompts" })).toBeVisible();
  });
});
