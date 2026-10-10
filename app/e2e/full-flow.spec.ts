import { readFileSync } from "node:fs";
import { expect, importFiles, test } from "./fixtures";

// Phase 4 exit: import → compute → export, in one flow, through the real engine.
// Synthetic Zerodha tradebook (fake ISINs): a pre-2018 holding, a short-term trade and F&O.
const HEADER = "symbol,isin,trade_date,exchange,segment,series,trade_type,auction,quantity,price,trade_id,order_id,order_execution_time";
const csv = [HEADER,
  "SYNTHA,INE000A01011,2015-01-01,NSE,EQ,EQ,buy,false,1000,500,1,11,2015-01-01T09:30:00",
  "SYNTHA,INE000A01011,2025-06-01,NSE,EQ,EQ,sell,false,1000,1500,2,12,2025-06-01T10:00:00",
  "SYNTHC,INE000C01013,2025-04-10,NSE,EQ,EQ,buy,false,100,1000,3,13,2025-04-10T09:30:00",
  "SYNTHC,INE000C01013,2025-12-20,NSE,EQ,EQ,sell,false,100,1300,4,14,2025-12-20T10:00:00",
  "NIFTY25JUNFUT,,2025-06-03,NFO,FO,,sell,false,75,24000,5,15,2025-06-03T09:30:00",
  "NIFTY25JUNFUT,,2025-06-20,NFO,FO,,buy,false,75,23900,6,16,2025-06-20T10:00:00"].join("\n") + "\n";

test("import → compute → export", async ({ page }, testInfo) => {
  await page.goto("/");
  await expect(page.getByText(/runs on this computer/)).toBeVisible({ timeout: 30_000 });

  // Import
  await page.getByLabel(/Choose tradebook/).setInputFiles({ name: "tb.csv", mimeType: "text/csv", buffer: Buffer.from(csv) });
  await importFiles(page);
  await expect(page.getByText("6 trades — 4 shares, 2 F&O")).toBeVisible();

  // Compute: 2018 price → LTCG 7,00,000 - 1,25,000 = 5,75,000 x 12.5% = 71,875;
  // STCG 30,000 x 20% = 6,000 → 77,875 → ₹77,880. F&O income 7,500 (slab).
  await page.getByRole("tab", { name: "Gains" }).click();
  const fmv = page.getByLabel(/31-Jan-2018 price for INE000A01011/);
  await fmv.fill("800");
  await fmv.blur();
  await expect(page.locator("tr.total")).toContainText("₹77,880.00");
  await expect(page.getByLabel("Summary")).toContainText("₹7,500.00");

  // Export: schedules (ITR-3 because of F&O) and the PDF
  await page.getByRole("tab", { name: "Export" }).click();
  await page.getByLabel("Name for INE000A01011").fill("SYNTHETIC A LTD");
  await page.getByLabel("Name for INE000A01011").blur();
  const jsonDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download ITR schedules" }).click();
  const json = await jsonDownload;
  expect(json.suggestedFilename()).toBe("kosh-ITR-3-FY2025-26-schedules.json");
  const jsonPath = testInfo.outputPath("schedules.json");
  await json.saveAs(jsonPath);
  const schedules = JSON.parse(readFileSync(jsonPath, "utf8"));
  expect(schedules.Schedule112A.Schedule112ADtls[0]).toMatchObject({
    ShareOnOrBefore: "BE", ISINCode: "INE000A01011", ShareUnitName: "SYNTHETIC A LTD", Balance: 700000 });
  expect(schedules.ScheduleCGFor23.ShortTermCapGainFor23.TotalSTCG).toBe(30000);
  await expect(page.getByText(/valid against the official schema/)).toBeVisible();

  const pdfDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download PDF summary" }).click();
  const pdf = await pdfDownload;
  const pdfPath = testInfo.outputPath("summary.pdf");
  await pdf.saveAs(pdfPath);
  expect(readFileSync(pdfPath).subarray(0, 5).toString()).toBe("%PDF-");
});
