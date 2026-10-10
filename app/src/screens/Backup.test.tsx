import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EMPTY_SESSION, SessionProvider, useSession } from "../state";
import { Backup } from "./Backup";

type Call = { method: string; params: Record<string, unknown> };

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
const saved: string[] = [];

function renderBackup() {
  render(<SessionProvider initial={{ ...EMPTY_SESSION, profileId: 1, settingsLoaded: true }}><Backup /><Probe /></SessionProvider>);
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
  saved.length = 0;
});

describe("Backup", () => {
  it("saves the backup file under the engine's name and says where", async () => {
    vi.stubGlobal("URL", { ...URL, createObjectURL: vi.fn(() => "blob:x"), revokeObjectURL: vi.fn() });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) { saved.push(this.download); });
    engine({ ledger_backup: { result: { name: "kosh-backup-2026-10-10.kosh", data_base64: btoa("SQLite format 3") } } });
    renderBackup();
    expect(screen.getByText(/isn’t encrypted/)).toBeTruthy();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Back up saved data" })));
    expect(saved).toEqual(["kosh-backup-2026-10-10.kosh"]);
    expect(screen.getByText("Backup downloaded as kosh-backup-2026-10-10.kosh.")).toBeTruthy();
  });

  it("restores only after confirming, then opens the remembered person", async () => {
    localStorage.setItem("kosh.profile", "Second");
    const calls = engine({
      ledger_restore: { result: { before: "/data/kosh.sqlite.before-restore", profiles: [{ id: 1, name: "Me" }, { id: 2, name: "Second" }] } },
      ledger_profile: { result: { profile: { id: 2, name: "Second" }, profiles: [{ id: 1, name: "Me" }, { id: 2, name: "Second" }],
        trades: [], batches: [], settings: { fund_classes: {}, unconfirmed: [], fmv_2018: {}, names: {}, brought_forward: [],
          manual_buys: [], excluded: [], filed_on_time: {} } } },
    });
    renderBackup();
    const backup = new File(["SQLite format 3"], "kosh-backup.kosh");
    fireEvent.change(screen.getByLabelText(/Restore from a backup/), { target: { files: [backup] } });
    expect(screen.getByText(/replaces/).textContent).toMatch(/every person/);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("button", { name: "Replace with this backup" })).toBeNull();
    fireEvent.change(screen.getByLabelText(/Restore from a backup/), { target: { files: [backup] } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Replace with this backup" })));
    expect(calls[0]).toMatchObject({ method: "ledger_restore", params: { data_base64: btoa("SQLite format 3") } });
    expect(calls[1]).toMatchObject({ method: "ledger_profile", params: { name: "Second" } });
    await waitFor(() => expect(state().profileId).toBe(2));
    expect(state().revision).toBe(1);
    expect(screen.getByText(/kept as \/data\/kosh.sqlite.before-restore/)).toBeTruthy();
  });

  it("shows a refused backup", async () => {
    engine({ ledger_restore: { error: { type: "LedgerError", message: "the backup is not a Kosh ledger (not an SQLite file)" } } });
    renderBackup();
    fireEvent.change(screen.getByLabelText(/Restore from a backup/), { target: { files: [new File(["x"], "x.kosh")] } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Replace with this backup" })));
    expect((await screen.findByRole("alert")).textContent).toMatch(/not an SQLite file/);
    expect(state()).toMatchObject({ profileId: 1, working: false });
    expect((screen.getByRole("button", { name: "Back up saved data" }) as HTMLButtonElement).disabled).toBe(false);
  });
});

describe("Backup while working (QA)", () => {
  it("marks the session as working during a restore", async () => {
    let release: () => void = () => {};
    vi.stubGlobal("fetch", vi.fn((_u: string, init: RequestInit) => new Promise<Response>((resolve) => {
      const request = JSON.parse(String(init.body));
      release = () => resolve(new Response(JSON.stringify({ id: request.id, error: { type: "LedgerError", message: "busy" } })));
    })));
    renderBackup();
    fireEvent.change(screen.getByLabelText(/Restore from a backup/), { target: { files: [new File(["x"], "x.kosh")] } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Replace with this backup" })));
    expect(state().working).toBe(true);
    expect((screen.getByRole("button", { name: "Back up saved data" }) as HTMLButtonElement).disabled).toBe(true);
    await act(async () => release());
    await waitFor(() => expect(state().working).toBe(false));
  });
});
