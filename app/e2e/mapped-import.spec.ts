import { expect, importFiles, test } from "./fixtures";

// Synthetic file with user-defined headers (Kosh has no built-in Groww layout).
const csv = ["Date,Type,Qty,Price,Order Id,ISIN,Exchange",
  "01/05/2025,BUY,10,100.50,G1,INE000A01011,NSE",
  "01/06/2025,SELL,10,110,G2,INE000A01011,NSE"].join("\n") + "\n";

test("mapped import needs the required columns, then imports through the engine", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText(/runs on this computer/)).toBeVisible({ timeout: 30_000 });
  await page.getByLabel(/Groww or other/).check();
  await page.getByLabel(/Choose tradebook/).setInputFiles({ name: "groww.csv", mimeType: "text/csv", buffer: Buffer.from(csv) });
  await expect(page.getByRole("button", { name: "Preview import" })).toBeDisabled();
  await expect(page.getByText(/Still needed/)).toBeVisible();
  for (const [label, value] of [["Trade date *", "Date"], ["Buy / sell *", "Type"], ["Quantity *", "Qty"],
                                ["Price *", "Price"], ["Trade / order number *", "Order Id"],
                                ["ISIN (shares)", "ISIN"], ["Exchange", "Exchange"]]) {
    await page.getByLabel(label, { exact: true }).fill(value);
  }
  await importFiles(page);
  await expect(page.getByText("Imported 2 new trades from Groww (mapped)")).toBeVisible();
  await page.getByRole("tab", { name: "Gains" }).click();
  // STCG = 10 x (110 - 100.50) = 95 → ₹95.00 at 20%: tax 19 → rounded to ₹20.
  await expect(page.locator("tr.total")).toContainText("₹20.00");
});
