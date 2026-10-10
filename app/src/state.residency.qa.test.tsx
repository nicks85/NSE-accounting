import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LedgerLoader, ProfileSwitcher, SessionProvider, useSession } from "./state";

// QA (brief 0006): the status is per person. Older ledgers send settings without `residency`.
const OLD = { fund_classes: {}, unconfirmed: [], fmv_2018: {}, names: {}, brought_forward: [], manual_buys: [], excluded: [], filed_on_time: {} };
const PROFILES = [{ id: 4, name: "Synthetic" }, { id: 5, name: "Second" }];

function engine() {
  const calls: { method: string; params: Record<string, unknown> }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init: RequestInit) => {
    const request = JSON.parse(String(init.body));
    calls.push(request);
    const reply = request.method === "ledger_profile"
      ? { result: { profile: PROFILES.find((p) => p.name === request.params.name), profiles: PROFILES, trades: [], batches: [],
          settings: request.params.name === "Synthetic" ? { ...OLD, residency: { 2025: "NRI" } } : OLD } }
      : { result: { settings: request.params.settings } };
    return new Response(JSON.stringify({ id: request.id, ...reply }));
  }));
  return calls;
}

function Probe() {
  const { session } = useSession();
  return <output data-testid="state">{JSON.stringify(session)}</output>;
}
const state = () => JSON.parse(screen.getByTestId("state").textContent!);

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("Residential status per person (QA, brief 0006)", () => {
  it("loads with the person, resets on a switch, and an older ledger's settings aren't saved back", async () => {
    localStorage.setItem("kosh.profile", "Synthetic");
    const calls = engine();
    await act(async () => render(<SessionProvider><LedgerLoader /><ProfileSwitcher /><Probe /></SessionProvider>));
    await waitFor(() => expect(state().settingsLoaded).toBe(true));
    expect(state().residency).toEqual({ 2025: "NRI" });
    await act(async () => fireEvent.change(screen.getByLabelText("Person"), { target: { value: "5" } }));
    await waitFor(() => expect(state().profileId).toBe(5));
    expect(state().residency).toEqual({});
    expect(calls.filter((c) => c.method === "ledger_save_settings")).toEqual([]);
  });
});
