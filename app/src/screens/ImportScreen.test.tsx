import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SessionProvider, useSession } from "../state";
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

function renderScreen() {
  render(<SessionProvider><ImportScreen /><Probe /></SessionProvider>);
}

const state = () => JSON.parse(screen.getByTestId("state").textContent!);
const file = (name: string, text = "a,b") => new File([text], name, { type: "text/csv" });

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ImportScreen", () => {
  it("imports a Zerodha file, adds trades once and shows the unconfirmed notice", async () => {
    const result = { source: "Zerodha tradebook (CSV)", format_confirmed: false, trades: [TRADE],
                     warnings: ["note one"], suggested_classes: {}, scheme_names: {} };
    const calls = engineReplies({ result }, { result });
    renderScreen();
    const button = screen.getByRole("button", { name: "Import files" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/Choose tradebook/), { target: { files: [file("tb.csv")] } });
    await act(async () => fireEvent.click(button));
    await screen.findByText(/Imported 1 trade from Zerodha/);
    expect(screen.getByText(/isn't confirmed/)).toBeTruthy();
    expect(calls[0].method).toBe("import");
    expect(calls[0].params.broker).toBe("zerodha");
    expect((calls[0].params.files as { data_base64: string }[])[0].data_base64).toBe(btoa("a,b"));
    expect(state().trades).toHaveLength(1);

    fireEvent.change(screen.getByLabelText(/Choose tradebook/), { target: { files: [file("tb.csv")] } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Import files" })));
    await screen.findByText(/1 were already loaded/);
    expect(state().trades).toHaveLength(1);
    expect(screen.getByText(/1 trades — 1 shares/)).toBeTruthy();
    expect(state().sources).toHaveLength(1); // the no-op re-import adds no source line

    fireEvent.click(screen.getByRole("button", { name: "Clear all" }));
    expect(state().trades).toHaveLength(0);
  });

  it("sends the column mapping for Groww/Angel One", async () => {
    const calls = engineReplies({ result: { source: "Groww (mapped)", format_confirmed: false,
      trades: [], warnings: [], suggested_classes: {}, scheme_names: {} } });
    renderScreen();
    fireEvent.click(screen.getByLabelText(/Groww, Angel One/));
    fireEvent.change(screen.getByLabelText("Trade date *"), { target: { value: "Date" } });
    fireEvent.change(screen.getByLabelText("Broker name"), { target: { value: "Angel One" } });
    fireEvent.change(screen.getByLabelText(/Choose tradebook/), { target: { files: [file("g.csv")] } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Import files" })));
    await screen.findByText(/Imported 0 trades/);
    expect(calls[0].params).toMatchObject({ broker: "mapped", key: "ANGELONE", source: "Angel One (mapped)",
                                            mapping: { trade_date: "Date" } });
  });

  it("needs a password for a CAS and merges suggested classes without overriding", async () => {
    engineReplies({ result: { source: "CAS", format_confirmed: false, trades: [], warnings: [],
      suggested_classes: { INF000E01011: "equity-oriented" }, scheme_names: { INF000E01011: "Fund" } } });
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

  it("shows engine errors", async () => {
    engineReplies({ error: { type: "ImportFormatError", message: "row 3: bad price" } });
    renderScreen();
    fireEvent.change(screen.getByLabelText(/Choose tradebook/), { target: { files: [file("x.csv")] } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Import files" })));
    expect((await screen.findByRole("alert")).textContent).toMatch(/row 3: bad price/);
  });
});
