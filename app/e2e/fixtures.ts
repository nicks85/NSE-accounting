import { test as base, type Page } from "@playwright/test";

/** Tests start with the first-launch disclaimer already accepted (disclaimer.spec.ts covers it),
 *  and each test uses its own ledger profile so saved trades never leak between tests. */
export const test = base.extend({
  page: async ({ page }, use, testInfo) => {
    const profile = `e2e-${testInfo.testId}-${testInfo.retry}-${Date.now()}`;
    await page.addInitScript((name) => {
      localStorage.setItem("kosh.disclaimer.accepted", "1");
      localStorage.setItem("kosh.profile", name);
    }, profile);
    await use(page);
  },
});
export { expect } from "@playwright/test";

/** Brief 0004: an import is a preview, then "Save" when the preview has something to save.
 *  Errors (the file can't be read) leave no preview, and nothing is clicked after them. */
export async function importFiles(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Preview import" }).click();
  const panel = page.getByRole("region", { name: "Import preview" });
  await panel.or(page.getByRole("alert").first()).first().waitFor();
  if (!(await panel.isVisible())) return;
  const save = panel.getByRole("button", { name: /^Save / });
  if ((await save.count()) > 0 && (await save.isEnabled())) await save.click();
}
