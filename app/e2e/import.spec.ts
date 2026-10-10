import { expect, importFiles, test } from "./fixtures";

// Synthetic Zerodha tradebook (fake ISIN); layout per importers/zerodha.py (unconfirmed, Q-015).
const HEADER = "symbol,isin,trade_date,exchange,segment,series,trade_type,auction,quantity,price,trade_id,order_id,order_execution_time";
const ROWS = [
  "SYNTHA,INE000A01011,2025-05-01,NSE,EQ,EQ,buy,false,100,1000,1,11,2025-05-01T09:30:00",
  "SYNTHA,INE000A01011,2025-10-01,NSE,EQ,EQ,sell,false,100,1200,2,12,2025-10-01T10:00:00",
];
const tradebook = { name: "tradebook.csv", mimeType: "text/csv", buffer: Buffer.from([HEADER, ...ROWS].join("\n") + "\n") };

const LATER = "SYNTHA,INE000A01011,2026-01-05,NSE,EQ,EQ,buy,false,10,900,3,13,2026-01-05T09:45:00";
const overlapping = { name: "tradebook-2.csv", mimeType: "text/csv", buffer: Buffer.from([HEADER, ...ROWS, LATER].join("\n") + "\n") };

test("import into the saved ledger: duplicates, reload and undo", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText(/runs on this computer/)).toBeVisible({ timeout: 30_000 });
  await page.getByLabel(/Choose tradebook/).setInputFiles(tradebook);
  await importFiles(page);
  await expect(page.getByText("Imported 2 new trades from Zerodha tradebook (CSV)")).toBeVisible();
  await expect(page.getByText(/isn't confirmed against real files/)).toBeVisible();
  await expect(page.getByText(/2 trades — 2 shares/)).toBeVisible();

  // The same file again is recognised without reading it.
  await page.getByLabel(/Choose tradebook/).setInputFiles(tradebook);
  await importFiles(page);
  await expect(page.getByText(/tradebook.csv: already imported on/)).toBeVisible();

  // An overlapping later file adds only what is new.
  await page.getByLabel(/Choose tradebook/).setInputFiles(overlapping);
  await importFiles(page);
  await expect(page.getByText("tradebook-2.csv: 1 new trade, 2 already in your ledger and skipped.")).toBeVisible();
  const history = page.getByRole("table", { name: "Import history" });
  await expect(history.getByRole("row")).toHaveCount(3);

  // Closing and reopening keeps everything.
  await page.reload();
  await expect(page.getByText(/3 trades — 3 shares/)).toBeVisible({ timeout: 30_000 });
  await expect(history.getByRole("row")).toHaveCount(3);

  // Undo the second import: its one trade goes, the first file's stay.
  await page.getByRole("button", { name: "Undo import of tradebook-2.csv" }).click();
  await page.getByRole("button", { name: "Remove 1 trade" }).click();
  await expect(page.getByText(/2 trades — 2 shares/)).toBeVisible();
  await expect(history.getByRole("row")).toHaveCount(2);
});
