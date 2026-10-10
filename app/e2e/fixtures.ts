import { test as base } from "@playwright/test";

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
