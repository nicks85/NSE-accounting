import { expect, test } from "@playwright/test";

test("app shell loads without any external network requests", async ({ page }) => {
  const external: string[] = [];
  page.on("request", (req) => {
    const url = new URL(req.url());
    if (!["localhost", "127.0.0.1"].includes(url.hostname)) external.push(req.url());
  });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Kosh" })).toBeVisible();
  expect(external).toEqual([]);
});
