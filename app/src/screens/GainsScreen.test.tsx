import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ReportProvider } from "../report";
import { EMPTY_SESSION, SessionProvider, useSession, type Session } from "../state";
import { GainsScreen } from "./GainsScreen";

const CITE = { topic: "LTCG on STT-paid equity above the exemption", section: "s.112A", act_1961: "s.112A",
  act_2025: "s.198", url: "https://example.invalid/act", unverified: false, question: null };
const GUESS = { ...CITE, topic: "Disposals assumed STT-paid", section: "s.111A(1)(b)", unverified: true, question: "Q-009" };

function report(overrides: object = {}) {
  return {
    tax_year: "FY 2025-26", start_year: 2025, act: "Income-tax Act, 1961",
    summary: { bucket_nets: [{ label: "LTCG @ 12.5%", amount: "1000000" }], exemption_used: [{ label: "LTCG @ 12.5%", amount: "125000" }],
      taxable: [{ label: "LTCG @ 12.5%", amount: "875000" }], special_rate_tax: "109375.000", special_rate_tax_rounded: "109380",
      speculative_income: "500", non_speculative_income: "0", speculative_after_setoff: "500", non_speculative_after_setoff: "0" },
    capital_gains: [{ instrument: "INE000A01011", isin: "INE000A01011", acquired_on: "2015-01-01", sold_on: "2025-06-01",
      long_term_after: "2016-01-01", quantity: "1000", sale_value: "1500000", transfer_expenses: "0", actual_cost: "500000",
      cost: "500000", grandfathered_fmv: null, stripped_loss: "0", gain: "1000000", bucket: "LTCG @ 12.5%", manual: false,
      open_trade_id: "B", close_trade_id: "S", citations: [CITE, GUESS] }],
    business_lines: [{ instrument: "INE000B01012", opened_on: "2025-06-02", closed_on: "2025-06-02", quantity: "50.000",
      income: "500", speculative: true, citations: [CITE] }],
    setoff_steps: [{ loss: "LTCG exemption", against: "LTCG @ 12.5%", amount: "125000", citation: CITE }],
    carried_forward: [], expired: [], open_lots: [],
    complete: true, missing_history: [], excluded_sales: [], excluded_value: "0",
    warnings: [{ code: "UNVERIFIED", message: "Disposals assumed STT-paid", question: "Q-009", ref: null }],
    ...overrides,
  };
}

function mockEngine(computeReport: object, unclassified: string[] = []) {
  const calls: { method: string; params: Record<string, unknown> }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init: RequestInit) => {
    const request = JSON.parse(String(init.body));
    calls.push(request);
    const result = request.method === "compute" ? computeReport : { isins: unclassified };
    return new Response(JSON.stringify({ id: request.id, result }));
  }));
  return calls;
}

function Probe() {
  return <output data-testid="state">{JSON.stringify(useSession().session)}</output>;
}

async function renderWith(session: Partial<Session>) {
  await act(async () => render(
    <SessionProvider initial={{ ...EMPTY_SESSION, trades: [{ trade_id: "x" } as never], ...session }}>
      <ReportProvider><GainsScreen /><Probe /></ReportProvider>
    </SessionProvider>,
  ));
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("GainsScreen", () => {
  it("shows the summary, lines and a why drill-down with sections and UNVERIFIED badges", async () => {
    mockEngine(report());
    await renderWith({});
    const summary = await screen.findByLabelText("Summary");
    expect(within(summary).getByText("₹1,09,380.00")).toBeTruthy();
    const gains = screen.getByLabelText("Capital gains");
    expect(within(gains).getByText("1000")).toBeTruthy();
    fireEvent.click(within(gains).getByRole("button", { name: "Why?" }));
    expect(within(gains).getByText(/long-term if sold after 2016-01-01/)).toBeTruthy();
    expect(within(gains).getByText("s.112A")).toBeTruthy();
    expect(within(gains).getByText("UNVERIFIED Q-009")).toBeTruthy();
    expect(within(gains).getAllByText("https://example.invalid/act", { selector: "code" })).toHaveLength(2);
    const trading = screen.getByLabelText("Trading income");
    expect(within(trading).getByText("50")).toBeTruthy();
    fireEvent.click(within(trading).getByRole("button", { name: "Why?" }));
    expect(within(trading).getByText("s.112A")).toBeTruthy();
  });

  it("asks for missing FMVs and fund classes and feeds them back", async () => {
    const calls = mockEngine(report(), ["INF000G01014"]);
    await renderWith({});
    const fmv = await screen.findByLabelText(/31-Jan-2018 price for INE000A01011/);
    fireEvent.change(fmv, { target: { value: "800" } });
    await act(async () => fireEvent.blur(fmv));
    expect(JSON.parse(screen.getByTestId("state").textContent!).fmv2018).toEqual({ INE000A01011: "800" });
    await act(async () => fireEvent.change(screen.getByLabelText(/Fund class for INF000G01014/), { target: { value: "other" } }));
    expect(JSON.parse(screen.getByTestId("state").textContent!).fundClasses).toEqual({ INF000G01014: "other" });
    const lastCompute = calls.filter((c) => c.method === "compute").at(-1)!;
    expect(lastCompute.params).toMatchObject({ fmv_2018: { INE000A01011: "800" }, fund_classes: { INF000G01014: "other" } });
  });

  it("ignores an invalid FMV and handles year changes, idle and errors", async () => {
    const calls = mockEngine(report({ capital_gains: [] }));
    await renderWith({});
    expect(await screen.findByText("No sales in this year.")).toBeTruthy();
    await act(async () => fireEvent.change(screen.getByLabelText("Tax year"), { target: { value: "2026" } }));
    expect(calls.filter((c) => c.method === "compute").at(-1)!.params.year).toBe(2026);
    cleanup();
    await act(async () => render(<SessionProvider><ReportProvider><GainsScreen /></ReportProvider></SessionProvider>));
    expect(screen.getByText("Import trades first.")).toBeTruthy();
    cleanup();
    vi.stubGlobal("fetch", vi.fn(async (_u: string, init: RequestInit) => new Response(JSON.stringify({
      id: JSON.parse(String(init.body)).id, error: { type: "X", message: "bad year" } }))));
    await renderWith({});
    expect((await screen.findByRole("alert")).textContent).toMatch(/bad year/);
  });

  it("marks manual lines without figures", async () => {
    const manual = { ...report().capital_gains[0], manual: true };
    mockEngine(report({ capital_gains: [manual] }));
    await renderWith({ fmv2018: { INE000A01011: "800" } });
    const gains = await screen.findByLabelText("Capital gains");
    expect(within(gains).getByText("MANUAL")).toBeTruthy();
    expect(within(gains).getAllByText("—")).toHaveLength(2);
  });
});

describe("GainsScreen inputs (QA)", () => {
  it("rejects an invalid FMV inline, accepts commas and clears with an empty box", async () => {
    mockEngine(report());
    await renderWith({});
    const fmv = await screen.findByLabelText(/31-Jan-2018 price for INE000A01011/);
    fireEvent.change(fmv, { target: { value: "abc" } });
    await act(async () => fireEvent.blur(fmv));
    expect(fmv.getAttribute("aria-invalid")).toBe("true");
    expect(screen.getByText("Enter a price like 1234.55")).toBeTruthy();
    fireEvent.change(fmv, { target: { value: "1,234.50" } });
    await act(async () => fireEvent.blur(fmv));
    expect(JSON.parse(screen.getByTestId("state").textContent!).fmv2018).toEqual({ INE000A01011: "1234.50" });
    fireEvent.change(fmv, { target: { value: "" } });
    await act(async () => fireEvent.blur(fmv));
    expect(JSON.parse(screen.getByTestId("state").textContent!).fmv2018).toEqual({});
  });

  it("marks CAS-guessed classes until confirmed and keeps the report while recomputing", async () => {
    mockEngine(report());
    await renderWith({ fundClasses: { INF000E01011: "equity-oriented" }, unconfirmed: ["INF000E01011"] });
    expect(await screen.findByText(/guessed from the CAS/)).toBeTruthy();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Confirm" })));
    expect(JSON.parse(screen.getByTestId("state").textContent!).unconfirmed).toEqual([]);
    expect(screen.queryByText(/guessed from the CAS/)).toBeNull();
    expect(screen.getByLabelText("Summary")).toBeTruthy(); // not unmounted by the recompute
  });
});

const GAP = { trade_id: "S1", instrument: "INE000A01011", isin: "INE000A01011", sold_on: "2025-06-10", quantity: "100",
  price: "300", sale_value: "30000", segment: "EQUITY" };

describe("GainsScreen missing purchase history", () => {
  it("withholds the tax figure and adds a hand-entered purchase as a MANUAL buy", async () => {
    const calls = mockEngine(report({ complete: false, missing_history: [GAP] }));
    await renderWith({});
    const summary = await screen.findByLabelText("Summary");
    expect(within(summary).queryByText("₹1,09,380.00")).toBeNull();
    expect(within(summary).getByText("Incomplete: 1 sale(s) missing purchase history")).toBeTruthy();
    const gaps = screen.getByLabelText("Missing purchase history");
    fireEvent.click(within(gaps).getByRole("button", { name: "Add the purchase" }));
    const add = within(gaps).getByRole("button", { name: "Add purchase" }) as HTMLButtonElement;
    expect(add.disabled).toBe(true);
    fireEvent.change(within(gaps).getByLabelText("Purchase date"), { target: { value: "2025-07-01" } });
    expect(within(gaps).getByText(/a purchase date before the sale/)).toBeTruthy();
    fireEvent.change(within(gaps).getByLabelText("Purchase date"), { target: { value: "2023-01-02" } });
    fireEvent.change(within(gaps).getByLabelText("Price per share (₹)"), { target: { value: "1,00.50" } });
    fireEvent.change(within(gaps).getByLabelText("How you got these shares"), { target: { value: "ipo" } });
    expect(within(gaps).getByText(/issue price you paid/)).toBeTruthy();
    await act(async () => fireEvent.click(add));
    const session = JSON.parse(screen.getByTestId("state").textContent!);
    expect(session.manualBuys).toEqual([{ how: "ipo", forTrade: "S1", trade: {
      trade_id: "MANUAL:1", trade_date: "2023-01-02", instrument: "INE000A01011", side: "BUY", quantity: "100",
      price: "100.50", charges: "0", stt: "0", segment: "EQUITY", executed_at: null, account: null } }]);
    const sent = calls.filter((c) => c.method === "compute").at(-1)!.params.trades as { trade_id: string }[];
    expect(sent.map((t) => t.trade_id)).toEqual(["x", "MANUAL:1"]);
    expect(within(screen.getByLabelText("Missing purchase history")).getByText("IPO allotment")).toBeTruthy();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Remove" })));
    expect(JSON.parse(screen.getByTestId("state").textContent!).manualBuys).toEqual([]);
  });

  it("excludes a sale, labels the total and can include it again", async () => {
    const calls = mockEngine(report({ excluded_sales: [GAP], excluded_value: "30000" }));
    await renderWith({ excluded: ["S1", "OLD"] });
    const summary = await screen.findByLabelText("Summary");
    expect(within(summary).getByText("₹1,09,380.00")).toBeTruthy();
    expect(within(summary).getByText(/Excludes 1 sale\(s\) \(₹30,000.00 sale value\)/)).toBeTruthy();
    expect(calls.filter((c) => c.method === "compute").at(-1)!.params.excluded).toEqual(["S1", "OLD"]);
    const gaps = screen.getByLabelText("Missing purchase history");
    expect(within(gaps).getByText(/Sale OLD \(not in this year/)).toBeTruthy();
    await act(async () => fireEvent.click(within(gaps).getAllByRole("button", { name: "Include again" })[0]));
    expect(JSON.parse(screen.getByTestId("state").textContent!).excluded).toEqual(["OLD"]);
  });

  it("excludes from the gap row", async () => {
    mockEngine(report({ complete: false, missing_history: [GAP] }));
    await renderWith({});
    await act(async () => fireEvent.click(await screen.findByRole("button", { name: "Exclude this sale" })));
    expect(JSON.parse(screen.getByTestId("state").textContent!).excluded).toEqual(["S1"]);
  });
});

describe("Charges by type in why? (brief 0005)", () => {
  const base = { trade_date: "2015-01-01", instrument: "INE000A01011", side: "BUY", quantity: "1000", price: "500",
    stt: "100", segment: "EQUITY", executed_at: null };
  const BUY = { ...base, trade_id: "B", charges: "38.60", charge_parts: { BROKERAGE: "20", GST: "3.60", STAMP: "15" } };
  const SELL = { ...base, trade_id: "S", side: "SELL", trade_date: "2025-06-01", quantity: "2500", charges: "12", stt: "0" };

  it("shows each whole trade's charges by type, and how much of it the line uses", async () => {
    mockEngine(report());
    await renderWith({ trades: [BUY, SELL] as never });
    const gains = await screen.findByLabelText("Capital gains");
    fireEvent.click(within(gains).getByRole("button", { name: "Why?" }));
    const charges = within(gains).getByRole("list", { name: "Charges" });
    expect(charges.textContent).toMatch(/Charges on the purchase \(1000 on 2015-01-01\): brokerage ₹20.00, GST ₹3.60, stamp duty ₹15.00; STT ₹100.00\./);
    expect(charges.textContent).toMatch(/Charges on the sale \(2500 on 2025-06-01\): ₹12.00 \(not broken down by type\)\. 1000 of those 2500 are in this line\./);
  });

  it("says when a file has no charges, and skips trades it can't find", async () => {
    mockEngine(report());
    await renderWith({ trades: [{ ...BUY, charges: "0", stt: "0", charge_parts: {} }] as never });
    const gains = await screen.findByLabelText("Capital gains");
    fireEvent.click(within(gains).getByRole("button", { name: "Why?" }));
    const charges = within(gains).getByRole("list", { name: "Charges" });
    expect(charges.textContent).toMatch(/no charges in the file/);
    expect(charges.textContent).not.toMatch(/sale/);  // "S" isn't loaded
  });
});

describe("Charges in why? after review (QA)", () => {
  const base = { trade_date: "2015-01-01", instrument: "INE000A01011", side: "BUY", quantity: "1000", price: "500",
    stt: "0", segment: "EQUITY", executed_at: null };

  it("names charges entered by hand, and older Angel One imports", async () => {
    mockEngine(report({ capital_gains: [{ ...report().capital_gains[0], open_trade_id: "OPENING:x:1", close_trade_id: "ANGELONE:NSE:9#delivery" }] }));
    await renderWith({ trades: [{ ...base, trade_id: "OPENING:x:1", charges: "12" },
      { ...base, trade_id: "ANGELONE:NSE:9", side: "SELL", charges: "4" }] as never });
    const gains = await screen.findByLabelText("Capital gains");
    fireEvent.click(within(gains).getByRole("button", { name: "Why?" }));
    const charges = within(gains).getByRole("list", { name: "Charges" });
    expect(charges.textContent).toMatch(/₹12.00 \(entered by hand\)/);
    expect(charges.textContent).toMatch(/₹4.00 \(not broken down by type\)/);
  });

  it("says when nothing was entered for a hand-entered lot", async () => {
    mockEngine(report({ capital_gains: [{ ...report().capital_gains[0], open_trade_id: "MANUAL:1" }] }));
    await renderWith({ trades: [{ ...base, trade_id: "MANUAL:1", charges: "0" }] as never });
    const gains = await screen.findByLabelText("Capital gains");
    fireEvent.click(within(gains).getByRole("button", { name: "Why?" }));
    expect(within(gains).getByRole("list", { name: "Charges" }).textContent).toMatch(/no charges entered/);
  });
});

describe("Charges in why? after a split (QA round 2)", () => {
  it("counts the purchase in today's shares", async () => {
    const line = { ...report().capital_gains[0], quantity: "8", split_factor: "2", open_trade_id: "B" };
    mockEngine(report({ capital_gains: [line] }));
    await renderWith({ trades: [{ trade_id: "B", trade_date: "2015-01-01", instrument: "INE000A01011", side: "BUY",
      quantity: "10", price: "500", charges: "5", stt: "0", segment: "EQUITY", executed_at: null }] as never });
    const gains = await screen.findByLabelText("Capital gains");
    fireEvent.click(within(gains).getByRole("button", { name: "Why?" }));
    const charges = within(gains).getByRole("list", { name: "Charges" });
    expect(charges.textContent).toMatch(/After a split, this purchase is 20 shares; 8 of them are in this line\./);
    expect(charges.textContent).not.toMatch(/8 of those 10/);
  });
});
