import { expect, importFiles, test } from "./fixtures";

// Synthetic Zerodha tradebook (fake ISIN). Bought 1,000 @500 in 2015, sold @1,500 in Jun 2025.
const HEADER = "symbol,isin,trade_date,exchange,segment,series,trade_type,auction,quantity,price,trade_id,order_id,order_execution_time";
const csv = [HEADER,
  "SYNTHA,INE000A01011,2015-01-01,NSE,EQ,EQ,buy,false,1000,500,1,11,2015-01-01T09:30:00",
  "SYNTHA,INE000A01011,2025-06-01,NSE,EQ,EQ,sell,false,1000,1500,2,12,2025-06-01T10:00:00"].join("\n") + "\n";

test("settings are saved per person and survive closing Kosh", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText(/runs on this computer/)).toBeVisible({ timeout: 30_000 });
  await page.getByLabel(/Choose tradebook/).setInputFiles({ name: "tb.csv", mimeType: "text/csv", buffer: Buffer.from(csv) });
  await importFiles(page);
  await expect(page.getByText(/Imported 2 new trades/)).toBeVisible();

  // With the 31-Jan-2018 price 800: LTCG 7,00,000 - 1,25,000 = 5,75,000 x 12.5% = 71,875 → 71,880.
  await page.getByRole("tab", { name: "Gains" }).click();
  const fmv = page.getByLabel(/31-Jan-2018 price for INE000A01011/);
  await fmv.fill("800");
  await fmv.blur();
  const total = page.locator("tr.total");
  await expect(total).toContainText("₹71,880.00");

  // Reopen: the price is still there.
  await page.reload();
  await page.getByRole("tab", { name: "Gains" }).click();
  await expect(total).toContainText("₹71,880.00", { timeout: 30_000 });
  await expect(page.getByLabel(/31-Jan-2018 price for INE000A01011/)).toHaveValue("800");

  // A second person starts empty and sees none of the first person's data.
  const me = await page.getByLabel("Person", { exact: true }).locator("option:checked").textContent();
  await page.getByRole("button", { name: "Add a person" }).click();
  await page.getByLabel("Name").fill("Second person");
  await page.getByRole("button", { name: "Add" }).click();
  await expect(page.getByLabel("Person", { exact: true })).toHaveValue(/\d+/);
  await expect(page.getByText("Import trades first.")).toBeVisible();

  // Back to the first person: trades and the price return.
  await page.getByLabel("Person", { exact: true }).selectOption({ label: me! });
  await expect(total).toContainText("₹71,880.00");
});
