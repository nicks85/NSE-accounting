import { expect, importFiles, test } from "./fixtures";

// Synthetic Zerodha tradebook (fake ISIN). Bought 1,000 @500 in 2015, sold @1,500 in Jun 2025.
const HEADER = "symbol,isin,trade_date,exchange,segment,series,trade_type,auction,quantity,price,trade_id,order_id,order_execution_time";
const csv = [HEADER,
  "SYNTHA,INE000A01011,2015-01-01,NSE,EQ,EQ,buy,false,1000,500,1,11,2015-01-01T09:30:00",
  "SYNTHA,INE000A01011,2025-06-01,NSE,EQ,EQ,sell,false,1000,1500,2,12,2025-06-01T10:00:00"].join("\n") + "\n";

test("gains: grandfathering input changes the tax, and why? shows the rule", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText(/runs on this computer/)).toBeVisible({ timeout: 30_000 });
  await page.getByLabel(/Choose tradebook/).setInputFiles({ name: "tb.csv", mimeType: "text/csv", buffer: Buffer.from(csv) });
  await importFiles(page);
  await expect(page.getByText(/Imported 2 new trades/)).toBeVisible();
  await page.getByRole("tab", { name: "Gains" }).click();

  // Without the 2018 price: LTCG 10,00,000 - 1,25,000 = 8,75,000 x 12.5% = 1,09,375 → 1,09,380.
  const total = page.locator("tr.total");
  await expect(total).toContainText("₹1,09,380.00");
  const fmv = page.getByLabel(/31-Jan-2018 price for INE000A01011/);
  await fmv.fill("800");
  await fmv.blur();
  // With FMV 800: cost 8,00,000 → LTCG 7,00,000 - 1,25,000 = 5,75,000 x 12.5% = 71,875 → 71,880.
  await expect(total).toContainText("₹71,880.00");

  await page.getByLabel("Capital gains").getByRole("button", { name: "Why?" }).click();
  await expect(page.getByText(/31-Jan-2018 value ₹8,00,000.00/)).toBeVisible();
  await expect(page.getByLabel("Capital gains").getByText("s.55(2)(ac)", { exact: true })).toBeVisible();
});
