import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { rpc } from "./engine";
import { computeParams, useSession } from "./state";

export type Citation = {
  topic: string;
  section: string;
  act_1961: string | null;
  act_2025: string | null;
  url: string;
  unverified: boolean;
  question: string | null;
};
export type Amount = { label: string; amount: string };
export type GainLine = {
  instrument: string; isin: string; acquired_on: string; sold_on: string; long_term_after: string;
  quantity: string; sale_value: string; transfer_expenses: string; actual_cost: string; cost: string;
  grandfathered_fmv: string | null; stripped_loss: string; gain: string; bucket: string;
  manual: boolean; open_trade_id: string; close_trade_id: string; citations: Citation[];
};
export type BusinessLine = {
  instrument: string; opened_on: string; closed_on: string; quantity: string; income: string;
  speculative: boolean; citations: Citation[];
};
export type Loss = { origin_year: number; kind: string; amount: string };
export type Notice = { code: string; message: string; question: string | null; ref: string | null };
export type Report = {
  tax_year: string; start_year: number; act: string;
  summary: {
    bucket_nets: Amount[]; exemption_used: Amount[]; taxable: Amount[];
    special_rate_tax: string; special_rate_tax_rounded: string;
    speculative_income: string; non_speculative_income: string;
    speculative_after_setoff: string; non_speculative_after_setoff: string;
  };
  capital_gains: GainLine[];
  business_lines: BusinessLine[];
  setoff_steps: { loss: string; against: string; amount: string; citation: Citation }[];
  carried_forward: Loss[];
  expired: Loss[];
  open_lots: { instrument: string; isin: string; acquired_on: string; quantity: string; cost: string; segment: string }[];
  warnings: Notice[];
  /** False while a sale is missing purchase history: the tax figure is withheld. */
  complete: boolean;
  missing_history: Shortfall[];
  excluded_sales: Shortfall[];
  /** Total sale value of excluded_sales (exact decimal string). */
  excluded_value: string;
};
/** The part of a sale with no earlier purchase to match. */
export type Shortfall = {
  trade_id: string; instrument: string; isin: string; sold_on: string; quantity: string;
  price: string; sale_value: string; segment: "EQUITY" | "FNO" | "MF";
};

export type ReportState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; report: Report; unclassified: string[]; stale?: boolean }
  | { status: "error"; message: string };

const ReportContext = createContext<ReportState>({ status: "idle" });

/** Recompute whenever the inputs change; every screen reads the same result. */
export function ReportProvider({ children }: { children: ReactNode }) {
  const { session } = useSession();
  const [state, setState] = useState<ReportState>({ status: "idle" });
  const { trades, year, fundClasses, fmv2018, broughtForward, manualBuys, excluded } = session;
  useEffect(() => {
    let current = true;
    if (trades.length === 0 && manualBuys.length === 0 && broughtForward.length === 0) {
      setState({ status: "idle" });
      return;
    }
    // Keep showing the previous result while recalculating, so inputs keep their focus.
    setState((prev) => (prev.status === "ready" ? { ...prev, stale: true } : { status: "loading" }));
    const params = computeParams(session);
    Promise.all([
      rpc<Report>("compute", params),
      rpc<{ isins: string[] }>("unclassified_funds", { trades, fund_classes: fundClasses }),
    ])
      .then(([report, funds]) => current && setState({ status: "ready", report, unclassified: funds.isins }))
      .catch((e: Error) => current && setState({ status: "error", message: e.message }));
    return () => {
      current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- recompute only when inputs change
  }, [trades, year, fundClasses, fmv2018, broughtForward, manualBuys, excluded]);
  return <ReportContext.Provider value={state}>{children}</ReportContext.Provider>;
}

export function useReport(): ReportState {
  return useContext(ReportContext);
}

/** Display an engine decimal string in Indian grouping with 2 decimals. Display only. */
const DECIMAL = /^-?\d+(\.\d+)?$/;

/** True for an engine decimal string equal to zero ("0", "0.000", "-0.00"). */
export function isZero(value: string): boolean {
  return /^-?0*(\.0*)?$/.test(value);
}

export function inr(value: string): string {
  if (!DECIMAL.test(value)) return value; // never mis-render something that isn't a plain decimal
  const negative = value.startsWith("-");
  const [whole, fraction = ""] = value.replace("-", "").split(".");
  let paise = (fraction + "000").slice(0, 3);
  let rupees = whole.replace(/^0+(?=\d)/, "") || "0";
  // half-up rounding on the third decimal, done on digits (no float)
  if (Number(paise[2]) >= 5) {
    const cents = (BigInt(rupees) * 100n + BigInt(paise.slice(0, 2)) + 1n).toString().padStart(3, "0");
    rupees = cents.slice(0, -2);
    paise = cents.slice(-2) + "0";
  }
  const tail = rupees.slice(-3);
  let head = rupees.slice(0, -3);
  const groups: string[] = [];
  while (head.length > 2) {
    groups.unshift(head.slice(-2));
    head = head.slice(0, -2);
  }
  if (head) groups.unshift(head);
  const text = [...groups, tail].join(",");
  const isZero = /^0+$/.test(rupees) && /^0+$/.test(paise.slice(0, 2));
  return `${negative && !isZero ? "−" : ""}₹${text}.${paise.slice(0, 2)}`;
}

/** Quantity for display: trailing zeros after the decimal point removed ("1000" stays). */
export function qty(value: string): string {
  return value.includes(".") ? value.replace(/0+$/, "").replace(/\.$/, "") : value;
}
