import { test, expect } from "@playwright/test";

test.describe("Settings API keys accordions", () => {
  test("integration section titles are in the DOM and visible color", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("director-app-root")).toBeVisible({ timeout: 60_000 });
    await page.getByRole("button", { name: "Settings" }).click();
    await page.getByRole("button", { name: "API keys" }).click();

    const headings = page.locator(
      ".settings-tab-panel details.settings-section summary .settings-section-heading",
    );
    const n = await headings.count();
    expect(n).toBeGreaterThan(8);

    const first = headings.first();
    await expect(first).toHaveText(/Telegram bot/i, { timeout: 15_000 });

    const color = await first.evaluate((el) => getComputedStyle(el).color);
    const opacity = await first.evaluate((el) => getComputedStyle(el).opacity);
    expect(color).not.toBe("rgba(0, 0, 0, 0)");
    expect(parseFloat(opacity)).toBeGreaterThan(0.5);

    const box = await first.boundingBox();
    expect(box?.width ?? 0).toBeGreaterThan(40);
    expect(box?.height ?? 0).toBeGreaterThan(8);

    for (let i = 0; i < n; i++) {
      const row = headings.nth(i);
      const t = (await row.innerText()).trim();
      const h = await row.boundingBox();
      expect(t.length, `row ${i} should have a visible title`).toBeGreaterThan(2);
      expect(h?.height ?? 0, `row ${i} "${t.slice(0, 40)}"`).toBeGreaterThan(14);
    }

  });
});
