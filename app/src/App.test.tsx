import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { App } from "./App";

describe("App", () => {
  it("renders the title and disclaimer", () => {
    render(<App />);
    expect(screen.getByRole("heading", { name: "Kosh" })).toBeTruthy();
    expect(screen.getByRole("note").textContent).toMatch(/Not tax advice/);
  });
});
