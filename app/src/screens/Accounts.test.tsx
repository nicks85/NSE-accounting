import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ReportProvider } from "../report";
import { EMPTY_SESSION, SessionProvider, useSession, type Session } from "../state";
import { HoldingsScreen } from "./HoldingsScreen";
import { ImportScreen } from "./ImportScreen";
import { MissingHistory } from "./MissingHistory";

type Call = { method: string; params: Record<string, unknown> };
const ISIN = "INE000A01012";
const trade = (id: string, account: string | null, side: "BUY" | "SELL" = "BUY") => ({
  trade_id: id, trade_date: "2024-01-02", instrument: ISIN, side, quantity: "100", price: "100", charges: "0",
  stt: "0", segment: "EQUITY" as const, executed_at: null, account,
});
const MOVE = { transfer_id: "TRANSFER:1", on: "2024-09-02", instrument: ISIN, isin: ISIN, quantity: "60",
  from_account: "Zerodha", to_account: "Groww" };
const REPORT = {
  tax_year: "FY 2025-26", start_year: 2025, act: "Income-tax Act, 1961",
  summary: { bucket_nets: [], exemption_used: [], taxable: [], special_rate_tax: "0", special_rate_tax_rounded: "0",
    speculative_income: "0", non_speculative_income: "0", speculative_after_setoff: "0", non_speculative_after_setoff: "0" },
  capital_gains: [], business_lines: [], setoff_steps: [], carried_forward: [], expired: [], warnings: [],
  complete: true, missing_history: [], excluded_sales: [], excluded_value: "0", filing: null,
  open_lots: [
    { instrument: ISIN, isin: ISIN, acquired_on: "2023-01-02", quantity: "60", cost: "6030", segment: "EQUITY", account: "Groww", entered_on: "2024-09-02" },
    { instrument: ISIN, isin: ISIN, acquired_on: "2023-01-02", quantity: "40", cost: "4020", segment: "EQUITY", account: "Zerodha", entered_on: null },
  ],
};

function engine(replies: Record<string, object>) {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_u: string, init: RequestInit) => {
    const request = JSON.parse(String(init.body));
    calls.push(request);
    const reply = replies[request.method] ?? { result: request.method === "compute" ? REPORT : request.method === "unclassified_funds" ? { isins: [] } : {} };
    return new Response(JSON.stringify({ id: request.id, ...reply }));
  }));
  return calls;
}

function Probe() {
  return <output data-testid="state">{JSON.stringify(useSession().session)}</output>;
}
const state = () => JSON.parse(screen.getByTestId("state").textContent!);

async function renderHoldings(session: Partial<Session>) {
  await act(async () => render(
    <SessionProvider initial={{ ...EMPTY_SESSION, profileId: 1, settingsLoaded: true, ...session }}>
      <ReportProvider><HoldingsScreen /><Probe /></ReportProvider>
    </SessionProvider>,
  ));
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Holdings by account", () => {
  it("shows each lot's account and when moved shares arrived", async () => {
    engine({});
    await renderHoldings({ trades: [trade("Z:1", "Zerodha")] as never, accounts: ["Zerodha", "Groww"], transfers: [MOVE] });
    const lots = await screen.findByRole("table", { name: "Open lots" });
    expect(within(lots).getByText("Groww")).toBeTruthy();
    expect(within(lots).getByText("arrived 2024-09-02")).toBeTruthy();
    const moves = screen.getByRole("table", { name: "Transfers" });
    expect(within(moves).getByText("Zerodha")).toBeTruthy();
  });

  it("adds a transfer and shows the engine's refusal", async () => {
    const calls = engine({ ledger_add_transfer: { error: { type: "RequestError", message: "Zerodha held 40 of INE000A01012 on 2024-09-02, fewer than 60" } } });
    await renderHoldings({ trades: [trade("Z:1", "Zerodha")] as never, accounts: ["Zerodha", "Groww"] });
    const form = await screen.findByRole("form", { name: "Add a transfer" });
    const add = within(form).getByRole("button", { name: "Add transfer" }) as HTMLButtonElement;
    expect(add.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Date"), { target: { value: "2024-09-02" } });
    fireEvent.change(screen.getByLabelText("Shares"), { target: { value: ISIN } });
    fireEvent.change(screen.getByLabelText("Quantity"), { target: { value: "60" } });
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "Zerodha" } });
    expect(within(screen.getByLabelText("To")).queryByRole("option", { name: "Zerodha" })).toBeNull();
    fireEvent.change(screen.getByLabelText("To"), { target: { value: "Groww" } });
    await act(async () => fireEvent.click(add));
    expect(calls.find((c) => c.method === "ledger_add_transfer")!.params).toEqual({
      profile_id: 1, on: "2024-09-02", instrument: ISIN, quantity: "60", from_account: "Zerodha", to_account: "Groww" });
    expect((await screen.findByRole("alert")).textContent).toMatch(/Not saved: Zerodha held 40/);
    expect(state().working).toBe(false);
  });

  it("removes a transfer after confirming, and refreshes from the reply", async () => {
    const calls = engine({ ledger_remove_transfer: { result: { trades: [], batches: [], accounts: ["Zerodha", "Groww"], transfers: [] } } });
    await renderHoldings({ accounts: ["Zerodha", "Groww"], transfers: [MOVE] });
    fireEvent.click(await screen.findByRole("button", { name: "Remove the transfer on 2024-09-02" }));
    fireEvent.click(screen.getByRole("button", { name: "Keep" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove the transfer on 2024-09-02" }));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Yes, remove the transfer on 2024-09-02" })));
    expect(calls.find((c) => c.method === "ledger_remove_transfer")!.params).toEqual({ profile_id: 1, transfer_id: "TRANSFER:1" });
    expect(state().transfers).toEqual([]);
  });

  it("renames an account and puts unassigned trades into one", async () => {
    const calls = engine({
      ledger_rename_account: { result: { trades: [trade("O:1", null)], batches: [], accounts: ["Zerodha", "Groww India"], transfers: [] } },
      ledger_assign_account: { result: { trades: [trade("O:1", "Zerodha")], batches: [], accounts: ["Zerodha", "Groww India"], transfers: [] } },
    });
    await renderHoldings({ trades: [trade("O:1", null)] as never, accounts: ["Zerodha", "Groww"] });
    expect(screen.getByText(/1 share trade .* isn't in a demat account yet/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Rename Groww" }));
    fireEvent.change(screen.getByLabelText("New name for Groww"), { target: { value: "Groww India" } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Rename" })));
    expect(calls.find((c) => c.method === "ledger_rename_account")!.params).toEqual({ profile_id: 1, old: "Groww", new: "Groww India" });
    expect(within(screen.getByLabelText("Demat accounts")).getByRole("button", { name: "Rename Groww India" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Put them in"), { target: { value: "Zerodha" } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Move" })));
    expect(calls.find((c) => c.method === "ledger_assign_account")!.params).toEqual({ profile_id: 1, account: "Zerodha" });
    expect(screen.queryByText(/isn't in a demat account yet/)).toBeNull();
  });

  it("hides accounts and transfers for one account with nothing unassigned", async () => {
    engine({});
    await renderHoldings({ trades: [trade("Z:1", "Zerodha")] as never, accounts: ["Zerodha"] });
    await screen.findByRole("table", { name: "Open lots" });
    expect(screen.queryByLabelText("Transfers between your accounts")).toBeNull();
    expect(screen.queryByRole("columnheader", { name: "Account" })).toBeNull();
    expect(screen.getByLabelText("Demat accounts")).toBeTruthy();
  });
});

describe("Account on import and in missing history", () => {
  it("defaults the account to the broker and sends a typed one", async () => {
    const calls = engine({ ledger_import: { result: { files: [], source: null, format_confirmed: true, warnings: [], suggested_classes: {},
      scheme_names: {}, trades: [], batches: [], accounts: ["Zerodha", "Zerodha joint"], transfers: [] } } });
    render(<SessionProvider initial={{ ...EMPTY_SESSION, profileId: 1, settingsLoaded: true, accounts: ["Zerodha"] }}><ImportScreen /><Probe /></SessionProvider>);
    const account = screen.getByLabelText("Demat account these trades are in") as HTMLInputElement;
    expect(account.value).toBe("Zerodha");
    fireEvent.click(screen.getByLabelText("Angel One trade history"));
    expect((screen.getByLabelText("Demat account these trades are in") as HTMLInputElement).value).toBe("Angel One");
    fireEvent.click(screen.getByLabelText("Mutual fund CAS (PDF)"));
    expect(screen.queryByLabelText(/Demat account/)).toBeNull();
    fireEvent.click(screen.getByLabelText("Zerodha tradebook"));
    fireEvent.change(screen.getByLabelText("Demat account these trades are in"), { target: { value: "Zerodha joint" } });
    fireEvent.change(screen.getByLabelText(/Choose tradebook/), { target: { files: [new File(["a"], "z.csv")] } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Import files" })));
    expect(calls[0].params).toMatchObject({ broker: "zerodha", account: "Zerodha joint" });
    expect(state().accounts).toEqual(["Zerodha", "Zerodha joint"]);
  });

  it("names the account of a sale with no purchase and suggests a transfer", () => {
    const gap = { trade_id: "G:9", instrument: ISIN, isin: ISIN, sold_on: "2025-06-10", quantity: "15", price: "120",
      sale_value: "1800", segment: "EQUITY" as const, account: "Groww" };
    render(<SessionProvider initial={{ ...EMPTY_SESSION, profileId: 1, accounts: ["Zerodha", "Groww"] }}>
      <MissingHistory report={{ ...REPORT, missing_history: [gap], complete: false } as never} /><Probe /></SessionProvider>);
    expect(screen.getByText(/from Groww/)).toBeTruthy();
    expect(screen.getByText(/add a transfer on the Holdings screen/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Add the purchase" }));
    fireEvent.change(screen.getByLabelText("Purchase date"), { target: { value: "2024-01-02" } });
    fireEvent.change(screen.getByLabelText("Price per share (₹)"), { target: { value: "100" } });
    fireEvent.click(screen.getByRole("button", { name: "Add purchase" }));
    return waitFor(() => expect(state().manualBuys[0].trade.account).toBe("Groww"));
  });
});

describe("Accounts after review (QA)", () => {
  const buyFor = (account: string | null) => ({ how: "ipo" as const, forTrade: "S", trade: { ...trade("MANUAL:1", account) } });

  it("a rename moves hand-entered purchases to the new name", async () => {
    engine({ ledger_rename_account: { result: { trades: [], batches: [], accounts: ["Zerodha", "Groww India"], transfers: [] } } });
    await renderHoldings({ accounts: ["Zerodha", "Groww"], manualBuys: [buyFor("groww"), buyFor("Zerodha")] as never });
    fireEvent.click(screen.getByRole("button", { name: "Rename Groww" }));
    fireEvent.change(screen.getByLabelText("New name for Groww"), { target: { value: "  Groww   India " } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Rename" })));
    expect(state().manualBuys.map((b: { trade: { account: string } }) => b.trade.account)).toEqual(["Groww India", "Zerodha"]);
  });

  it("putting all unassigned trades in an account moves unassigned purchases too", async () => {
    engine({ ledger_assign_account: { result: { trades: [], batches: [], accounts: ["Zerodha"], transfers: [] } } });
    await renderHoldings({ trades: [trade("O:1", null)] as never, accounts: ["Zerodha"], manualBuys: [buyFor(null)] as never });
    expect(screen.getByText(/2 share trades .* aren't in a demat account yet/)).toBeTruthy();  // the purchase counts too
    fireEvent.change(screen.getByLabelText("Put them in"), { target: { value: " zerodha " } });  // stored as "Zerodha"
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Move" })));
    expect(state().manualBuys[0].trade.account).toBe("Zerodha");
  });

  it("puts unassigned trades into accounts one by one", async () => {
    const calls = engine({ ledger_assign_account: { result: { trades: [trade("O:2", null)], batches: [], accounts: ["Zerodha", "Groww"], transfers: [] } } });
    await renderHoldings({ trades: [trade("O:1", null), trade("O:2", null)] as never, accounts: ["Zerodha", "Groww"],
      manualBuys: [buyFor(null)] as never });
    expect(screen.getByRole("button", { name: "Move MANUAL:1" })).toBeTruthy();  // unassigned purchases are listed
    fireEvent.click(screen.getByText("Or put them in accounts one by one"));
    const move = screen.getByRole("button", { name: "Move O:1" }) as HTMLButtonElement;
    expect(move.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Account for O:1"), { target: { value: "Groww" } });
    await act(async () => fireEvent.click(move));
    expect(calls.find((c) => c.method === "ledger_assign_account")!.params).toEqual({ profile_id: 1, account: "Groww", trade_ids: ["O:1"] });
  });

  it("forgets a typed account when the person changes, and says where a file went", async () => {
    engine({ ledger_import: { result: { files: [{ name: "z.csv", added: 0, duplicates: 0, already_imported_on: "2026-10-10T10:00:00+00:00",
      imported_into: "Zerodha", conflicts: [], possible_duplicates: 0 }], source: null, format_confirmed: true, warnings: [],
      suggested_classes: {}, scheme_names: {}, trades: [], batches: [], accounts: ["Zerodha"], transfers: [] } } });
    function Switch() {
      const { update } = useSession();
      return <button type="button" onClick={() => update({ profileId: 2 })}>switch</button>;
    }
    render(<SessionProvider initial={{ ...EMPTY_SESSION, profileId: 1, settingsLoaded: true }}><ImportScreen /><Switch /></SessionProvider>);
    fireEvent.change(screen.getByLabelText("Demat account these trades are in"), { target: { value: "Groww" } });
    fireEvent.click(screen.getByRole("button", { name: "switch" }));
    expect((screen.getByLabelText("Demat account these trades are in") as HTMLInputElement).value).toBe("Zerodha");
    fireEvent.change(screen.getByLabelText(/Choose tradebook/), { target: { files: [new File(["a"], "z.csv")] } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Import files" })));
    expect(screen.getByText(/already imported on .* into Zerodha/)).toBeTruthy();
    expect(screen.getByText(/undo that import below and import it again/)).toBeTruthy();
  });
});
