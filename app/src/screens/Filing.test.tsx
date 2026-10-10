import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ReportProvider } from "../report";
import { EMPTY_SESSION, SessionProvider, useSession, type Session } from "../state";
import { ExportScreen } from "./ExportScreen";
import { GainsScreen } from "./GainsScreen";
import { LossesScreen } from "./LossesScreen";

const REPORT = {
  tax_year: "FY 2025-26", start_year: 2025, act: "Income-tax Act, 1961",
  summary: { bucket_nets: [], exemption_used: [], taxable: [], special_rate_tax: "0", special_rate_tax_rounded: "2000",
    speculative_income: "0", non_speculative_income: "0", speculative_after_setoff: "0", non_speculative_after_setoff: "0" },
  capital_gains: [], business_lines: [], setoff_steps: [], carried_forward: [], expired: [], open_lots: [], warnings: [],
  complete: true, missing_history: [], excluded_sales: [], excluded_value: "0", filing: null,
};
const FILED = { filed_at: "2026-07-20T10:00:00+00:00", itr_form: "ITR-2", engine_version: "0.0.1", changes: [] };
const CHANGED = { ...FILED, changes: [
  { item: "Tax at special rates (rounded)", filed: "2000", now: "3000" },
  { item: "Number of capital-gain lines", filed: "1", now: "2" }] };

type Call = { method: string; params: Record<string, unknown> };

/** compute answers with each report in turn (the last one repeats). */
function engine(...reports: object[]) {
  const calls: Call[] = [];
  let computes = 0;
  vi.stubGlobal("fetch", vi.fn(async (_u: string, init: RequestInit) => {
    const request = JSON.parse(String(init.body));
    calls.push(request);
    const result = request.method === "compute" ? reports[Math.min(computes++, reports.length - 1)]
      : request.method === "unclassified_funds" ? { isins: [] } : {};
    return new Response(JSON.stringify({ id: request.id, result }));
  }));
  return calls;
}

function Probe() {
  return <output data-testid="state">{JSON.stringify(useSession().session)}</output>;
}

async function renderWith(ui: ReactElement, session: Partial<Session> = {}) {
  await act(async () => render(
    <SessionProvider initial={{ ...EMPTY_SESSION, profileId: 3, trades: [{ trade_id: "x" } as never], ...session }}>
      <ReportProvider>{ui}<Probe /></ReportProvider>
    </SessionProvider>,
  ));
}

const state = () => JSON.parse(screen.getByTestId("state").textContent!);

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Returns filed on time (Q-011)", () => {
  it("asks for each year with a brought-forward loss and this year, and sends late years", async () => {
    const calls = engine(REPORT);
    await renderWith(<LossesScreen />, { broughtForward: [{ origin_year: 2023, kind: "Short-term capital loss", amount: "10" }] });
    const section = await screen.findByLabelText("Returns filed on time");
    expect(within(section).getAllByRole("combobox")).toHaveLength(2);
    await act(async () => fireEvent.change(screen.getByLabelText(/Return for FY 2023-24/), { target: { value: "no" } }));
    await act(async () => fireEvent.change(screen.getByLabelText(/Return for FY 2025-26/), { target: { value: "yes" } }));
    expect(state().filedOnTime).toEqual({ 2023: false, 2025: true });
    await waitFor(() => expect(calls.filter((c) => c.method === "compute").at(-1)!.params).toMatchObject({
      late_returns: [2023], profile_id: 3 }));
    await act(async () => fireEvent.change(screen.getByLabelText(/Return for FY 2023-24/), { target: { value: "" } }));
    expect(state().filedOnTime).toEqual({ 2025: true });
  });
});

describe("Filing a year", () => {
  it("marks the year filed with the chosen form, then shows it", async () => {
    const calls = engine(REPORT, { ...REPORT, filing: FILED });
    await renderWith(<ExportScreen />);
    fireEvent.change(await screen.findByLabelText("Form"), { target: { value: "ITR-2" } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Mark FY 2025-26 as filed" })));
    expect(calls.find((c) => c.method === "ledger_mark_filed")!.params).toMatchObject({ year: 2025, profile_id: 3, itr_form: "ITR-2",
      shown: { summary: { special_rate_tax_rounded: "2000" } } });
    expect(await screen.findByText(/marked as filed on 20 Jul 2026 \(ITR-2\)\. The figures still match/)).toBeTruthy();
  });

  it("can't mark an incomplete year, and unmarks after confirming", async () => {
    const calls = engine({ ...REPORT, complete: false, missing_history: [{ trade_id: "S" }] });
    await renderWith(<ExportScreen />);
    expect((await screen.findByRole("button", { name: "Mark FY 2025-26 as filed" }) as HTMLButtonElement).disabled).toBe(true);
    cleanup();
    vi.unstubAllGlobals();
    const again = engine({ ...REPORT, filing: FILED }, REPORT);
    await renderWith(<ExportScreen />);
    fireEvent.click(await screen.findByRole("button", { name: "Unmark as filed" }));
    fireEvent.click(screen.getByRole("button", { name: "Keep" }));
    expect(again.some((c) => c.method === "ledger_unmark_filed")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Unmark as filed" }));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Forget the filed figures" })));
    expect(again.find((c) => c.method === "ledger_unmark_filed")!.params).toMatchObject({ year: 2025, profile_id: 3 });
    expect(await screen.findByRole("button", { name: "Mark FY 2025-26 as filed" })).toBeTruthy();
    expect(calls.length).toBeGreaterThan(0);
  });

  it("shows a failed mark", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_u: string, init: RequestInit) => {
      const request = JSON.parse(String(init.body));
      const reply = request.method === "ledger_mark_filed" ? { error: { type: "LedgerError", message: "missing purchase history" } }
        : { result: request.method === "compute" ? REPORT : { isins: [] } };
      return new Response(JSON.stringify({ id: request.id, ...reply }));
    }));
    await renderWith(<ExportScreen />);
    await act(async () => fireEvent.click(await screen.findByRole("button", { name: "Mark FY 2025-26 as filed" })));
    expect((await screen.findByRole("alert")).textContent).toMatch(/missing purchase history/);
  });
});

describe("Changed since filing", () => {
  it("lists every changed figure on Gains and Export", async () => {
    engine({ ...REPORT, filing: CHANGED });
    await renderWith(<GainsScreen />);
    const banner = await screen.findByLabelText("Changed since filing");
    expect(within(banner).getByText("₹2,000.00")).toBeTruthy();
    expect(within(banner).getByText("₹3,000.00")).toBeTruthy();
    expect(within(banner).getByText("2")).toBeTruthy();  // counts aren't shown as money
    expect(within(banner).getByText(/marked as filed on 20 Jul 2026/)).toBeTruthy();
    cleanup();
    await renderWith(<ExportScreen />);
    expect(await screen.findByLabelText("Changed since filing")).toBeTruthy();
    expect(screen.getByText(/Its figures have changed since/)).toBeTruthy();
  });

  it("falls back to the stored text for an unreadable date", async () => {
    engine({ ...REPORT, filing: { ...CHANGED, filed_at: "2026-07-20 garbled" } });
    await renderWith(<GainsScreen />);
    expect(await screen.findByText(/marked as filed on 2026-07-20,/)).toBeTruthy();
  });

  it("shows nothing when the figures match or the year isn't filed", async () => {
    engine({ ...REPORT, filing: { ...CHANGED, changes: [{ item: "Net STCG @ 20%", filed: null, now: "5" }] } });
    await renderWith(<GainsScreen />);
    expect(within(await screen.findByLabelText("Changed since filing")).getByText("—")).toBeTruthy();
    cleanup();
    engine({ ...REPORT, filing: FILED });
    await renderWith(<GainsScreen />);
    await screen.findByLabelText("Summary");
    expect(screen.queryByLabelText("Changed since filing")).toBeNull();
  });
});

describe("Late returns on the Losses screen (QA)", () => {
  it("lists lapsed and not-carried losses", async () => {
    const loss = { origin_year: 2024, kind: "Short-term capital loss", amount: "4000" };
    engine({ ...REPORT, lapsed: [loss], not_carried: [{ ...loss, origin_year: 2025, amount: "500" }] });
    await renderWith(<LossesScreen />);
    expect(within(await screen.findByRole("table", { name: "Lapsed" })).getByText("₹4,000.00")).toBeTruthy();
    expect(within(screen.getByRole("table", { name: "Not carried forward" })).getByText("₹500.00")).toBeTruthy();
  });
});

describe("Marking a stale report (QA)", () => {
  it("disables marking while the figures are updating", async () => {
    let release: () => void = () => {};
    let computes = 0;
    vi.stubGlobal("fetch", vi.fn((_u: string, init: RequestInit) => {
      const request = JSON.parse(String(init.body));
      const respond = () => new Response(JSON.stringify({ id: request.id, result: request.method === "compute" ? REPORT : { isins: [] } }));
      if (request.method === "compute" && computes++ > 0) return new Promise<Response>((resolve) => { release = () => resolve(respond()); });
      return Promise.resolve(respond());
    }));
    function Change() {
      const { update } = useSession();
      return <button type="button" onClick={() => update({ fmv2018: { X: "1" } })}>change</button>;
    }
    await renderWith(<><ExportScreen /><Change /></>);
    const mark = await screen.findByRole("button", { name: "Mark FY 2025-26 as filed" }) as HTMLButtonElement;
    expect(mark.disabled).toBe(false);
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "change" })));
    expect((screen.getByRole("button", { name: "Mark FY 2025-26 as filed" }) as HTMLButtonElement).disabled).toBe(true);
    await act(async () => release());
    await waitFor(() => expect((screen.getByRole("button", { name: "Mark FY 2025-26 as filed" }) as HTMLButtonElement).disabled).toBe(false));
  });
});
