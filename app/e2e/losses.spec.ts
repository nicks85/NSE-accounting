import { expect, importFiles, test } from "./fixtures";

const HEADER = "symbol,isin,trade_date,exchange,segment,series,trade_type,auction,quantity,price,trade_id,order_id,order_execution_time";
const csv = [HEADER,
  "SYNTHA,INE000A01011,2024-03-01,NSE,EQ,EQ,buy,false,100,1000,1,11,2024-03-01T09:30:00",
  "SYNTHA,INE000A01011,2025-08-01,NSE,EQ,EQ,sell,false,40,1100,2,12,2025-08-01T10:00:00",
  "SYNTHC,INE000C01013,2025-04-10,NSE,EQ,EQ,buy,false,100,1000,3,13,2025-04-10T09:30:00",
  "SYNTHC,INE000C01013,2025-12-20,NSE,EQ,EQ,sell,false,100,1300,4,14,2025-12-20T10:00:00"].join("\n") + "\n";

test("holdings and brought-forward losses", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText(/runs on this computer/)).toBeVisible({ timeout: 30_000 });
  await page.getByLabel(/Choose tradebook/).setInputFiles({ name: "tb.csv", mimeType: "text/csv", buffer: Buffer.from(csv) });
  await importFiles(page);
  await expect(page.getByText(/Imported 4 new trades/)).toBeVisible();

  await page.getByRole("tab", { name: "Holdings" }).click();
  await expect(page.getByLabel("Open lots")).toContainText("₹60,000.00");

  await page.getByRole("tab", { name: "Losses" }).click();
  await page.getByLabel("Year it arose").selectOption({ label: "FY 2023-24" });
  await page.getByLabel("Amount (₹)").fill("10,000");
  await page.getByRole("button", { name: "Add loss" }).click();
  await page.getByRole("tab", { name: "Gains" }).click();
  // STCG 30,000 - b/f STCL 10,000 = 20,000 x 20% = 4,000.
  await expect(page.locator("tr.total")).toContainText("₹4,000.00");
});
