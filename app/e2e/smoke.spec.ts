import { expect, test } from "@playwright/test";

test("app shell talks to the local engine and makes no external requests", async ({ page }) => {
  const external: string[] = [];
  page.on("request", (req) => {
    const url = new URL(req.url());
    if (!["localhost", "127.0.0.1"].includes(url.hostname)) external.push(req.url());
  });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Kosh" })).toBeVisible();
  await expect(page.getByText(/Engine .* runs on this computer, offline/)).toBeVisible({
    timeout: 30_000,
  });
  for (const tab of ["Holdings", "Gains", "Losses", "Export", "Import"]) {
    await page.getByRole("tab", { name: tab }).click();
    await expect(page.getByRole("heading", { level: 2, name: tab })).toBeVisible();
  }
  expect(external).toEqual([]);
});
