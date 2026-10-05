import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ReportProvider } from "../report";
import { EMPTY_SESSION, SessionProvider, useSession, type Session } from "../state";
import { HoldingsScreen } from "./HoldingsScreen";
import { LossesScreen } from "./LossesScreen";

const BASE = {
  tax_year: "FY 2025-26", start_year: 2025, act: "Income-tax Act, 1961",
  summary: { bucket_nets: [], exemption_used: [], taxable: [], special_rate_tax: "0", special_rate_tax_rounded: "0",
    speculative_income: "0", non_speculative_income: "0", speculative_after_setoff: "0", non_speculative_after_setoff: "0" },
  capital_gains: [], business_lines: [], setoff_steps: [], carried_forward: [], expired: [], open_lots: [], warnings: [],
};

function mockEngine(report: object) {
  const calls: { method: string; params: Record<string, unknown> }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_u: string, init: RequestInit) => {
    const request = JSON.parse(String(init.body));
    calls.push(request);
    return new Response(JSON.stringify({ id: request.id, result: request.method === "compute" ? report : { isins: [] } }));
  }));
  return calls;
}

function Probe() {
  return <output data-testid="state">{JSON.stringify(useSession().session)}</output>;
}

async function renderWith(ui: ReactElement, session: Partial<Session> = {}) {
  await act(async () => render(
    <SessionProvider initial={{ ...EMPTY_SESSION, trades: [{ trade_id: "x" } as never], ...session }}>
      <ReportProvider>{ui}<Probe /></ReportProvider>
    </SessionProvider>,
  ));
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("HoldingsScreen", () => {
  it("lists open lots with names and formatted figures", async () => {
    mockEngine({ ...BASE, open_lots: [
      { instrument: "INF000E01011#9", isin: "INF000E01011", acquired_on: "2024-03-01", quantity: "60.500", cost: "60000.25", segment: "MF" }] });
    await renderWith(<HoldingsScreen />, { names: { INF000E01011: "Synthetic Fund" } });
    const table = await screen.findByLabelText("Open lots");
    expect(within(table).getByText("Synthetic Fund")).toBeTruthy();
    expect(within(table).getByText("Mutual fund")).toBeTruthy();
    expect(within(table).getByText("60.5")).toBeTruthy();
    expect(within(table).getByText("₹60,000.25")).toBeTruthy();
  });

  it("handles no holdings, idle and errors", async () => {
    mockEngine(BASE);
    await renderWith(<HoldingsScreen />);
    expect(await screen.findByText("No open holdings.")).toBeTruthy();
    cleanup();
    await act(async () => render(<SessionProvider><ReportProvider><HoldingsScreen /></ReportProvider></SessionProvider>));
    expect(screen.getByText("Import trades first.")).toBeTruthy();
    cleanup();
    vi.stubGlobal("fetch", vi.fn(async (_u: string, init: RequestInit) => new Response(JSON.stringify({
      id: JSON.parse(String(init.body)).id, error: { type: "X", message: "nope" } }))));
    await renderWith(<HoldingsScreen />);
    expect((await screen.findByRole("alert")).textContent).toMatch(/nope/);
  });
});

describe("LossesScreen", () => {
  it("validates, adds and removes brought-forward losses and sends them to the engine", async () => {
    const calls = mockEngine({ ...BASE, carried_forward: [{ origin_year: 2023, kind: "Short-term capital loss", amount: "4000" }],
      expired: [{ origin_year: 2020, kind: "Speculative business loss", amount: "5000" }] });
    await renderWith(<LossesScreen />);
    fireEvent.change(screen.getByLabelText("Amount (₹)"), { target: { value: "12.345" } });
    fireEvent.click(screen.getByRole("button", { name: "Add loss" }));
    expect(screen.getByRole("alert").textContent).toMatch(/positive amount/);
    fireEvent.change(screen.getByLabelText("Year it arose"), { target: { value: "2023" } });
    fireEvent.change(screen.getByLabelText("Amount (₹)"), { target: { value: "10,000.50" } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Add loss" })));
    expect(JSON.parse(screen.getByTestId("state").textContent!).broughtForward).toEqual([
      { origin_year: 2023, kind: "Short-term capital loss", amount: "10000.50" }]);
    expect(calls.filter((c) => c.method === "compute").at(-1)!.params.brought_forward).toHaveLength(1);
    expect(within(screen.getByLabelText("Carried forward")).getByText("FY 2023-24")).toBeTruthy();
    expect(within(screen.getByLabelText("Expired")).getByText("₹5,000.00")).toBeTruthy();
    expect(screen.getByLabelText("Year it arose").querySelectorAll("option")).toHaveLength(8);
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Remove" })));
    expect(JSON.parse(screen.getByTestId("state").textContent!).broughtForward).toEqual([]);
  });

  it("labels 2025-Act years TY and shows nothing to carry", async () => {
    mockEngine({ ...BASE, tax_year: "TY 2027-28" });
    await renderWith(<LossesScreen />, { year: 2027 });
    expect(screen.getByText("Losses brought forward into TY 2027-28")).toBeTruthy();
    expect(await screen.findByText("No losses to carry forward.")).toBeTruthy();
  });
});
