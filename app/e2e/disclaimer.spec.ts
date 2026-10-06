import { expect, test } from "@playwright/test";

test("first launch shows the disclaimer and remembers acceptance", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("dialog", { name: "Before you start" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Import" })).toHaveCount(0);
  const continueButton = page.getByRole("button", { name: "Continue" });
  await expect(continueButton).toBeDisabled();
  await page.getByRole("checkbox").check();
  await continueButton.click();
  await expect(page.getByRole("tab", { name: "Import" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("tab", { name: "Import" })).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
});
