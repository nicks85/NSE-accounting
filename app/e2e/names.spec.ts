import { expect, importFiles, test } from "./fixtures";

// Synthetic Angel One trade history (no ISIN column). The company's ISIN is typed once.
const header = ["Scrip/Contract", "Buy/Sell", "Buy Price", "Sell Price", "Quantity", "Brokerage", "GST", "STT",
  "Sebi Tax", "Exchange Turnover Charges", "Stamp Duty", "Other Charges", "IPFT Charges", "Order Type", "Segment",
  "Exchange", "Order ID", "Trade ID", "Date"];
const file = (name: string, rows: string[]) => ({ name, mimeType: "text/csv", buffer: Buffer.from([
  "ClientCode,SYNTH01", "", "TradeBook And Charges", header.join(","), ...rows].join("\n") + "\n") });
const BUY = "EXAMPLE DEPO SER (I),Buy,100,,10,0,0,0,0,0,0,0,0,Delivery,CAPITAL,NSE,1001,501,2025-05-02";
const SELL = "EXAMPLE DEPO SER (I),Sell,,120,10,0,0,0,0,0,0,0,0,Delivery,CAPITAL,NSE,1002,502,2025-09-01";

test("type a company's ISIN once; saved trades and later files use it", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText(/runs on this computer/)).toBeVisible({ timeout: 30_000 });
  await page.getByLabel("Angel One trade history").check();
  await page.getByLabel(/Choose tradebook/).setInputFiles(file("may.csv", [BUY]));
  await importFiles(page);
  const names = page.getByRole("region", { name: "Companies imported by name" });
  await expect(names).toContainText("EXAMPLE DEPO SER (I)");

  await names.getByLabel("ISIN for EXAMPLE DEPO SER (I)", { exact: true }).fill("INE000A01011");  // wrong check digit
  await names.getByRole("button", { name: "Save the ISIN for EXAMPLE DEPO SER (I)" }).click();
  await expect(names.getByRole("alert")).toContainText("isn't a valid Indian ISIN");
  await names.getByLabel("ISIN for EXAMPLE DEPO SER (I)", { exact: true }).fill("INE000A01012");
  await names.getByRole("button", { name: "Save the ISIN for EXAMPLE DEPO SER (I)" }).click();
  await expect(names).toContainText("EXAMPLE DEPO SER (I) → INE000A01012");

  // A later file from the same broker: the name is matched to the ISIN on import.
  await page.getByLabel(/Choose tradebook/).setInputFiles(file("sep.csv", [BUY, SELL]));
  await page.getByRole("button", { name: "Preview import" }).click();
  const preview = page.getByRole("region", { name: "Import preview" });
  await expect(preview).toContainText("1 new");
  await expect(preview).not.toContainText("without an ISIN");
  await preview.getByRole("button", { name: "Save this file" }).click();

  await page.getByRole("tab", { name: "Gains" }).click();
  const gains = page.getByLabel("Capital gains");
  await expect(gains).toContainText("INE000A01012");
  await expect(gains).toContainText("EXAMPLE DEPO SER (I)");

  // Undo after the later file: both trades go back under the name, the sale included.
  await page.getByRole("tab", { name: "Import" }).click();
  await names.getByRole("button", { name: "Undo the ISIN for EXAMPLE DEPO SER (I)" }).click();
  await expect(names.getByRole("table", { name: "Names without an ISIN" }))
    .toContainText(/EXAMPLE DEPO SER \(I\)\s*2/);
  await expect(names).not.toContainText("→ INE000A01012");
  await page.getByRole("tab", { name: "Gains" }).click();
  await expect(gains).not.toContainText("INE000A01012");
});
