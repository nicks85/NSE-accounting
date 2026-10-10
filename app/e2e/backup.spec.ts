import { readFileSync } from "node:fs";
import { expect, test } from "./fixtures";

test.describe.configure({ mode: "serial" });

const HEADER = "symbol,isin,trade_date,exchange,segment,series,trade_type,auction,quantity,price,trade_id,order_id,order_execution_time";
const tradebook = { name: "tb.csv", mimeType: "text/csv", buffer: Buffer.from([HEADER,
  "SYNTHA,INE000A01011,2025-05-01,NSE,EQ,EQ,buy,false,100,1000,1,11,2025-05-01T09:30:00",
  "SYNTHA,INE000A01011,2025-07-01,NSE,EQ,EQ,sell,false,100,1100,2,12,2025-07-01T10:00:00"].join("\n") + "\n") };

test("back up, lose the trades, restore them", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText(/runs on this computer/)).toBeVisible({ timeout: 30_000 });
  await page.getByLabel(/Choose tradebook/).setInputFiles(tradebook);
  await page.getByRole("button", { name: "Import files" }).click();
  await expect(page.getByText(/2 trades — 2 shares/)).toBeVisible();

  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Back up saved data" }).click();
  const file = await download;
  expect(file.suggestedFilename()).toMatch(/^kosh-backup-\d{4}-\d{2}-\d{2}\.kosh$/);
  const backup = readFileSync(await file.path());

  await page.getByRole("button", { name: "Undo import of tb.csv" }).click();
  await page.getByRole("button", { name: "Remove 2 trades" }).click();
  await expect(page.getByText("Nothing imported yet.")).toBeVisible();

  await page.getByLabel(/Restore from a backup/).setInputFiles({ name: file.suggestedFilename(), mimeType: "application/octet-stream", buffer: backup });
  await page.getByRole("button", { name: "Replace with this backup" }).click();
  await expect(page.getByText(/Restored from kosh-backup-/)).toBeVisible();
  await expect(page.getByText(/2 trades — 2 shares/)).toBeVisible();
});

test("a file that isn't a backup is refused and nothing changes", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText(/runs on this computer/)).toBeVisible({ timeout: 30_000 });
  await page.getByLabel(/Choose tradebook/).setInputFiles(tradebook);
  await page.getByRole("button", { name: "Import files" }).click();
  await expect(page.getByText(/2 trades — 2 shares/)).toBeVisible();
  await page.getByLabel(/Restore from a backup/).setInputFiles({ name: "notes.kosh", mimeType: "text/plain", buffer: Buffer.from("hello") });
  await page.getByRole("button", { name: "Replace with this backup" }).click();
  await expect(page.getByRole("alert")).toContainText("not an SQLite file");
  await expect(page.getByText(/2 trades — 2 shares/)).toBeVisible();
});
