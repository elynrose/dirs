import { test, expect } from "@playwright/test";

/**
 * Fast smoke: Studio shell renders (Vite + React). No seeded project or API required.
 * Start stack: `scripts/start-director.ps1` or rely on playwright.config webServer (Vite only).
 */
test.describe("Studio smoke", () => {
  test("shell loads and shows Director Studio", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveTitle(/Director/i);
    await expect(page.getByTestId("director-app-root")).toBeVisible({ timeout: 60_000 });
    await expect(page.getByRole("heading", { name: "Director Studio" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Editor" })).toBeVisible();
  });
});
