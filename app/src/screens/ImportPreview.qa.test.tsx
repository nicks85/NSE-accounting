import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EMPTY_SESSION, SessionProvider, useSession } from "../state";
import { ImportScreen } from "./ImportScreen";

// QA review of brief 0004 E: a preview must never outlive the inputs it was made for.
// These were known bugs in the first review (`it.fails`); fixed since.

const PREVIEWED = {
  name: "tb.csv", sha256: "aa", added: 1, duplicates: 0, already_imported_on: null, conflicts: [], possible_duplicates: 0,
  account: "Zerodha", trades: 1, buys: 1, sells: 0, date_from: "2025-04-02", date_to: "2025-04-02",
  charges: "0", stt: "0", stated_charges: null, charges_check: "none", by_name: [], notes: [],
};
const reply = { files: [PREVIEWED], source: "Zerodha", format_confirmed: true, warnings: [], suggested_classes: {},
  scheme_names: {}, trades: [], batches: [], saved: false };

function Restore() {
  const { update } = useSession();
  return <button type="button" onClick={() => update((s) => ({ revision: (s.revision ?? 0) + 1 }))}>restore</button>;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Import preview staleness (QA)", () => {
  it("drops a preview whose reply arrives after the account was changed", async () => {
    let release: () => void = () => {};
    vi.stubGlobal("fetch", vi.fn((_u: string, init: RequestInit) => new Promise<Response>((resolve) => {
      const request = JSON.parse(String(init.body));
      release = () => resolve(new Response(JSON.stringify({ id: request.id, result: reply })));
    })));
    render(<SessionProvider initial={{ ...EMPTY_SESSION, profileId: 1 }}><ImportScreen /></SessionProvider>);
    fireEvent.change(screen.getByLabelText(/Choose tradebook/), { target: { files: [new File(["a"], "tb.csv")] } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Preview import" })));
    // The account box stays editable while the preview is being read.
    fireEvent.change(screen.getByLabelText("Demat account these trades are in"), { target: { value: "Groww" } });
    await act(async () => release());
    // The preview was made for "Zerodha"; Save would put the trades there while the form says Groww.
    expect(screen.queryByRole("region", { name: "Import preview" })).toBeNull();
  });

  it("drops the preview when a backup is restored (its counts belong to the old ledger)", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_u: string, init: RequestInit) =>
      new Response(JSON.stringify({ id: JSON.parse(String(init.body)).id, result: reply }))));
    render(<SessionProvider initial={{ ...EMPTY_SESSION, profileId: 1 }}><ImportScreen /><Restore /></SessionProvider>);
    fireEvent.change(screen.getByLabelText(/Choose tradebook/), { target: { files: [new File(["a"], "tb.csv")] } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Preview import" })));
    await screen.findByRole("region", { name: "Import preview" });
    fireEvent.click(screen.getByRole("button", { name: "restore" }));
    expect(screen.queryByRole("region", { name: "Import preview" })).toBeNull();
  });
});
