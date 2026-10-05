import { describe, expect, it } from "vitest";
import { inr, qty } from "./report";

describe("inr", () => {
  it.each([
    ["1234567.891", "₹12,34,567.89"],
    ["1500.0000000000", "₹1,500.00"],
    ["-5", "−₹5.00"],
    ["0", "₹0.00"],
    ["-0.004", "₹0.00"],
    ["0.005", "₹0.01"],
    ["999.995", "₹1,000.00"],
    ["123456789012", "₹1,23,45,67,89,012.00"],
    ["-100000.5", "−₹1,00,000.50"],
  ])("%s → %s", (value, expected) => {
    expect(inr(value)).toBe(expected);
  });
});

describe("qty", () => {
  it.each([["1000", "1000"], ["10.5000", "10.5"], ["3.000", "3"], ["0.1230", "0.123"]])(
    "%s → %s", (value, expected) => expect(qty(value)).toBe(expected),
  );
});
