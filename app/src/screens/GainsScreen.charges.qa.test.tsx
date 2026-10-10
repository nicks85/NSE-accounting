// QA review of brief 0005: known gaps in the "why?" charges, as expected failures.
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ReportProvider } from "../report";
import { EMPTY_SESSION, SessionProvider, type Session } from "../state";
import { GainsScreen } from "./GainsScreen";

function report(line: object) {
  return {
    tax_year: "FY 2025-26", start_year: 2025, act: "Income-tax Act, 1961",
    summary: { bucket_nets: [], exemption_used: [], taxable: [], special_rate_tax: "0", special_rate_tax_rounded: "0",
      speculative_income: "0", non_speculative_income: "0", speculative_after_setoff: "0", non_speculative_after_setoff: "0" },
    capital_gains: [{ instrument: "INE000A01011", isin: "INE000A01011", acquired_on: "2024-01-01", sold_on: "2025-06-01",
      long_term_after: "2025-01-01", quantity: "10", sale_value: "1200", transfer_expenses: "0", actual_cost: "1000",
      cost: "1000", grandfathered_fmv: null, stripped_loss: "0", gain: "200", bucket: "LTCG @ 12.5%", manual: false,
      open_trade_id: "B", close_trade_id: "S", citations: [], ...line }],
    business_lines: [], setoff_steps: [], carried_forward: [], expired: [], open_lots: [],
    complete: true, missing_history: [], excluded_sales: [], excluded_value: "0", warnings: [],
  };
}

function mockEngine(computeReport: object) {
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init: RequestInit) => {
    const request = JSON.parse(String(init.body));
    const result = request.method === "compute" ? computeReport : { isins: [] };
    return new Response(JSON.stringify({ id: request.id, result }));
  }));
}

async function openWhy(session: Partial<Session>) {
  await act(async () => render(
    <SessionProvider initial={{ ...EMPTY_SESSION, ...session }}><ReportProvider><GainsScreen /></ReportProvider></SessionProvider>,
  ));
  const gains = await screen.findByLabelText("Capital gains");
  fireEvent.click(within(gains).getByRole("button", { name: "Why?" }));
  return within(gains).getByRole("list", { name: "Charges" });
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const base = { trade_date: "2024-01-01", side: "BUY", quantity: "10", price: "100", stt: "0", executed_at: null };

describe("QA 0005 why? charges", () => {
  // Fund ids are CAS:<ISIN>#<folio>:<date>:<n> (FOLIO_SEPARATOR is "#"), so split("#")[0] loses them.
  it("finds a mutual fund purchase whose id contains the folio separator", async () => {
    const id = "CAS:INF000A01011#123:2024-01-01:0";
    mockEngine(report({ instrument: "INF000A01011#123", isin: "INF000A01011", open_trade_id: id, close_trade_id: "none" }));
    const charges = await openWhy({ trades: [{ ...base, trade_id: id, instrument: "INF000A01011#123", segment: "MUTUAL_FUND",
      charges: "0.05", charge_parts: { STAMP: "0.05" } }] as never });
    expect(charges.textContent).toMatch(/stamp duty ₹0.05/);
  });

  // After a 1:2 split the line has 20 shares from a purchase of 10.
  it("doesn't say a split-adjusted line holds more shares than the purchase", async () => {
    mockEngine(report({ quantity: "20", close_trade_id: "none" }));
    const charges = await openWhy({ trades: [{ ...base, trade_id: "B", instrument: "INE000A01011", segment: "EQUITY",
      charges: "1", charge_parts: { BROKERAGE: "1" } }] as never });
    expect(charges.textContent).toMatch(/brokerage ₹1.00/);
    expect(charges.textContent).not.toMatch(/20 of those 10/);
  });
});
