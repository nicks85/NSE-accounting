import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EMPTY_SESSION, LedgerLoader, ProfileSwitcher, SessionProvider, useSession } from "./state";

const TRADE = {
  trade_id: "ZERODHA:NSE:2025-05-01:1", trade_date: "2025-05-01", instrument: "INE000A01011",
  side: "BUY", quantity: "10", price: "100", charges: "0", stt: "0", segment: "EQUITY", executed_at: null,
};
const EMPTY = { fund_classes: {}, unconfirmed: [], fmv_2018: {}, names: {}, brought_forward: [], manual_buys: [], excluded: [] };
const SAVED = { ...EMPTY, fmv_2018: { INE000A01011: "800" }, unconfirmed: ["INF000E01011"],
  fund_classes: { INF000E01011: "equity-oriented" },
  manual_buys: [{ trade: { ...TRADE, trade_id: "MANUAL:1" }, how: "ipo", for_trade: "S1" }] };
const PROFILES = [{ id: 4, name: "Synthetic" }, { id: 5, name: "Second" }];

type Call = { method: string; params: Record<string, unknown> };

/** Answers ledger_profile by name; records every call. */
function engine(opts: { fail?: string; saveError?: string } = {}) {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init: RequestInit) => {
    const request = JSON.parse(String(init.body));
    calls.push(request);
    let reply: object;
    if (opts.fail) reply = { error: { type: "LedgerError", message: opts.fail } };
    else if (request.method === "ledger_profile") {
      const name = request.params.name as string;
      const profile = PROFILES.find((p) => p.name === name) ?? { id: 9, name };
      reply = { result: { profile, profiles: profile.id === 9 ? [...PROFILES, profile] : PROFILES,
        trades: profile.id === 4 ? [TRADE] : [], batches: [], settings: profile.id === 4 ? SAVED : EMPTY } };
    } else if (opts.saveError) reply = { error: { type: "ValueError", message: opts.saveError } };
    else reply = { result: { settings: request.params.settings } };
    return new Response(JSON.stringify({ id: request.id, ...reply }));
  }));
  return calls;
}

function Probe() {
  const { session, update } = useSession();
  return (
    <>
      <output data-testid="state">{JSON.stringify(session)}</output>
      <button type="button" onClick={() => update((s) => ({ fmv2018: { ...s.fmv2018, INE000B01012: "50" } }))}>edit</button>
    </>
  );
}

const state = () => JSON.parse(screen.getByTestId("state").textContent!);
const saves = (calls: Call[]) => calls.filter((c) => c.method === "ledger_save_settings");

async function renderApp() {
  await act(async () => render(<SessionProvider><LedgerLoader /><ProfileSwitcher /><Probe /></SessionProvider>));
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("LedgerLoader", () => {
  it("opens the remembered profile with its trades and settings, without saving them back", async () => {
    localStorage.setItem("kosh.profile", "Synthetic");
    const calls = engine();
    await renderApp();
    await waitFor(() => expect(state().settingsLoaded).toBe(true));
    expect(calls[0]).toMatchObject({ method: "ledger_profile", params: { name: "Synthetic" } });
    expect(state()).toMatchObject({ profileId: 4, trades: [TRADE], fmv2018: { INE000A01011: "800" },
      unconfirmed: ["INF000E01011"], manualBuys: [{ how: "ipo", forTrade: "S1" }] });
    expect(saves(calls)).toEqual([]);
  });

  it("saves each settings change for the open profile", async () => {
    localStorage.setItem("kosh.profile", "Synthetic");
    const calls = engine();
    await renderApp();
    await waitFor(() => expect(state().settingsLoaded).toBe(true));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "edit" })));
    await waitFor(() => expect(saves(calls)).toHaveLength(1));
    expect(saves(calls)[0].params).toMatchObject({ profile_id: 4, settings: {
      fmv_2018: { INE000A01011: "800", INE000B01012: "50" },
      manual_buys: [{ how: "ipo", for_trade: "S1" }] } });
  });

  it("shows a failed save", async () => {
    const calls = engine({ saveError: "31-Jan-2018 price must be positive" });
    await renderApp();
    await waitFor(() => expect(state().settingsLoaded).toBe(true));
    expect(calls[0].params).toEqual({ name: "Me" });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "edit" })));
    expect((await screen.findByRole("alert")).textContent).toMatch(/wasn't saved: 31-Jan-2018 price/);
  });

  it("reports a ledger that can't be opened and saves nothing", async () => {
    const calls = engine({ fail: "made by a newer version of Kosh" });
    await renderApp();
    await waitFor(() => expect(state().ledgerError).toMatch(/newer version/));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "edit" })));
    expect(saves(calls)).toEqual([]);
    expect(screen.queryByLabelText("Person")).toBeNull();
  });
});

describe("ProfileSwitcher", () => {
  it("switches person without carrying settings over, and remembers the choice", async () => {
    localStorage.setItem("kosh.profile", "Synthetic");
    const calls = engine();
    await renderApp();
    await waitFor(() => expect(state().settingsLoaded).toBe(true));
    await act(async () => fireEvent.change(screen.getByLabelText("Person"), { target: { value: "5" } }));
    await waitFor(() => expect(state().profileId).toBe(5));
    expect(state()).toMatchObject({ trades: [], fmv2018: {}, manualBuys: [] });
    expect(localStorage.getItem("kosh.profile")).toBe("Second");
    expect(saves(calls)).toEqual([]);  // switching saves nothing into either profile
  });

  it("adds a person with a new name only", async () => {
    localStorage.setItem("kosh.profile", "Synthetic");
    engine();
    await renderApp();
    await waitFor(() => expect(state().settingsLoaded).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "Add a person" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "second" } });
    expect(screen.getByText(/already a person with this name/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Add" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "  Third  " } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Add" })));
    await waitFor(() => expect(state().profileId).toBe(9));
    expect(state().profiles.map((p: { name: string }) => p.name)).toEqual(["Synthetic", "Second", "Third"]);
    expect(localStorage.getItem("kosh.profile")).toBe("Third");
    expect(screen.getByRole("button", { name: "Add a person" })).toBeTruthy();
  });

  it("cancels adding and stays hidden before the ledger opens", async () => {
    localStorage.setItem("kosh.profile", "Synthetic");
    engine();
    await renderApp();
    await waitFor(() => expect(state().settingsLoaded).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "Add a person" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByLabelText("Name")).toBeNull();
    cleanup();
    render(<SessionProvider initial={EMPTY_SESSION}><ProfileSwitcher /></SessionProvider>);
    expect(screen.queryByLabelText("Person")).toBeNull();
  });
});

describe("Saving and switching races (QA)", () => {
  it("sends saves one at a time and ends with the newest settings", async () => {
    localStorage.setItem("kosh.profile", "Synthetic");
    const calls: Call[] = [];
    const pending: (() => void)[] = [];
    vi.stubGlobal("fetch", vi.fn((_u: string, init: RequestInit) => {
      const request = JSON.parse(String(init.body));
      calls.push(request);
      const reply = request.method === "ledger_profile"
        ? { result: { profile: PROFILES[0], profiles: PROFILES, trades: [], batches: [], settings: EMPTY } }
        : { result: { settings: request.params.settings } };
      const respond = () => new Response(JSON.stringify({ id: request.id, ...reply }));
      if (request.method !== "ledger_save_settings") return Promise.resolve(respond());
      return new Promise<Response>((resolve) => pending.push(() => resolve(respond())));
    }));
    function Edits() {
      const { update } = useSession();
      return <>{["1", "2", "3"].map((v) => <button key={v} type="button" onClick={() => update({ fmv2018: { X: v } })}>set {v}</button>)}</>;
    }
    await act(async () => render(<SessionProvider><LedgerLoader /><Edits /><Probe /></SessionProvider>));
    await waitFor(() => expect(state().settingsLoaded).toBe(true));
    for (const v of ["1", "2", "3"]) await act(async () => fireEvent.click(screen.getByRole("button", { name: `set ${v}` })));
    expect(saves(calls)).toHaveLength(1);  // the others wait
    await act(async () => pending.shift()!());
    await waitFor(() => expect(saves(calls)).toHaveLength(2));
    expect(saves(calls).map((c) => (c.params.settings as { fmv_2018: object }).fmv_2018)).toEqual([{ X: "1" }, { X: "3" }]);
    await act(async () => pending.shift()!());
    expect(saves(calls)).toHaveLength(2);
  });

  it("keeps the open person usable when switching fails", async () => {
    localStorage.setItem("kosh.profile", "Synthetic");
    let failNext = false;
    const calls: Call[] = [];
    vi.stubGlobal("fetch", vi.fn(async (_u: string, init: RequestInit) => {
      const request = JSON.parse(String(init.body));
      calls.push(request);
      const reply = failNext && request.method === "ledger_profile"
        ? { error: { type: "LedgerError", message: "the ledger is busy" } }
        : request.method === "ledger_profile"
          ? { result: { profile: PROFILES[0], profiles: PROFILES, trades: [], batches: [], settings: EMPTY } }
          : { result: {} };
      return new Response(JSON.stringify({ id: request.id, ...reply }));
    }));
    await renderApp();
    await waitFor(() => expect(state().settingsLoaded).toBe(true));
    failNext = true;
    await act(async () => fireEvent.change(screen.getByLabelText("Person"), { target: { value: "5" } }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/Couldn't switch person: the ledger is busy/);
    expect(state()).toMatchObject({ profileId: 4, settingsLoaded: true });
    expect((screen.getByLabelText("Person") as HTMLSelectElement).disabled).toBe(false);
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "edit" })));
    await waitFor(() => expect(saves(calls)).toHaveLength(1));
    expect(saves(calls)[0].params.profile_id).toBe(4);
  });
});

describe("Save errors after switching (QA)", () => {
  it("names the person whose change wasn't saved", async () => {
    localStorage.setItem("kosh.profile", "Synthetic");
    let failSave: () => void = () => {};
    vi.stubGlobal("fetch", vi.fn((_u: string, init: RequestInit) => {
      const request = JSON.parse(String(init.body));
      if (request.method === "ledger_save_settings") {
        return new Promise<Response>((resolve) => {
          failSave = () => resolve(new Response(JSON.stringify({ id: request.id, error: { type: "LedgerError", message: "disk full" } })));
        });
      }
      const profile = request.params.name === "Second" ? PROFILES[1] : PROFILES[0];
      return Promise.resolve(new Response(JSON.stringify({ id: request.id, result: {
        profile, profiles: PROFILES, trades: [], batches: [], settings: EMPTY } })));
    }));
    await renderApp();
    await waitFor(() => expect(state().settingsLoaded).toBe(true));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "edit" })));
    await act(async () => fireEvent.change(screen.getByLabelText("Person"), { target: { value: "5" } }));
    await waitFor(() => expect(state().profileId).toBe(5));
    await act(async () => failSave());
    expect((await screen.findByRole("alert")).textContent).toMatch(/wasn't saved: Synthetic: disk full/);
  });
});
