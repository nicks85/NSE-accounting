import { expect, importFiles, test } from "./fixtures";

// Synthetic Zerodha tradebooks (fake ISIN).
const HEADER = "symbol,isin,trade_date,exchange,segment,series,trade_type,auction,quantity,price,trade_id,order_id,order_execution_time";
const file = (name: string, rows: string[]) => ({ name, mimeType: "text/csv", buffer: Buffer.from([HEADER, ...rows].join("\n") + "\n") });
// FY 2025-26: bought 100 @1,000 and sold @1,100 → STCG 10,000 x 20% = 2,000.
const THIS_YEAR = file("fy2025.csv", [
  "SYNTHA,INE000A01011,2025-05-01,NSE,EQ,EQ,buy,false,100,1000,1,11,2025-05-01T09:30:00",
  "SYNTHA,INE000A01011,2025-07-01,NSE,EQ,EQ,sell,false,100,1100,2,12,2025-07-01T10:00:00"]);
// Found later: 100 bought in 2023 @500. FIFO now sells those first → LTCG 60,000, under the
// ₹1.25 lakh exemption → tax 0.
const OLDER = file("fy2022.csv", ["SYNTHA,INE000A01011,2023-01-02,NSE,EQ,EQ,buy,false,100,500,3,13,2023-01-02T09:30:00"]);

test("a filed year warns when a later import changes its figures", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText(/runs on this computer/)).toBeVisible({ timeout: 30_000 });
  await page.getByLabel(/Choose tradebook/).setInputFiles(THIS_YEAR);
  await importFiles(page);
  await expect(page.getByText(/Imported 2 new trades/)).toBeVisible();

  await page.getByRole("tab", { name: "Export" }).click();
  await page.getByRole("button", { name: "Mark FY 2025-26 as filed" }).click();
  await expect(page.getByText(/The figures still match what you filed/)).toBeVisible();

  await page.getByRole("tab", { name: "Import" }).click();
  await page.getByLabel(/Choose tradebook/).setInputFiles(OLDER);
  await importFiles(page);
  await expect(page.getByText(/Imported 1 new trade/)).toBeVisible();

  await page.getByRole("tab", { name: "Gains" }).click();
  const banner = page.getByLabel("Changed since filing");
  await expect(banner).toBeVisible();
  await expect(banner.getByRole("row", { name: /Tax at special rates/ })).toContainText("₹2,000.00");
  await expect(banner.getByRole("row", { name: /Tax at special rates/ })).toContainText("₹0.00");

  // It survives closing Kosh.
  await page.reload();
  await page.getByRole("tab", { name: "Gains" }).click();
  await expect(page.getByLabel("Changed since filing")).toBeVisible({ timeout: 30_000 });
});

test("a loss from a return filed late is not set off", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText(/runs on this computer/)).toBeVisible({ timeout: 30_000 });
  await page.getByLabel(/Choose tradebook/).setInputFiles(THIS_YEAR);
  await importFiles(page);
  await expect(page.getByText(/Imported 2 new trades/)).toBeVisible();

  await page.getByRole("tab", { name: "Losses" }).click();
  await page.getByLabel("Year it arose").selectOption({ label: "FY 2024-25" });
  await page.getByLabel("Amount (₹)").fill("4000");
  await page.getByRole("button", { name: "Add loss" }).click();
  await page.getByRole("tab", { name: "Gains" }).click();
  // STCG 10,000 - 4,000 = 6,000 x 20% = 1,200.
  await expect(page.locator("tr.total")).toContainText("₹1,200.00");

  await page.getByRole("tab", { name: "Losses" }).click();
  await page.getByLabel(/Return for FY 2024-25/).selectOption("no");
  await page.getByRole("tab", { name: "Gains" }).click();
  await expect(page.locator("tr.total")).toContainText("₹2,000.00");
  await expect(page.getByText(/LOSS_LAPSED Q-011/)).toBeVisible();
});
