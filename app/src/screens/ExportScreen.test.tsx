import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ReportProvider } from "../report";
import { EMPTY_SESSION, SessionProvider, useSession } from "../state";
import { ExportScreen } from "./ExportScreen";

const tauri = vi.hoisted(() => ({ invoke: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: tauri.invoke }));

const REPORT = {
  tax_year: "FY 2025-26", start_year: 2025, act: "Income-tax Act, 1961",
  summary: { bucket_nets: [], exemption_used: [], taxable: [], special_rate_tax: "0", special_rate_tax_rounded: "0",
    speculative_income: "0", non_speculative_income: "0", speculative_after_setoff: "0", non_speculative_after_setoff: "0" },
  capital_gains: [{ isin: "INE000A01011", acquired_on: "2015-01-01", bucket: "LTCG @ 12.5%", manual: false }],
  business_lines: [], setoff_steps: [], carried_forward: [], expired: [], open_lots: [], warnings: [],
};

function mockEngine(replies: Record<string, object>) {
  const calls: { method: string; params: Record<string, unknown> }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_u: string, init: RequestInit) => {
    const request = JSON.parse(String(init.body));
    calls.push(request);
    const reply = replies[request.method] ?? { result: request.method === "compute" ? REPORT : { isins: [] } };
    return new Response(JSON.stringify({ id: request.id, ...reply }));
  }));
  return calls;
}

function Probe() {
  return <output data-testid="state">{JSON.stringify(useSession().session)}</output>;
}

const saved: { name: string; type: string }[] = [];

async function renderScreen(trades = [{ trade_id: "x" } as never]) {
  await act(async () => render(
    <SessionProvider initial={{ ...EMPTY_SESSION, trades }}><ReportProvider><ExportScreen /><Probe /></ReportProvider></SessionProvider>,
  ));
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  saved.length = 0;
});

function captureDownloads() {
  vi.stubGlobal("URL", { ...URL, createObjectURL: vi.fn(() => "blob:x"), revokeObjectURL: vi.fn() });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
    saved.push({ name: this.download, type: this.href });
  });
}

describe("ExportScreen", () => {
  it("downloads valid ITR schedules with the chosen form and share names", async () => {
    const calls = mockEngine({ export_itr: { result: { form: "ITR-2", valid: true, errors: [],
      warnings: [{ code: "EXPORT", message: "only two schedules", question: "Q-025" }], json: "{}" } } });
    captureDownloads();
    await renderScreen();
    const name = await screen.findByLabelText("Name for INE000A01011");
    fireEvent.change(name, { target: { value: "SYNTHETIC A LTD" } });
    await act(async () => fireEvent.blur(name));
    fireEvent.change(screen.getByLabelText("Form"), { target: { value: "ITR-2" } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Download ITR schedules" })));
    expect(saved).toEqual([{ name: "kosh-ITR-2-FY2025-26-schedules.json", type: "blob:x" }]);
    const request = calls.find((c) => c.method === "export_itr")!;
    expect(request.params).toMatchObject({ form: "ITR-2", names: { INE000A01011: "SYNTHETIC A LTD" } });
    expect(within(screen.getByLabelText("Export result")).getByText(/valid against the official schema/)).toBeTruthy();
    expect(screen.getByText(/only two schedules/)).toBeTruthy();
    fireEvent.change(name, { target: { value: "" } });
    await act(async () => fireEvent.blur(name));
    expect(JSON.parse(screen.getByTestId("state").textContent!).names).toEqual({});
  });

  it("refuses to save invalid schedules and downloads the PDF", async () => {
    mockEngine({
      export_itr: { result: { form: "ITR-3", valid: false, errors: [{ path: "Schedule112A", message: "bad" }], warnings: [], json: "{}" } },
      export_pdf: { result: { pdf_base64: btoa("%PDF-1.4") } },
    });
    captureDownloads();
    await renderScreen();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Download ITR schedules" })));
    expect(screen.getByRole("alert").textContent).toMatch(/nothing was saved/);
    expect(saved).toEqual([]);
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Download PDF summary" })));
    expect(saved).toEqual([{ name: "kosh-summary-FY2025-26.pdf", type: "blob:x" }]);
  });

  it("shows engine errors and needs trades", async () => {
    mockEngine({ export_pdf: { error: { type: "X", message: "no schema" } } });
    await renderScreen();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Download PDF summary" })));
    expect(screen.getByRole("alert").textContent).toMatch(/Export failed: no schema/);
    cleanup();
    await renderScreen([]);
    expect(screen.getByText("Import trades first.")).toBeTruthy();
  });
});

describe("ExportScreen in the desktop app", () => {
  it("saves through the native dialog and reports cancel", async () => {
    const invoke = tauri.invoke;
    invoke.mockImplementation(async (command: string, args: { request?: string; name?: string; dataBase64?: string }) => {
      if (command === "engine_rpc") {
        const request = JSON.parse(args.request!);
        const result = request.method === "compute" ? REPORT : request.method === "unclassified_funds" ? { isins: [] }
          : { pdf_base64: btoa("%PDF-1.4") };
        return JSON.stringify({ id: request.id, result });
      }
      return invoke.mock.calls.filter((c) => c[0] === "save_file").length === 1 ? "/Users/x/kosh-summary-FY2025-26.pdf" : null;
    });
    (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {};
    try {
      await renderScreen();
      await act(async () => fireEvent.click(screen.getByRole("button", { name: "Download PDF summary" })));
      await waitFor(() => expect(invoke.mock.calls.some((c) => c[0] === "save_file")).toBe(true));
      const saveCall = invoke.mock.calls.find((c) => c[0] === "save_file")!;
      expect(saveCall[1]).toMatchObject({ name: "kosh-summary-FY2025-26.pdf", dataBase64: btoa("%PDF-1.4") });
      expect((await screen.findByText(/Saved to/)).textContent).toMatch("Saved to /Users/x/kosh-summary-FY2025-26.pdf");
      await act(async () => fireEvent.click(screen.getByRole("button", { name: "Download PDF summary" })));
      expect(await screen.findByText(/you cancelled the dialog/)).toBeTruthy();
    } finally {
      delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__;
      invoke.mockReset();
    }
  });
});
