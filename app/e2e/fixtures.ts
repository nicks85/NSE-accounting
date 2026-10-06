import { test as base } from "@playwright/test";

/** Tests start with the first-launch disclaimer already accepted (disclaimer.spec.ts covers it). */
export const test = base.extend({
  page: async ({ page }, use) => {
    await page.addInitScript(() => localStorage.setItem("kosh.disclaimer.accepted", "1"));
    await use(page);
  },
});
export { expect } from "@playwright/test";
