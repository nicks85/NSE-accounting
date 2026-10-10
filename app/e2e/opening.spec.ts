import { expect, importFiles, test } from "./fixtures";

// Synthetic, check-digit-valid fake ISIN. The tradebook only has the sale (Jun 2025, 100 @300);
// the 100 shares were bought in 2016 @100, before the earliest tradebook.
const HEADER = "symbol,isin,trade_date,exchange,segment,series,trade_type,auction,quantity,price,trade_id,order_id,order_execution_time";
const tradebook = { name: "tb.csv", mimeType: "text/csv", buffer: Buffer.from([HEADER,
  "SYNTHA,INE000A01012,2025-06-10,NSE,EQ,EQ,sell,false,100,300,1,11,2025-06-10T10:00:00"].join("\n") + "\n") };

test("holdings entered by hand resolve a sale from before the first tradebook", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText(/runs on this computer/)).toBeVisible({ timeout: 30_000 });
  await page.getByLabel(/Choose tradebook/).setInputFiles(tradebook);
  await importFiles(page);
  await expect(page.getByText(/Imported 1 new trade/)).toBeVisible();
  await page.getByRole("tab", { name: "Gains" }).click();
  await expect(page.locator("tr.total")).toContainText("Incomplete: 1 sale(s)");

  await page.getByRole("tab", { name: "Import" }).click();
  await page.getByLabel("Holdings from before your first tradebook").check();
  await page.getByLabel("ISIN, lot 1").fill("INE000A01012");
  await page.getByLabel("Name, lot 1").fill("SYNTHETIC ALPHA");
  await page.getByLabel("Quantity, lot 1").fill("100");
  await page.getByLabel("Bought on, lot 1").fill("2016-04-01");
  await page.getByLabel("Price, lot 1").fill("100");
  await page.getByRole("button", { name: "Save holdings" }).click();
  await expect(page.getByText("Saved 1 holding.")).toBeVisible();
  await expect(page.getByRole("table", { name: "Import history" })).toContainText("Entered by hand");

  // LTCG 100 x (300 - 100) = 20,000, under the ₹1.25 lakh exemption → tax 0. Bought before
  // 31-Jan-2018, so Kosh asks for that day's price; without it the actual cost is used.
  await page.getByRole("tab", { name: "Gains" }).click();
  await expect(page.locator("tr.total")).toContainText("₹0.00");
  await expect(page.getByText(/OPENING_HOLDINGS Q-029/)).toBeVisible();
  await expect(page.getByLabel("Capital gains")).toContainText("SYNTHETIC ALPHA");

  // Entering the same lot again is recognised.
  await page.getByRole("tab", { name: "Import" }).click();
  await page.getByLabel("Holdings from before your first tradebook").check();
  await page.getByLabel("ISIN, lot 1").fill("INE000A01012");
  await page.getByLabel("Quantity, lot 1").fill("100");
  await page.getByLabel("Bought on, lot 1").fill("2016-04-01");
  await page.getByLabel("Price, lot 1").fill("100");
  await page.getByRole("button", { name: "Save holdings" }).click();
  await expect(page.getByText("Saved 0 holdings; 1 already saved and skipped.")).toBeVisible();
});

test("the filled-in template imports, and a typo in an ISIN is refused", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText(/runs on this computer/)).toBeVisible({ timeout: 30_000 });
  await page.getByLabel("Holdings from before your first tradebook").check();
  const template = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download the template" }).click();
  expect((await template).suggestedFilename()).toBe("kosh-opening-holdings.csv");

  const csv = (row: string) => ({ name: "opening.csv", mimeType: "text/csv",
    buffer: Buffer.from(`isin,name,quantity,buy_date,price,charges,how_acquired\n${row}\n`) });
  await page.getByLabel("Choose the filled-in template").setInputFiles(csv("INE000A01011,,10,2016-04-01,100,,bought"));
  await importFiles(page);
  await expect(page.getByRole("alert")).toContainText("isn't a valid Indian ISIN");

  await page.getByLabel("Choose the filled-in template").setInputFiles(csv("INE000A01012,,10,2016-04-01,100,,gift"));
  await importFiles(page);
  await expect(page.getByText("Imported 1 new trade from Opening holdings")).toBeVisible();
  await expect(page.getByText(/1 trades — 1 shares/)).toBeVisible();
});
