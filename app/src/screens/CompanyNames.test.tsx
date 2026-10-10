import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EMPTY_SESSION, LedgerLoader, SessionProvider, useSession, type Session } from "../state";
import { CompanyNames } from "./CompanyNames";

type Call = { method: string; params: Record<string, unknown> };
const SETTINGS = { fund_classes: {}, unconfirmed: [], fmv_2018: {}, names: { INE000A01012: "SYNTH ALPHA" }, brought_forward: [],
  manual_buys: [], excluded: [], filed_on_time: {} };

function engine(reply: object) {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_u: string, init: RequestInit) => {
    const request = JSON.parse(String(init.body));
    calls.push(request);
    return new Response(JSON.stringify({ id: request.id, ...reply }));
  }));
  return calls;
}

function Probe() {
  return <output data-testid="state">{JSON.stringify(useSession().session)}</output>;
}
const state = () => JSON.parse(screen.getByTestId("state").textContent!);

function renderNames(session: Partial<Session>) {
  render(<SessionProvider initial={{ ...EMPTY_SESSION, profileId: 1, settingsLoaded: true, ...session }}><CompanyNames /><Probe /></SessionProvider>);
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Companies imported by name (brief 0007)", () => {
  it("saves a typed ISIN and takes the moved settings from the reply", async () => {
    const calls = engine({ result: { trades: [], batches: [], names_unmapped: [], names_mapped: [{ name: "SYNTH ALPHA", isin: "INE000A01012" }],
      settings: SETTINGS } });
    renderNames({ namesUnmapped: [{ name: "SYNTH ALPHA", trades: 2 }], names: { "NAME:SYNTH ALPHA": "SYNTH ALPHA" } });
    const save = screen.getByRole("button", { name: "Save the ISIN for SYNTH ALPHA" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    const input = screen.getByLabelText("ISIN for SYNTH ALPHA");
    fireEvent.change(input, { target: { value: "ine000a0101" } });
    expect(save.disabled).toBe(true);  // too short, and it says why
    expect(screen.getByText(/An ISIN is 12 characters.*\(11 now\)/)).toBeTruthy();
    expect(input.getAttribute("aria-invalid")).toBe("true");
    fireEvent.change(input, { target: { value: " ine 000a 01012 " } });  // pasted with spaces
    expect(save.disabled).toBe(false);
    expect(screen.queryByText(/An ISIN is 12 characters/)).toBeNull();
    await act(async () => fireEvent.click(save));
    expect(calls[0]).toMatchObject({ method: "ledger_map_name", params: { profile_id: 1, name: "SYNTH ALPHA", isin: "INE000A01012" } });
    expect(state()).toMatchObject({ namesUnmapped: [], names: { INE000A01012: "SYNTH ALPHA" }, working: false });
    expect(screen.getByText(/SYNTH ALPHA → INE000A01012/)).toBeTruthy();
  });

  it("says when the name and the ISIN had different prices", async () => {
    engine({ result: { trades: [], batches: [], names_unmapped: [], names_mapped: [{ name: "SYNTH ALPHA", isin: "INE000A01012" }],
      settings: SETTINGS, notes: ["SYNTH ALPHA had the 31-Jan-2018 price 50, but INE000A01012 already had 60; 60 is kept for both. Check which is right."] } });
    renderNames({ namesUnmapped: [{ name: "SYNTH ALPHA", trades: 2 }] });
    fireEvent.change(screen.getByLabelText("ISIN for SYNTH ALPHA"), { target: { value: "INE000A01012" } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save the ISIN for SYNTH ALPHA" })));
    expect(screen.getByText(/60 is kept for both/).closest("[role=status]")).toBeTruthy();
  });

  it("undoes a match and shows the engine's refusal", async () => {
    const calls = engine({ error: { type: "LedgerError", message: "'INE000A01011' isn't a valid Indian ISIN (check for a typo)" } });
    renderNames({ namesUnmapped: [{ name: "B", trades: 1 }], namesMapped: [{ name: "A", isin: "INE000A01012" }] });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Undo the ISIN for A" })));
    expect(calls[0]).toMatchObject({ method: "ledger_unmap_name", params: { name: "A" } });
    expect((await screen.findByRole("alert")).textContent).toMatch(/valid Indian ISIN/);
  });

  it("waits for a queued settings save, and a save queued meanwhile can't undo the match", async () => {
    const calls: Call[] = [];
    const pending: (() => void)[] = [];
    const named = { ...SETTINGS, names: { "NAME:SYNTH ALPHA": "SYNTH ALPHA" } };
    vi.stubGlobal("fetch", vi.fn((_u: string, init: RequestInit) => {
      const request = JSON.parse(String(init.body));
      calls.push(request);
      const reply = request.method === "ledger_profile"
        ? { result: { profile: { id: 1, name: "Synthetic" }, profiles: [{ id: 1, name: "Synthetic" }], trades: [], batches: [],
          names_unmapped: [{ name: "SYNTH ALPHA", trades: 1 }], names_mapped: [], settings: named } }
        : request.method === "ledger_map_name"
          ? { result: { trades: [], batches: [], names_unmapped: [], names_mapped: [{ name: "SYNTH ALPHA", isin: "INE000A01012" }],
            settings: { ...SETTINGS, fmv_2018: { INE000A01012: "50" } } } }
          : { result: { settings: request.params.settings } };
      const respond = () => new Response(JSON.stringify({ id: request.id, ...reply }));
      if (request.method === "ledger_profile") return Promise.resolve(respond());
      return new Promise<Response>((resolve) => pending.push(() => resolve(respond())));
    }));
    function Edit() {
      const { update } = useSession();
      return <>{["50", "60"].map((v) => (
        <button key={v} type="button" onClick={() => update({ fmv2018: { "NAME:SYNTH ALPHA": v } })}>price {v}</button>))}</>;
    }
    localStorage.setItem("kosh.profile", "Synthetic");
    await act(async () => render(<SessionProvider><LedgerLoader /><CompanyNames /><Edit /><Probe /></SessionProvider>));
    await waitFor(() => expect(state().settingsLoaded).toBe(true));
    const methods = () => calls.map((c) => c.method);
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "price 50" })));
    expect(methods()).toEqual(["ledger_profile", "ledger_save_settings"]);  // still saving
    fireEvent.change(screen.getByLabelText("ISIN for SYNTH ALPHA"), { target: { value: "INE000A01012" } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save the ISIN for SYNTH ALPHA" })));
    expect(methods()).toHaveLength(2);  // the match waits for the save
    await act(async () => pending.shift()!());
    await waitFor(() => expect(methods()).toEqual(["ledger_profile", "ledger_save_settings", "ledger_map_name"]));
    // An edit made while the match runs, from before it, is not saved over the match.
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "price 60" })));
    expect(methods()).toHaveLength(3);  // held while the match runs
    await act(async () => pending.shift()!());
    await waitFor(() => expect(state().fmv2018).toEqual({ INE000A01012: "50" }));
    const later = calls.slice(3);
    expect(later.every((c) => !JSON.stringify(c.params).includes("NAME:SYNTH ALPHA"))).toBe(true);
    while (pending.length) await act(async () => pending.shift()!());
    localStorage.clear();
  });

  it("shows nothing when every company has an ISIN", () => {
    renderNames({});
    expect(screen.queryByLabelText("Companies imported by name")).toBeNull();
  });
});
