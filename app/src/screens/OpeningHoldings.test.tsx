import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EMPTY_SESSION, SessionProvider, useSession } from "../state";
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


type Call = { method: string; params: Record<string, unknown> };
const LOT = { trade_id: "OPENING:INE000A01012:2016-04-01:1", trade_date: "2016-04-01", instrument: "INE000A01012",
  side: "BUY", quantity: "10", price: "100", charges: "0", stt: "0", segment: "EQUITY", executed_at: null };
const BATCH = { id: 4, kind: "opening", broker: "opening", file_name: "Entered by hand", file_sha256: null,
  imported_at: "2026-10-10T10:00:00+00:00", date_from: "2016-04-01", date_to: "2016-04-01", rows_read: 1,
  trades_added: 1, duplicates_skipped: 0 };

function engine(replies: Record<string, object>) {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_u: string, init: RequestInit) => {
    const request = JSON.parse(String(init.body));
    calls.push(request);
    return new Response(JSON.stringify({ id: request.id, ...(replies[request.method] ?? { result: {} }) }));
  }));
  return calls;
}

function Probe() {
  return <output data-testid="state">{JSON.stringify(useSession().session)}</output>;
}

const state = () => JSON.parse(screen.getByTestId("state").textContent!);

function renderOpening() {
  render(<SessionProvider initial={{ ...EMPTY_SESSION, profileId: 1, settingsLoaded: true }}><ImportScreen /><Probe /></SessionProvider>);
  fireEvent.click(screen.getByLabelText("Holdings from before your first tradebook"));
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Opening holdings", () => {
  it("saves lots entered by hand and shows them in the history", async () => {
    const calls = engine({ ledger_add_opening: { result: { added: 1, duplicates: 1, names: { INE000A01012: "SYNTH ALPHA" },
      trades: [LOT], batches: [BATCH], conflicts: [], warnings: ["row 1: fractional share quantity 1.5"] } } });
    renderOpening();
    expect(screen.queryByLabelText(/Password/)).toBeNull();
    const save = screen.getByRole("button", { name: "Save holdings" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("ISIN, lot 1"), { target: { value: "ine000a01012" } });
    expect(screen.getByText(/Each lot needs an ISIN, quantity, date and price/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Name, lot 1"), { target: { value: "SYNTH ALPHA" } });
    fireEvent.change(screen.getByLabelText("Quantity, lot 1"), { target: { value: "1,000" } });
    fireEvent.change(screen.getByLabelText("Bought on, lot 1"), { target: { value: "2016-04-01" } });
    fireEvent.change(screen.getByLabelText("Price, lot 1"), { target: { value: "100" } });
    fireEvent.change(screen.getByLabelText("How acquired, lot 1"), { target: { value: "ipo" } });
    fireEvent.click(screen.getByRole("button", { name: "Add another lot" }));
    expect(screen.getByLabelText("ISIN, lot 2")).toBeTruthy();  // a blank extra row is ignored
    fireEvent.click(screen.getByRole("button", { name: "Remove lot 2" }));
    fireEvent.click(screen.getByRole("button", { name: "Add another lot" }));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save holdings" })));
    expect(calls[0]).toMatchObject({ method: "ledger_add_opening", params: { profile_id: 1, rows: [
      { isin: "INE000A01012", name: "SYNTH ALPHA", quantity: "1000", buy_date: "2016-04-01", price: "100", how_acquired: "ipo",
        account: "", entered_on: "" }] } });
    expect(screen.getByText(/^Saved 1 holding; 1 already saved and skipped\. A lot with the same date.*Note: row 1: fractional share quantity 1\.5\.$/)).toBeTruthy();
    expect(state()).toMatchObject({ trades: [LOT], batches: [BATCH], names: { INE000A01012: "SYNTH ALPHA" }, working: false });
    expect(screen.getByRole("table", { name: "Import history" }).textContent).toMatch(/Entered by hand.*Opening holdings/);
    expect((screen.getByLabelText("ISIN, lot 1") as HTMLInputElement).value).toBe("");
  });

  it("shows the engine's reason when a lot is refused", async () => {
    engine({ ledger_add_opening: { error: { type: "ImportFormatError", message: "row 1: INE000A01011 isn't a valid Indian ISIN" } } });
    renderOpening();
    for (const [label, value] of [["ISIN, lot 1", "INE000A01011"], ["Quantity, lot 1", "1"], ["Bought on, lot 1", "2016-04-01"], ["Price, lot 1", "1"]]) {
      fireEvent.change(screen.getByLabelText(label), { target: { value } });
    }
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save holdings" })));
    expect((await screen.findByRole("alert")).textContent).toMatch(/Not saved: .*isn't a valid Indian ISIN/);
    expect((screen.getByLabelText("ISIN, lot 1") as HTMLInputElement).value).toBe("INE000A01011");  // kept to fix
  });

  it("downloads the template and imports a filled-in one as one file", async () => {
    vi.stubGlobal("URL", { ...URL, createObjectURL: vi.fn(() => "blob:x"), revokeObjectURL: vi.fn() });
    const saved: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) { saved.push(this.download); });
    const calls = engine({
      opening_template: { result: { name: "kosh-opening-holdings.csv", csv: "isin,quantity\n" } },
      ledger_import: { result: { files: [{ name: "o.csv", added: 1, duplicates: 0, already_imported_on: null, conflicts: [], possible_duplicates: 0 }],
        source: "Opening holdings", format_confirmed: true, warnings: [], suggested_classes: {}, scheme_names: {}, trades: [LOT], batches: [BATCH] } },
    });
    renderOpening();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Download the template" })));
    expect(saved).toEqual(["kosh-opening-holdings.csv"]);
    const input = screen.getByLabelText("Choose the filled-in template") as HTMLInputElement;
    expect(input.multiple).toBe(false);
    fireEvent.change(input, { target: { files: [new File(["isin"], "o.csv")] } });
    await importNow();
    await waitFor(() => expect(calls.at(-1)!.params).toMatchObject({ broker: "opening", profile_id: 1 }));
    expect(screen.getByText(/Imported 1 new trade from Opening holdings/)).toBeTruthy();
  });

  it("reports a template that can't be fetched", async () => {
    engine({ opening_template: { error: { type: "X", message: "engine stopped" } } });
    renderOpening();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Download the template" })));
    expect(screen.getByText("engine stopped")).toBeTruthy();
  });
});

describe("Opening holdings (QA)", () => {
  it("refuses a corrected lot and says how to fix it", async () => {
    engine({ ledger_add_opening: { result: { added: 0, duplicates: 0, names: {}, trades: [], batches: [], warnings: [],
      conflicts: [{ new: LOT, existing: LOT, reason: "same_id" }] } } });
    renderOpening();
    for (const [label, value] of [["ISIN, lot 1", "INE000A01012"], ["Quantity, lot 1", "10"], ["Bought on, lot 1", "2016-04-01"], ["Price, lot 1", "100"]]) {
      fireEvent.change(screen.getByLabelText(label), { target: { value } });
    }
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save holdings" })));
    expect((await screen.findByRole("alert")).textContent).toMatch(/1 lot\(s\) are already saved .* undo the earlier entry/);
    expect((screen.getByLabelText("ISIN, lot 1") as HTMLInputElement).value).toBe("INE000A01012");
  });

  it("forgets typed rows when the person changes", async () => {
    engine({});
    function Switch() {
      const { update } = useSession();
      return <button type="button" onClick={() => update({ profileId: 2 })}>switch</button>;
    }
    render(<SessionProvider initial={{ ...EMPTY_SESSION, profileId: 1, settingsLoaded: true }}><ImportScreen /><Switch /></SessionProvider>);
    fireEvent.click(screen.getByLabelText("Holdings from before your first tradebook"));
    fireEvent.change(screen.getByLabelText("ISIN, lot 1"), { target: { value: "INE000A01012" } });
    fireEvent.click(screen.getByRole("button", { name: "switch" }));
    expect((screen.getByLabelText("ISIN, lot 1") as HTMLInputElement).value).toBe("");
  });
});

describe("Opening holdings in accounts", () => {
  it("fills the only account in, and asks when moved shares arrived", async () => {
    const calls = engine({ ledger_add_opening: { result: { added: 1, duplicates: 0, names: {}, trades: [LOT], batches: [BATCH],
      conflicts: [], warnings: [] } } });
    render(<SessionProvider initial={{ ...EMPTY_SESSION, profileId: 1, settingsLoaded: true, accounts: ["Zerodha"] }}><ImportScreen /></SessionProvider>);
    fireEvent.click(screen.getByLabelText("Holdings from before your first tradebook"));
    expect((screen.getByLabelText("Demat account, lot 1") as HTMLInputElement).value).toBe("Zerodha");
    expect(screen.queryByLabelText("Arrived on, lot 1")).toBeNull();
    fireEvent.change(screen.getByLabelText("How acquired, lot 1"), { target: { value: "transfer" } });
    for (const [label, value] of [["ISIN, lot 1", "INE000A01012"], ["Quantity, lot 1", "10"], ["Bought on, lot 1", "2016-04-01"],
                                  ["Price, lot 1", "100"], ["Arrived on, lot 1", "2020-01-02"], ["Demat account, lot 1", "Groww"]]) {
      fireEvent.change(screen.getByLabelText(label), { target: { value } });
    }
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save holdings" })));
    expect(calls[0].params.rows).toEqual([expect.objectContaining({ how_acquired: "transfer", entered_on: "2020-01-02", account: "Groww" })]);
  });
});
