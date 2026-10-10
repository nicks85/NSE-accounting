import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EMPTY_SESSION, LedgerLoader, SessionProvider, useSession, type ImportBatch } from "../state";
import { ImportScreen } from "./ImportScreen";

const TRADE = {
  trade_id: "ZERODHA:NSE:2025-05-01:1", trade_date: "2025-05-01", instrument: "INE000A01011",
  side: "BUY", quantity: "10", price: "100", charges: "0", stt: "0", segment: "EQUITY", executed_at: null,
};

function engineReplies(...replies: object[]) {
  const calls: { method: string; params: Record<string, unknown> }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init: RequestInit) => {
    const request = JSON.parse(String(init.body));
    calls.push(request);
    return new Response(JSON.stringify({ id: request.id, ...replies[calls.length - 1] }));
  }));
  return calls;
}

function Probe() {
  const { session } = useSession();
  return <output data-testid="state">{JSON.stringify(session)}</output>;
}

function renderScreen(profileId: number | null = 1) {
  render(<SessionProvider initial={{ ...EMPTY_SESSION, profileId }}><ImportScreen /><Probe /></SessionProvider>);
}

const state = () => JSON.parse(screen.getByTestId("state").textContent!);
const file = (name: string, text = "a,b") => new File([text], name, { type: "text/csv" });

const BATCH: ImportBatch = { id: 3, kind: "tradebook", broker: "zerodha", file_name: "tb.csv", file_sha256: "ab",
  imported_at: "2026-10-10T10:00:00+00:00", date_from: "2025-05-01", date_to: "2025-05-01", rows_read: 1,
  trades_added: 1, duplicates_skipped: 0 };

function imported(overrides: object = {}) {
  return { files: [{ name: "tb.csv", added: 1, duplicates: 0, already_imported_on: null, conflicts: [], possible_duplicates: 0 }],
    source: "Zerodha tradebook (CSV)", format_confirmed: false, warnings: ["note one"], suggested_classes: {},
    scheme_names: {}, trades: [TRADE], batches: [BATCH], ...overrides };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ImportScreen", () => {
  it("imports into the ledger, then reports duplicates and a file already imported", async () => {
    const calls = engineReplies(
      { result: imported() },
      { result: imported({ files: [
        { name: "tb.csv", added: 0, duplicates: 0, already_imported_on: "2026-10-10T10:00:00+00:00", conflicts: [] },
        { name: "all.csv", added: 2, duplicates: 1, already_imported_on: null, conflicts: [], possible_duplicates: 1 }] }) },
    );
    renderScreen();
    const button = screen.getByRole("button", { name: "Import files" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/Choose tradebook/), { target: { files: [file("tb.csv")] } });
    await act(async () => fireEvent.click(button));
    await screen.findByText(/Imported 1 new trade from Zerodha/);
    expect(screen.getByText(/isn't confirmed/)).toBeTruthy();
    expect(calls[0].method).toBe("ledger_import");
    expect(calls[0].params).toMatchObject({ broker: "zerodha", profile_id: 1 });
    expect((calls[0].params.files as { data_base64: string }[])[0].data_base64).toBe(btoa("a,b"));
    expect(state().trades).toHaveLength(1);
    expect(state().batches).toEqual([BATCH]);
    expect(screen.getByText(/1 trades — 1 shares/)).toBeTruthy();
    const history = screen.getByRole("table", { name: "Import history" });
    expect(within(history).getByText("Zerodha")).toBeTruthy();

    fireEvent.change(screen.getByLabelText(/Choose tradebook/), { target: { files: [file("tb.csv"), file("all.csv")] } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Import files" })));
    expect(await screen.findByText(/tb.csv: already imported on/)).toBeTruthy();
    expect(screen.getByText(/all.csv: 2 new trades, 1 already in your ledger and skipped/)).toBeTruthy();
    expect(screen.getByText(/1 of these match a trade saved from another source/)).toBeTruthy();
  });

  it("shows conflicts side by side and stores nothing from that file", async () => {
    const changed = { ...TRADE, price: "101" };
    engineReplies({ result: imported({ files: [{ name: "b.csv", added: 0, duplicates: 0, already_imported_on: null,
      conflicts: [{ new: changed, existing: TRADE, reason: "same_id" }] }], trades: [], batches: [] }) });
    renderScreen();
    fireEvent.change(screen.getByLabelText(/Choose tradebook/), { target: { files: [file("b.csv")] } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Import files" })));
    const table = await screen.findByRole("table", { name: "Conflicts in b.csv" });
    expect(within(table).getByText("101")).toBeTruthy();
    expect(within(table).getByText("100")).toBeTruthy();
    expect(screen.getByText(/b.csv: not imported/)).toBeTruthy();
    expect(screen.getByText(/same trade number as one already saved/)).toBeTruthy();
    expect(screen.queryByText(/another source/)).toBeNull();
    expect(screen.queryByText(/isn't confirmed/)).toBeNull();
  });

  it("explains trades that look imported before under another broker name", async () => {
    engineReplies({ result: imported({ files: [{ name: "g2.csv", added: 0, duplicates: 0, already_imported_on: null,
      conflicts: [{ new: { ...TRADE, trade_id: "GROWWINDIA:1" }, existing: TRADE, reason: "other_source" }] }] }) });
    renderScreen();
    fireEvent.change(screen.getByLabelText(/Choose tradebook/), { target: { files: [file("g2.csv")] } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Import files" })));
    expect(await screen.findByText(/match a saved trade from another source/)).toBeTruthy();
    expect(screen.queryByText(/same trade number/)).toBeNull();
  });

  it("reloads the saved trades after a failed import", async () => {
    const calls = engineReplies({ error: { type: "ImportFormatError", message: "bad.csv row 2" } },
                                { result: { trades: [TRADE], batches: [BATCH] } });
    renderScreen();
    fireEvent.change(screen.getByLabelText(/Choose tradebook/), { target: { files: [file("bad.csv")] } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Import files" })));
    await waitFor(() => expect(state().batches).toEqual([BATCH]));
    expect(calls[1]).toMatchObject({ method: "ledger_state", params: { profile_id: 1 } });
    expect(screen.getByRole("alert").textContent).toMatch(/bad.csv row 2/);
  });

  it("undoes an import after a second click and forgets what belonged to its sales", async () => {
    const calls = engineReplies({ result: { removed: 1, trades: [], batches: [] } });
    const manual = { how: "bought" as const, forTrade: TRADE.trade_id, trade: { ...TRADE, trade_id: "MANUAL:1" } as never };
    render(<SessionProvider initial={{ ...EMPTY_SESSION, profileId: 1, trades: [TRADE as never], batches: [BATCH],
                                       manualBuys: [manual], excluded: [`${TRADE.trade_id}#delivery`, "OTHER"] }}>
      <ImportScreen /><Probe /></SessionProvider>);
    fireEvent.click(screen.getByRole("button", { name: "Undo import of tb.csv" }));
    fireEvent.click(screen.getByRole("button", { name: "Keep" }));
    expect(calls).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Undo import of tb.csv" }));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Remove 1 trade" })));
    expect(calls[0]).toMatchObject({ method: "ledger_undo", params: { profile_id: 1, batch_id: 3 } });
    expect(state()).toMatchObject({ trades: [], manualBuys: [], excluded: [] });
    expect(screen.getByText("Nothing imported yet.")).toBeTruthy();
  });

  it("reports a failed undo and reloads the ledger", async () => {
    const calls = engineReplies({ error: { type: "LedgerError", message: "no import 3 for this profile" } },
                                { error: { type: "LedgerError", message: "still busy" } });
    render(<SessionProvider initial={{ ...EMPTY_SESSION, profileId: 1, batches: [{ ...BATCH, broker: null, file_name: null }] }}>
      <ImportScreen /></SessionProvider>);
    fireEvent.click(screen.getByRole("button", { name: "Undo import of 3" }));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Remove 1 trade" })));
    expect((await screen.findByRole("alert")).textContent).toMatch(/no import 3/);
    expect(calls[1].method).toBe("ledger_state");
  });

  it("imports an Angel One file in one step and shows company names", async () => {
    const calls = engineReplies({ result: imported({ source: "Angel One trade history", format_confirmed: true,
      scheme_names: { "NAME:SYNTH ALPHA": "SYNTH ALPHA" } }) });
    renderScreen();
    fireEvent.click(screen.getByLabelText("Angel One trade history"));
    fireEvent.change(screen.getByLabelText(/Choose tradebook/), { target: { files: [file("t.xlsx")] } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Import files" })));
    await screen.findByText(/Imported 1 new trade from Angel One/);
    expect(screen.queryByText(/isn't confirmed/)).toBeNull();
    expect(calls[0].params.broker).toBe("angelone");
    expect(state().names).toEqual({ "NAME:SYNTH ALPHA": "SYNTH ALPHA" });
  });

  it("sends the column mapping for Groww/other brokers", async () => {
    const calls = engineReplies({ result: imported({ files: [], source: null, trades: [], batches: [] }) });
    renderScreen();
    fireEvent.click(screen.getByLabelText(/Groww or other/));
    fireEvent.change(screen.getByLabelText(/Choose tradebook/), { target: { files: [file("g.csv")] } });
    expect(screen.getByText(/Still needed/).textContent).toMatch(/Trade date.*ISIN or contract symbol.*exchange or segment/);
    expect((screen.getByRole("button", { name: "Import files" }) as HTMLButtonElement).disabled).toBe(true);
    for (const [label, value] of [["Trade date *", "Date"], ["Buy / sell *", "Type"], ["Quantity *", "Qty"],
                                  ["Price *", "Price"], ["Trade / order number *", "Id"], ["ISIN (shares)", "ISIN"],
                                  ["Exchange", "Exch"]]) {
      fireEvent.change(screen.getByLabelText(label), { target: { value } });
    }
    fireEvent.change(screen.getByLabelText("Broker name"), { target: { value: "Groww" } });
    fireEvent.change(screen.getByLabelText(/Choose tradebook/), { target: { files: [file("g.csv")] } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Import files" })));
    await screen.findByText(/Imported 0 new trades/);
    expect(calls[0].params).toMatchObject({ broker: "mapped", key: "GROWW", source: "Groww (mapped)",
                                            mapping: { trade_date: "Date" } });
  });

  it("needs a password for a CAS and merges suggested classes without overriding", async () => {
    engineReplies({ result: imported({ source: "CAS", suggested_classes: { INF000E01011: "equity-oriented" },
      scheme_names: { INF000E01011: "Fund" } }) });
    renderScreen();
    fireEvent.click(screen.getByLabelText(/Mutual fund CAS/));
    fireEvent.change(screen.getByLabelText(/Choose the CAS PDF/), { target: { files: [file("cas.pdf")] } });
    const button = screen.getByRole("button", { name: "Import files" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/Password/), { target: { value: "ABCDE1234F" } });
    expect(button.disabled).toBe(false);
    await act(async () => fireEvent.click(button));
    await waitFor(() => expect(state().fundClasses).toEqual({ INF000E01011: "equity-oriented" }));
    expect(state().names).toEqual({ INF000E01011: "Fund" });
  });

  it("shows engine errors, and refuses to import before the ledger has loaded", async () => {
    engineReplies({ error: { type: "ImportFormatError", message: "row 3: bad price" } });
    renderScreen();
    fireEvent.change(screen.getByLabelText(/Choose tradebook/), { target: { files: [file("x.csv")] } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Import files" })));
    expect((await screen.findByRole("alert")).textContent).toMatch(/row 3: bad price/);
    cleanup();
    renderScreen(null);
    fireEvent.change(screen.getByLabelText(/Choose tradebook/), { target: { files: [file("x.csv")] } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Import files" })));
    expect((await screen.findByRole("alert")).textContent).toMatch(/hasn't loaded yet/);
  });
});

describe("ImportScreen state (QA)", () => {
  it("merges a slow import into the latest state", async () => {
    let release: () => void = () => {};
    vi.stubGlobal("fetch", vi.fn((_u: string, init: RequestInit) => new Promise<Response>((resolve) => {
      const id = JSON.parse(String(init.body)).id;
      release = () => resolve(new Response(JSON.stringify({ id, result: imported({
        suggested_classes: { INF000E01011: "specified" } }) })));
    })));
    function Changer() {
      const { update } = useSession();
      return <button type="button" onClick={() => update({ fmv2018: { INE000A01011: "800" } })}>change</button>;
    }
    render(<SessionProvider initial={{ ...EMPTY_SESSION, profileId: 1 }}><ImportScreen /><Changer /><Probe /></SessionProvider>);
    fireEvent.change(screen.getByLabelText(/Choose tradebook/), { target: { files: [file("tb.csv")] } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Import files" })));
    fireEvent.click(screen.getByRole("button", { name: "change" }));  // edit while the import is running
    await act(async () => release());
    await screen.findByText(/Imported 1 new trade/);
    expect(state().fmv2018).toEqual({ INE000A01011: "800" });  // not overwritten
    expect(state().unconfirmed).toEqual(["INF000E01011"]);
  });
});

describe("LedgerLoader", () => {
  it("opens the named profile and loads its trades and history", async () => {
    localStorage.setItem("kosh.profile", "Synthetic");
    const calls = engineReplies({ result: { profile: { id: 4, name: "Synthetic" }, trades: [TRADE], batches: [BATCH] } });
    await act(async () => render(<SessionProvider><LedgerLoader /><Probe /></SessionProvider>));
    expect(calls[0]).toMatchObject({ method: "ledger_profile", params: { name: "Synthetic" } });
    await waitFor(() => expect(state()).toMatchObject({ profileId: 4, trades: [TRADE], batches: [BATCH], ledgerError: null }));
    localStorage.removeItem("kosh.profile");
  });

  it("uses the default profile and reports a ledger that can't be opened", async () => {
    const calls = engineReplies({ error: { type: "LedgerError", message: "made by a newer version of Kosh" } });
    await act(async () => render(<SessionProvider><LedgerLoader /><ImportScreen /><Probe /></SessionProvider>));
    expect(calls[0].params).toEqual({ name: "Me" });
    expect((await screen.findByRole("alert")).textContent).toMatch(/couldn't be opened: made by a newer version/);
  });
});
