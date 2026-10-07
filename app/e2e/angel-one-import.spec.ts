import { expect, test } from "./fixtures";

// Synthetic Angel One trade history (CSV) in the real export's layout: summary block, then the
// "TradeBook And Charges" table. Names are invented; the file has no ISIN column.
const header = ["Scrip/Contract", "Buy/Sell", "Buy Price", "Sell Price", "Quantity", "Brokerage", "GST", "STT",
  "Sebi Tax", "Exchange Turnover Charges", "Stamp Duty", "Other Charges", "IPFT Charges", "Order Type", "Segment",
  "Exchange", "Order ID", "Trade ID", "Date"];
const csv = [
  "ClientCode,SYNTH01", "", "Charges Summary", "Total Trades,2", "Total Trade Charges,3.2", "Total Non Trade Charges,0",
  "", "TradeBook And Charges", header.join(","),
  "SYNTHETIC ALPHA LTD,Buy,100,,10,1,0,0,0,0,0,0,0,Delivery,CAPITAL,NSE,1001,501,2025-05-02",
  "SYNTHETIC ALPHA LTD,Sell,,120,10,1,0,1.2,0,0,0,0,0,Delivery,CAPITAL,NSE,1002,502,2025-09-01",
].join("\n") + "\n";

test("Angel One import works in one step, by company name, with charges", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText(/runs on this computer/)).toBeVisible({ timeout: 30_000 });
  await page.getByLabel("Angel One trade history").check();
  await page.getByLabel(/Choose tradebook/).setInputFiles({ name: "Trades_History.csv", mimeType: "text/csv", buffer: Buffer.from(csv) });
  await page.getByRole("button", { name: "Import files" }).click();
  await expect(page.getByText("Imported 2 trades from Angel One trade history")).toBeVisible();

  await page.getByRole("tab", { name: "Gains" }).click();
  await expect(page.getByText("SYNTHETIC ALPHA LTD").first()).toBeVisible();
  // STCG = 10 × 120 − 1 − (10 × 100 + 1) = 198 (STT not deducted); 20% = 39.60 → ₹40.
  await expect(page.locator("tr.total")).toContainText("₹40.00");
});
