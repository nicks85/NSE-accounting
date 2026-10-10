import { expect, importFiles, test } from "./fixtures";

// Synthetic: 100 bought @1,000 on 1-May-24, sold @2,600 on 1-Jul-25. LTCG 1,60,000 - 1,25,000 =
// 35,000 x 12.5% = 4,375 → 4,380, whatever the residential status.
const HEADER = "symbol,isin,trade_date,exchange,segment,series,trade_type,auction,quantity,price,trade_id,order_id,order_execution_time";
const tradebook = { name: "tb.csv", mimeType: "text/csv", buffer: Buffer.from([HEADER,
  "SYNTHA,INE000A01011,2024-05-01,NSE,EQ,EQ,buy,false,100,1000,1,11,2024-05-01T09:30:00",
  "SYNTHA,INE000A01011,2025-07-01,NSE,EQ,EQ,sell,false,100,2600,2,12,2025-07-01T10:00:00"].join("\n") + "\n") };

test("residential status per year: notes change, the figure doesn't, and it's saved", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText(/runs on this computer/)).toBeVisible({ timeout: 30_000 });
  await page.getByLabel(/Choose tradebook/).setInputFiles(tradebook);
  await importFiles(page);
  await page.getByRole("tab", { name: "Gains" }).click();
  const total = page.locator("tr.total");
  await expect(total).toContainText("₹4,380.00");
  await expect(page.getByText(/your actual tax may be lower/)).toBeVisible();

  await page.getByLabel("Residential status for this year").selectOption("NRI");
  await expect(page.getByText(/Non-resident this year/)).toBeVisible();
  await expect(total).toContainText("₹4,380.00");

  await page.reload();
  await page.getByRole("tab", { name: "Gains" }).click();
  await expect(page.getByLabel("Residential status for this year")).toHaveValue("NRI", { timeout: 30_000 });
  await page.getByRole("tab", { name: "Export" }).click();
  await expect(page.getByText(/choose the residential status/)).toContainText("Non-resident");
});
