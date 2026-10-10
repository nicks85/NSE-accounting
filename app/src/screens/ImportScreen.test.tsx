import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EMPTY_SESSION, SessionProvider, useSession, type ImportBatch } from "../state";
import { ImportScreen } from "./ImportScreen";

/** Brief 0004: an import is a preview, then "Save" when the preview offers it. */
async function importNow() {
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Preview import" })));
  // Reading a chosen file is asynchronous: wait for the preview (or an error) to appear.
  await waitFor(() => expect(screen.queryByText("Check before saving") ?? screen.queryByRole("alert")).toBeTruthy());
  await waitFor(() => expect(screen.queryByText("Reading…")).toBeNull());
  const panel = screen.queryByLabelText("Import preview");
  const save = (panel && within(panel).queryByRole("button", { name: /^Save / })) as HTMLButtonElement | null;
  if (save && !save.disabled) await act(async () => fireEvent.click(save));
}


const TRADE = {
  trade_id: "ZERODHA:NSE:2025-05-01:1", trade_date: "2025-05-01", instrument: "INE000A01011",
  side: "BUY", quantity: "10", price: "100", charges: "0", stt: "0", segment: "EQUITY", executed_at: null,
};

function engineReplies(...replies: object[]) {
  const calls: { method: string; params: Record<string, unknown> }[] = [];
  let next = 0;
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init: RequestInit) => {
    const request = JSON.parse(String(init.body));
    calls.push(request);
    // A preview answers with the reply the save will get, without using it up.
    const reply = request.params.mode === "preview" ? replies[next] : replies[next++];
    return new Response(JSON.stringify({ id: request.id, ...reply }));
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
    const button = screen.getByRole("button", { name: "Preview import" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/Choose tradebook/), { target: { files: [file("tb.csv")] } });
    await importNow();
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
    await importNow();
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
    await importNow();
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
    await importNow();
    expect(await screen.findByText(/match a saved trade from another source/)).toBeTruthy();
    expect(screen.queryByText(/same trade number/)).toBeNull();
  });

  it("reloads the saved trades after a failed import", async () => {
    // The preview reads fine; saving fails (e.g. the disk), so the screen reloads the ledger.
    const calls: { method: string; params: Record<string, unknown> }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (_u: string, init: RequestInit) => {
      const request = JSON.parse(String(init.body));
      calls.push(request);
      const reply = request.method === "ledger_state" ? { result: { trades: [TRADE], batches: [BATCH] } }
        : request.params.mode === "preview" ? { result: imported() }
        : { error: { type: "LedgerError", message: "disk full" } };
      return new Response(JSON.stringify({ id: request.id, ...reply }));
    }));
    renderScreen();
    fireEvent.change(screen.getByLabelText(/Choose tradebook/), { target: { files: [file("bad.csv")] } });
    await importNow();
    await waitFor(() => expect(state().batches).toEqual([BATCH]));
    expect(calls.map((c) => c.method)).toEqual(["ledger_import", "ledger_import", "ledger_state"]);
    expect(screen.getByRole("alert").textContent).toMatch(/disk full/);
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
    await importNow();
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
    expect((screen.getByRole("button", { name: "Preview import" }) as HTMLButtonElement).disabled).toBe(true);
    for (const [label, value] of [["Trade date *", "Date"], ["Buy / sell *", "Type"], ["Quantity *", "Qty"],
                                  ["Price *", "Price"], ["Trade / order number *", "Id"], ["ISIN (shares)", "ISIN"],
                                  ["Exchange", "Exch"]]) {
      fireEvent.change(screen.getByLabelText(label), { target: { value } });
    }
    fireEvent.change(screen.getByLabelText("Broker name"), { target: { value: "Groww" } });
    fireEvent.change(screen.getByLabelText(/Choose tradebook/), { target: { files: [file("g.csv")] } });
    await importNow();
    expect((screen.getByRole("button", { name: "Nothing to save" }) as HTMLButtonElement).disabled).toBe(true);
    expect(calls[0].params).toMatchObject({ broker: "mapped", key: "GROWW", source: "Groww (mapped)", mode: "preview",
                                            mapping: { trade_date: "Date" } });
  });

  it("needs a password for a CAS and merges suggested classes without overriding", async () => {
    engineReplies({ result: imported({ source: "CAS", suggested_classes: { INF000E01011: "equity-oriented" },
      scheme_names: { INF000E01011: "Fund" } }) });
    renderScreen();
    fireEvent.click(screen.getByLabelText(/Mutual fund CAS/));
    fireEvent.change(screen.getByLabelText(/Choose the CAS PDF/), { target: { files: [file("cas.pdf")] } });
    const button = screen.getByRole("button", { name: "Preview import" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/Password/), { target: { value: "ABCDE1234F" } });
    expect(button.disabled).toBe(false);
    await importNow();
    await waitFor(() => expect(state().fundClasses).toEqual({ INF000E01011: "equity-oriented" }));
    expect(state().names).toEqual({ INF000E01011: "Fund" });
  });

  it("shows engine errors, and refuses to import before the ledger has loaded", async () => {
    engineReplies({ error: { type: "ImportFormatError", message: "row 3: bad price" } });
    renderScreen();
    fireEvent.change(screen.getByLabelText(/Choose tradebook/), { target: { files: [file("x.csv")] } });
    await importNow();
    expect((await screen.findByRole("alert")).textContent).toMatch(/row 3: bad price/);
    cleanup();
    renderScreen(null);
    fireEvent.change(screen.getByLabelText(/Choose tradebook/), { target: { files: [file("x.csv")] } });
    await importNow();
    expect((await screen.findByRole("alert")).textContent).toMatch(/hasn't loaded yet/);
  });
});

describe("ImportScreen state (QA)", () => {
  it("merges a slow import into the latest state", async () => {
    let release: () => void = () => {};
    vi.stubGlobal("fetch", vi.fn((_u: string, init: RequestInit) => new Promise<Response>((resolve) => {
      const request = JSON.parse(String(init.body));
      const reply = () => resolve(new Response(JSON.stringify({ id: request.id, result: imported({
        suggested_classes: { INF000E01011: "specified" } }) })));
      if (request.params.mode === "preview") reply(); else release = reply;  // the save is slow
    })));
    function Changer() {
      const { update } = useSession();
      return <button type="button" onClick={() => update({ fmv2018: { INE000A01011: "800" } })}>change</button>;
    }
    render(<SessionProvider initial={{ ...EMPTY_SESSION, profileId: 1 }}><ImportScreen /><Changer /><Probe /></SessionProvider>);
    fireEvent.change(screen.getByLabelText(/Choose tradebook/), { target: { files: [file("tb.csv")] } });
    await importNow();
    fireEvent.click(screen.getByRole("button", { name: "change" }));  // edit while the import is running
    await act(async () => release());
    await screen.findByText(/Imported 1 new trade/);
    expect(state().fmv2018).toEqual({ INE000A01011: "800" });  // not overwritten
    expect(state().unconfirmed).toEqual(["INF000E01011"]);
  });
});

describe("Replies after switching person (QA)", () => {
  it("never applies an import or undo reply to another person's session", async () => {
    const releases: (() => void)[] = [];
    vi.stubGlobal("fetch", vi.fn((_u: string, init: RequestInit) => new Promise<Response>((resolve) => {
      const request = JSON.parse(String(init.body));
      const result = request.method === "ledger_import" ? imported({ suggested_classes: { INF000E01011: "other" } })
        : { removed: 1, trades: [], batches: [] };
      const reply = () => resolve(new Response(JSON.stringify({ id: request.id, result })));
      if (request.params.mode === "preview") reply(); else releases.push(reply);  // save and undo wait
    })));
    function Switch() {
      const { update } = useSession();
      return <button type="button" onClick={() => update({ profileId: 2, trades: [], batches: [], manualBuys: [], excluded: ["KEEP"] })}>switch</button>;
    }
    render(<SessionProvider initial={{ ...EMPTY_SESSION, profileId: 1, trades: [TRADE as never], batches: [BATCH] }}>
      <ImportScreen /><Switch /><Probe /></SessionProvider>);
    fireEvent.change(screen.getByLabelText(/Choose tradebook/), { target: { files: [file("tb.csv")] } });
    await importNow();
    fireEvent.click(screen.getByRole("button", { name: "Undo import of tb.csv" }));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Remove 1 trade" })));
    expect(state().working).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "switch" }));
    await act(async () => releases.forEach((release) => release()));
    await waitFor(() => expect(state().working).toBe(false));
    expect(state()).toMatchObject({ profileId: 2, trades: [], batches: [], fundClasses: {}, excluded: ["KEEP"] });
  });
});

describe("Import preview (brief 0004)", () => {
  const PREVIEWED = {
    name: "tb.csv", sha256: "aa", added: 2, duplicates: 1, already_imported_on: null, conflicts: [], possible_duplicates: 0,
    account: "Zerodha", trades: 3, buys: 2, sells: 1, date_from: "2025-04-02", date_to: "2025-05-02",
    charges: "12.40", stt: "3", stated_charges: "15.40", charges_check: "matches", by_name: [], notes: ["row 9: segment 'X' skipped"],
  };
  const OLD = { name: "old.csv", sha256: "bb", added: 0, duplicates: 0, already_imported_on: "2026-10-01T10:00:00+00:00",
    imported_into: "Zerodha", conflicts: [], possible_duplicates: 0 };

  it("shows what saving would do and stores nothing until Save", async () => {
    const calls = engineReplies({ result: imported({ files: [PREVIEWED, OLD], format_confirmed: true }) });
    renderScreen();
    fireEvent.change(screen.getByLabelText(/Choose tradebook/), { target: { files: [file("tb.csv"), file("old.csv")] } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Preview import" })));
    const panel = await screen.findByRole("region", { name: "Import preview" });
    expect(within(panel).getByText(/3 trades \(2 buys, 1 sell\) from 2025-04-02 to 2025-05-02, into the demat account Zerodha/)).toBeTruthy();
    expect(within(panel).getByText(/already saved and will be skipped/)).toBeTruthy();
    expect(within(panel).getByText(/matches the file’s own total of ₹15.40/)).toBeTruthy();
    expect(within(panel).getByText(/old.csv: won’t be saved/)).toBeTruthy();
    expect(within(panel).getByText(/1 note from the importer/)).toBeTruthy();
    expect(state().trades).toEqual([]);  // nothing stored yet
    expect(calls[0].params.mode).toBe("preview");
    await waitFor(() => expect(screen.queryByText("Reading…")).toBeNull());
    await act(async () => fireEvent.click(within(panel).getByRole("button", { name: "Save 1 of 2 files" })));
    expect(calls[1].params).toMatchObject({ mode: "save", expected: ["aa", "bb"] });
    expect((calls[1].params.files as unknown[]).length).toBe(2);  // the same bytes as previewed
    expect(screen.queryByRole("region", { name: "Import preview" })).toBeNull();
  });

  it("warns when charges don't match the file's summary, and lists companies without an ISIN", async () => {
    engineReplies({ result: imported({ files: [{ ...PREVIEWED, charges_check: "differs", stated_charges: "50",
      by_name: ["NAME:SYNTH ALPHA"] }, { ...PREVIEWED, name: "z.csv", charges_check: "none", stated_charges: null }] }) });
    renderScreen();
    fireEvent.change(screen.getByLabelText(/Choose tradebook/), { target: { files: [file("tb.csv")] } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Preview import" })));
    const panel = await screen.findByRole("region", { name: "Import preview" });
    expect(within(panel).getByText(/The file’s own total is ₹50.00: check the file is complete/)).toBeTruthy();
    expect(within(panel).getByText(/1 company without an ISIN, imported by name: SYNTH ALPHA/)).toBeTruthy();
    expect(within(panel).getByText(/no charges summary to check against/)).toBeTruthy();
    expect(within(panel).getByText(/isn’t confirmed against real files/)).toBeTruthy();
  });

  it("is dropped by Cancel, and by changing the account", async () => {
    engineReplies({ result: imported({ files: [PREVIEWED] }) }, { result: imported({ files: [PREVIEWED] }) });
    renderScreen();
    fireEvent.change(screen.getByLabelText(/Choose tradebook/), { target: { files: [file("tb.csv")] } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Preview import" })));
    fireEvent.click(within(await screen.findByRole("region", { name: "Import preview" })).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("region", { name: "Import preview" })).toBeNull();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Preview import" })));
    await screen.findByRole("region", { name: "Import preview" });
    fireEvent.change(screen.getByLabelText("Demat account these trades are in"), { target: { value: "Groww" } });
    expect(screen.queryByRole("region", { name: "Import preview" })).toBeNull();
  });

  it("shows an engine refusal at save (files changed since the preview)", async () => {
    const calls: { method: string; params: Record<string, unknown> }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (_u: string, init: RequestInit) => {
      const request = JSON.parse(String(init.body));
      calls.push(request);
      const reply = request.method === "ledger_state" ? { result: { trades: [], batches: [] } }
        : request.params.mode === "preview" ? { result: imported({ files: [PREVIEWED] }) }
        : { error: { type: "RequestError", message: "the files changed since the preview; preview them again before saving" } };
      return new Response(JSON.stringify({ id: request.id, ...reply }));
    }));
    renderScreen();
    fireEvent.change(screen.getByLabelText(/Choose tradebook/), { target: { files: [file("tb.csv")] } });
    await importNow();
    expect((await screen.findByRole("alert")).textContent).toMatch(/changed since the preview/);
  });
});

describe("Import preview after review (QA)", () => {
  const FILE = { name: "tb.csv", sha256: "aa", added: 2, duplicates: 0, already_imported_on: null, conflicts: [], possible_duplicates: 0 };

  it("locks the inputs while the preview is being read", async () => {
    let release: () => void = () => {};
    vi.stubGlobal("fetch", vi.fn((_u: string, init: RequestInit) => new Promise<Response>((resolve) => {
      const request = JSON.parse(String(init.body));
      release = () => resolve(new Response(JSON.stringify({ id: request.id, result: imported({ files: [FILE] }) })));
    })));
    renderScreen();
    fireEvent.change(screen.getByLabelText(/Choose tradebook/), { target: { files: [file("tb.csv")] } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Preview import" })));
    expect(screen.getByLabelText("Demat account these trades are in").matches(":disabled")).toBe(true);
    await act(async () => release());
    expect(screen.getByLabelText("Demat account these trades are in").matches(":disabled")).toBe(false);
  });

  it("says when the save differs from the preview", async () => {
    const calls: { method: string; params: Record<string, unknown> }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (_u: string, init: RequestInit) => {
      const request = JSON.parse(String(init.body));
      calls.push(request);
      const files = request.params.mode === "preview" ? [FILE] : [{ ...FILE, added: 1, duplicates: 1 }];
      return new Response(JSON.stringify({ id: request.id, result: imported({ files }) }));
    }));
    renderScreen();
    fireEvent.change(screen.getByLabelText(/Choose tradebook/), { target: { files: [file("tb.csv")] } });
    await importNow();
    expect(await screen.findByText(/numbers differ from the preview/)).toBeTruthy();
    expect((calls[1].params.files as { data_base64: string }[])[0].data_base64).toBe(btoa("a,b"));  // read again
  });
});
