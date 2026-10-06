import { expect, test } from "./fixtures";

// Synthetic Zerodha tradebook (fake ISIN); layout per importers/zerodha.py (unconfirmed, Q-015).
const HEADER = "symbol,isin,trade_date,exchange,segment,series,trade_type,auction,quantity,price,trade_id,order_id,order_execution_time";
const ROWS = [
  "SYNTHA,INE000A01011,2025-05-01,NSE,EQ,EQ,buy,false,100,1000,1,11,2025-05-01T09:30:00",
  "SYNTHA,INE000A01011,2025-10-01,NSE,EQ,EQ,sell,false,100,1200,2,12,2025-10-01T10:00:00",
];
const tradebook = { name: "tradebook.csv", mimeType: "text/csv", buffer: Buffer.from([HEADER, ...ROWS].join("\n") + "\n") };

test("import a tradebook through the real engine", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText(/runs on this computer/)).toBeVisible({ timeout: 30_000 });
  await page.getByLabel(/Choose tradebook/).setInputFiles(tradebook);
  await page.getByRole("button", { name: "Import files" }).click();
  await expect(page.getByText("Imported 2 trades from Zerodha tradebook (CSV)")).toBeVisible();
  await expect(page.getByText(/isn't confirmed against real files/)).toBeVisible();
  await expect(page.getByText("2 trades — 2 shares")).toBeVisible();

  await page.getByLabel(/Choose tradebook/).setInputFiles(tradebook);
  await page.getByRole("button", { name: "Import files" }).click();
  await expect(page.getByText("2 were already loaded and were skipped.")).toBeVisible();
  await expect(page.getByLabel("Loaded data").getByRole("listitem")).toHaveCount(1);
});
