import { expect, test } from "./fixtures";

// Synthetic Angel One trade history (names invented, no ISIN). Charges on the rows: 1 + 1 + 1.2
// STT = 3.2, the same as the file's "Total Trade Charges".
const header = ["Scrip/Contract", "Buy/Sell", "Buy Price", "Sell Price", "Quantity", "Brokerage", "GST", "STT",
  "Sebi Tax", "Exchange Turnover Charges", "Stamp Duty", "Other Charges", "IPFT Charges", "Order Type", "Segment",
  "Exchange", "Order ID", "Trade ID", "Date"];
const file = (total: string) => ({ name: `angel-${total}.csv`, mimeType: "text/csv", buffer: Buffer.from([
  "ClientCode,SYNTH01", "", "Charges Summary", "Total Trades,2", `Total Trade Charges,${total}`, "Total Non Trade Charges,0",
  "", "TradeBook And Charges", header.join(","),
  "SYNTHETIC ALPHA LTD,Buy,100,,10,1,0,0,0,0,0,0,0,Delivery,CAPITAL,NSE,1001,501,2025-05-02",
  "SYNTHETIC ALPHA LTD,Sell,,120,10,1,0,1.2,0,0,0,0,0,Delivery,CAPITAL,NSE,1002,502,2025-09-01",
].join("\n") + "\n") });

test("preview an import, cancel it, then save it; a second time there is nothing to save", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText(/runs on this computer/)).toBeVisible({ timeout: 30_000 });
  await page.getByLabel("Angel One trade history").check();
  await page.getByLabel(/Choose tradebook/).setInputFiles(file("3.2"));
  await page.getByRole("button", { name: "Preview import" }).click();
  const panel = page.getByRole("region", { name: "Import preview" });
  await expect(panel).toContainText("2 trades (1 buy, 1 sell) from 2025-05-02 to 2025-09-01, into the demat account Angel One");
  await expect(panel).toContainText("That matches the file’s own total of ₹3.20");
  await expect(panel).toContainText("1 company without an ISIN, imported by name: SYNTHETIC ALPHA LTD");

  await panel.getByRole("button", { name: "Cancel" }).click();
  await expect(page.getByText("Nothing imported yet.")).toBeVisible();  // nothing was stored

  await page.getByRole("button", { name: "Preview import" }).click();
  await panel.getByRole("button", { name: "Save this file" }).click();
  await expect(page.getByText(/Imported 2 new trades from Angel One/)).toBeVisible();
  await expect(page.getByRole("table", { name: "Import history" })).toContainText("angel-3.2.csv");

  await page.getByLabel(/Choose tradebook/).setInputFiles(file("3.2"));
  await page.getByRole("button", { name: "Preview import" }).click();
  await expect(panel).toContainText("already imported on");
  await expect(panel.getByRole("button", { name: "Nothing to save" })).toBeDisabled();
});

test("charges that don't match the file's own total are flagged before saving", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText(/runs on this computer/)).toBeVisible({ timeout: 30_000 });
  await page.getByLabel("Angel One trade history").check();
  await page.getByLabel(/Choose tradebook/).setInputFiles(file("9.9"));
  await page.getByRole("button", { name: "Preview import" }).click();
  await expect(page.getByRole("region", { name: "Import preview" }))
    .toContainText("The file’s own total is ₹9.90: check the file is complete");
});
