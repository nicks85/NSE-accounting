import { expect, test } from "./fixtures";

// Synthetic Zerodha tradebook (fake ISINs). SYNTHA: 100 sold in Jun 2025 with no purchase in the file.
// SYNTHB: bought 10 @50 and sold @60 in FY 2025-26 → STCG 100.
const HEADER = "symbol,isin,trade_date,exchange,segment,series,trade_type,auction,quantity,price,trade_id,order_id,order_execution_time";
const csv = [HEADER,
  "SYNTHB,INE000B01012,2025-05-01,NSE,EQ,EQ,buy,false,10,50,1,11,2025-05-01T09:30:00",
  "SYNTHA,INE000A01011,2025-06-10,NSE,EQ,EQ,sell,false,100,3000,2,12,2025-06-10T10:00:00",
  "SYNTHB,INE000B01012,2025-07-01,NSE,EQ,EQ,sell,false,10,60,3,13,2025-07-01T10:00:00"].join("\n") + "\n";

test("missing purchase history: total and export withheld until the purchase is added or the sale excluded", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText(/runs on this computer/)).toBeVisible({ timeout: 30_000 });
  await page.getByLabel(/Choose tradebook/).setInputFiles({ name: "tb.csv", mimeType: "text/csv", buffer: Buffer.from(csv) });
  await page.getByRole("button", { name: "Import files" }).click();
  await expect(page.getByText(/Imported 3 trades/)).toBeVisible();
  await page.getByRole("tab", { name: "Gains" }).click();

  const total = page.locator("tr.total");
  await expect(total).toContainText("Incomplete: 1 sale(s) missing purchase history");
  const gaps = page.getByLabel("Missing purchase history");
  await expect(gaps).toContainText("sold 100 on 2025-06-10");

  await page.getByRole("tab", { name: "Export" }).click();
  await expect(page.getByText(/Export is off: 1 sale\(s\)/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Download ITR schedules" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Download PDF summary" })).toBeDisabled();

  // Exclude: the year completes, labelled. Only the SYNTHB STCG of 100 is taxed: 100 x 20% = 20 → 20.
  await page.getByRole("tab", { name: "Gains" }).click();
  await gaps.getByRole("button", { name: "Exclude this sale" }).click();
  await expect(total).toContainText("₹20.00");
  await expect(page.getByText(/Excludes 1 sale\(s\) \(₹3,00,000.00 sale value\)/)).toBeVisible();
  await gaps.getByRole("button", { name: "Include again" }).click();
  await expect(total).toContainText("Incomplete");

  // Add the purchase: 100 bought 2023-01-02 @1,000 → LTCG 2,00,000 - 1,25,000 = 75,000 x 12.5% = 9,375,
  // plus STCG 20 → 9,395 → rounded to ₹10: 9,400.
  await gaps.getByRole("button", { name: "Add the purchase" }).click();
  await gaps.getByLabel("Purchase date").fill("2023-01-02");
  await gaps.getByLabel("Price per share (₹)").fill("1000");
  await gaps.getByRole("button", { name: "Add purchase" }).click();
  await expect(total).toContainText("₹9,400.00");
  await expect(page.getByText(/MANUAL_PURCHASE Q-029/)).toBeVisible();

  await page.getByRole("tab", { name: "Export" }).click();
  await expect(page.getByRole("button", { name: "Download PDF summary" })).toBeEnabled();
  await expect(page.getByText(/Export is off/)).toHaveCount(0);
});
