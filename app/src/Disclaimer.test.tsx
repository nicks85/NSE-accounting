import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DISCLAIMER_VERSION, DisclaimerGate } from "./Disclaimer";

beforeEach(() => localStorage.clear());
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("DisclaimerGate", () => {
  it("hides the app until the user ticks and continues, then remembers", () => {
    render(<DisclaimerGate><p>the app</p></DisclaimerGate>);
    expect(screen.queryByText("the app")).toBeNull();
    const button = screen.getByRole("button", { name: "Continue" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(button);
    expect(screen.getByText("the app")).toBeTruthy();
    expect(localStorage.getItem("kosh.disclaimer.accepted")).toBe(DISCLAIMER_VERSION);
    cleanup();
    render(<DisclaimerGate><p>the app</p></DisclaimerGate>);
    expect(screen.getByText("the app")).toBeTruthy();
  });

  it("asks again when the wording version changes", () => {
    localStorage.setItem("kosh.disclaimer.accepted", "0");
    render(<DisclaimerGate><p>the app</p></DisclaimerGate>);
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("works when storage is unavailable", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    render(<DisclaimerGate><p>the app</p></DisclaimerGate>);
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(screen.getByText("the app")).toBeTruthy();
  });
});
