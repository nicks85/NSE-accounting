import { expect, importFiles, test } from "./fixtures";

// Synthetic tradebooks (fake ISIN) in two demat accounts. Zerodha: 100 bought @100 on
// 2-Jan-23. Groww: 50 bought @300 on 3-Jun-24, 120 sold @400 on 2-Jun-25. 100 were moved
// Zerodha → Groww on 2-Sep-24.
const HEADER = "symbol,isin,trade_date,exchange,segment,series,trade_type,auction,quantity,price,trade_id,order_id,order_execution_time";
const csv = (name: string, rows: string[]) => ({ name, mimeType: "text/csv", buffer: Buffer.from([HEADER, ...rows].join("\n") + "\n") });
const ZERODHA = csv("zerodha.csv", ["SYNTHA,INE000A01011,2023-01-02,NSE,EQ,EQ,buy,false,100,100,1,11,2023-01-02T09:30:00"]);
const GROWW = csv("groww.csv", [
  "SYNTHA,INE000A01011,2024-06-03,NSE,EQ,EQ,buy,false,50,300,7,71,2024-06-03T09:30:00",
  "SYNTHA,INE000A01011,2025-06-02,NSE,EQ,EQ,sell,false,120,400,8,81,2025-06-02T10:00:00"]);

test("FIFO per demat account, and a transfer between accounts", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText(/runs on this computer/)).toBeVisible({ timeout: 30_000 });
  await expect(page.getByLabel("Demat account these trades are in")).toHaveValue("Zerodha");
  await page.getByLabel(/Choose tradebook/).setInputFiles(ZERODHA);
  await importFiles(page);
  await expect(page.getByText(/Imported 1 new trade/)).toBeVisible();
  await page.getByLabel("Demat account these trades are in").fill("Groww");
  await page.getByLabel(/Choose tradebook/).setInputFiles(GROWW);
  await importFiles(page);
  await expect(page.getByText(/Imported 2 new trades/)).toBeVisible();

  // Groww's sale can't use Zerodha's shares: 70 of the 120 have no purchase in Groww.
  await page.getByRole("tab", { name: "Gains" }).click();
  await expect(page.locator("tr.total")).toContainText("Incomplete");
  await expect(page.getByLabel("Missing purchase history")).toContainText("from Groww");

  await page.getByRole("tab", { name: "Holdings" }).click();
  await page.getByLabel("Date").fill("2024-09-02");
  await page.getByLabel("Shares").selectOption("INE000A01011");
  await page.getByLabel("Quantity").fill("100");
  await page.getByLabel("From").selectOption("Zerodha");
  await page.getByLabel("To").selectOption("Groww");
  await page.getByRole("button", { name: "Add transfer" }).click();
  await expect(page.getByRole("table", { name: "Transfers" })).toContainText("2024-09-02");
  // 30 of the moved shares stay in Groww, bought 2-Jan-23 and arrived 2-Sep-24.
  await expect(page.getByRole("table", { name: "Open lots" })).toContainText("arrived 2024-09-02");

  // Groww's own 50 go first (entered 3-Jun-24): STCG 50 x 100 = 5,000 → 1,000 tax. Then 70 of the
  // moved shares: LTCG 70 x 300 = 21,000, under the ₹1.25 lakh exemption. Total 1,000.
  await page.getByRole("tab", { name: "Gains" }).click();
  await expect(page.locator("tr.total")).toContainText("₹1,000.00");
  await expect(page.getByText(/UNVERIFIED Q-026/).first()).toBeVisible();

  // Moving more than the account held is refused.
  await page.getByRole("tab", { name: "Holdings" }).click();
  await page.getByLabel("Date").fill("2024-09-03");
  await page.getByLabel("Shares").selectOption("INE000A01011");
  await page.getByLabel("Quantity").fill("1");
  await page.getByLabel("From").selectOption("Zerodha");
  await page.getByLabel("To").selectOption("Groww");
  await page.getByRole("button", { name: "Add transfer" }).click();
  await expect(page.getByRole("alert")).toContainText("Zerodha held 0");
});
