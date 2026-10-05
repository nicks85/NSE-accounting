import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

function mockEngine(reply: object | Error) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      reply instanceof Error ? Promise.reject(reply) : new Response(JSON.stringify({ id: 1, ...reply })),
    ),
  );
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
    fireEvent.click(screen.getByRole("button", { name: "Gains" }));
    expect(screen.getByRole("heading", { level: 2, name: "Gains" })).toBeTruthy();
    expect(screen.getByText("gains screen")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Gains" }).getAttribute("aria-current")).toBe("page");
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
