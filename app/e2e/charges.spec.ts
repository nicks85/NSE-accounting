import { expect, importFiles, test } from "./fixtures";

// Synthetic Angel One trade history: brokerage 1 and GST 0.18 on the buy; brokerage 1 and STT
// 1.20 on the sale. Total Trade Charges = 3.38.
const header = ["Scrip/Contract", "Buy/Sell", "Buy Price", "Sell Price", "Quantity", "Brokerage", "GST", "STT",
  "Sebi Tax", "Exchange Turnover Charges", "Stamp Duty", "Other Charges", "IPFT Charges", "Order Type", "Segment",
  "Exchange", "Order ID", "Trade ID", "Date"];
const file = { name: "angel.csv", mimeType: "text/csv", buffer: Buffer.from([
  "ClientCode,SYNTH01", "", "Charges Summary", "Total Trades,2", "Total Trade Charges,3.38", "Total Non Trade Charges,0",
  "", "TradeBook And Charges", header.join(","),
  "SYNTHETIC ALPHA LTD,Buy,100,,10,1,0.18,0,0,0,0,0,0,Delivery,CAPITAL,NSE,1001,501,2025-05-02",
  "SYNTHETIC ALPHA LTD,Sell,,120,10,1,0,1.2,0,0,0,0,0,Delivery,CAPITAL,NSE,1002,502,2025-09-01",
].join("\n") + "\n") };

test("a gain's why? shows the trades' charges by type", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText(/runs on this computer/)).toBeVisible({ timeout: 30_000 });
  await page.getByLabel("Angel One trade history").check();
  await page.getByLabel(/Choose tradebook/).setInputFiles(file);
  await importFiles(page);
  await expect(page.getByText(/Imported 2 new trades/)).toBeVisible();
  await page.getByRole("tab", { name: "Gains" }).click();
  await page.getByLabel("Capital gains").getByRole("button", { name: "Why?" }).click();
  const charges = page.getByRole("list", { name: "Charges" });
  await expect(charges).toContainText("Charges on the purchase (10 on 2025-05-02): brokerage ₹1.00, GST ₹0.18.");
  await expect(charges).toContainText("Charges on the sale (10 on 2025-09-01): brokerage ₹1.00; STT ₹1.20.");
});
