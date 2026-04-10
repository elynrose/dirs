import { test, expect } from "@playwright/test";

/**
 * Chat Studio page shell (no API / agent run required).
 * Run with: `npm run test:e2e` from apps/web (see playwright.config).
 */
test.describe("Chat Studio", () => {
  test("Chat rail opens hands-off chat layout", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("director-app-root")).toBeVisible({ timeout: 60_000 });
    await page.getByRole("button", { name: "Chat" }).click();
    await expect(page.getByTestId("chat-studio-root")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Hands-off chat" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Project setup" })).toBeVisible();
    await expect(page.getByTestId("chat-studio-setup-input")).toBeVisible();
    await expect(page.getByRole("button", { name: "Generate" })).toBeVisible();
  });
});
