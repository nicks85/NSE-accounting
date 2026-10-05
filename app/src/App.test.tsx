import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

function mockEngine(reply: object) {
  vi.stubGlobal("fetch", vi.fn(async (_u: string, init: RequestInit) =>
    new Response(JSON.stringify({ id: JSON.parse(String(init.body)).id, ...reply }))));
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("App shell", () => {
  it("renders the title, disclaimer and engine version", async () => {
    mockEngine({ result: { version: "9.9.9" } });
    await act(async () => render(<App />));
    expect(screen.getByRole("heading", { name: "Kosh" })).toBeTruthy();
    expect(screen.getByRole("note").textContent).toMatch(/Not tax advice/);
    expect(await screen.findByText(/Engine 9.9.9/)).toBeTruthy();
  });

  it("switches tabs and shows each screen", async () => {
    mockEngine({ result: { version: "1" } });
    await act(async () => render(<App screens={{ gains: <p>gains screen</p> }} />));
    fireEvent.click(screen.getByRole("tab", { name: "Gains" }));
    expect(screen.getByRole("heading", { level: 2, name: "Gains" })).toBeTruthy();
    expect(screen.getByText("gains screen")).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Gains" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("tabpanel").getAttribute("aria-labelledby")).toBe("tab-gains");
  });

  it("reports an engine error", async () => {
    mockEngine({ error: { type: "X", message: "boom" } });
    await act(async () => render(<App />));
    expect((await screen.findByRole("alert")).textContent).toMatch(/boom/);
  });

  it("reports an unreachable bridge", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("nope", { status: 500 })));
    await act(async () => render(<App />));
    expect((await screen.findByRole("alert")).textContent).toMatch(/bridge returned 500/);
  });
});

describe("tabs keyboard and protocol", () => {
  it("moves between tabs with arrow, Home and End keys", async () => {
    mockEngine({ result: { version: "1" } });
    await act(async () => render(<App />));
    const tablist = screen.getByRole("tablist");
    fireEvent.keyDown(tablist, { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "Holdings" }).getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement?.textContent).toBe("Holdings");
    fireEvent.keyDown(tablist, { key: "End" });
    expect(screen.getByRole("tab", { name: "Export" }).getAttribute("tabindex")).toBe("0");
    fireEvent.keyDown(tablist, { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "Import" }).getAttribute("aria-selected")).toBe("true");
    fireEvent.keyDown(tablist, { key: "ArrowLeft" });
    fireEvent.keyDown(tablist, { key: "Home" });
    fireEvent.keyDown(tablist, { key: "x" });
    expect(screen.getByRole("tab", { name: "Import" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("main")).toBeTruthy();
  });

  it("rejects a response for a different request", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ id: 99999, result: { version: "1" } }))));
    await act(async () => render(<App />));
    expect((await screen.findByRole("alert")).textContent).toMatch(/answered request 99999/);
  });
});
