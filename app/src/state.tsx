import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

/** A trade as the engine sends it: amounts are decimal strings. */
export type Trade = {
  trade_id: string;
  trade_date: string;
  instrument: string;
  side: "BUY" | "SELL";
  quantity: string;
  price: string;
  charges: string;
  stt: string;
  segment: "EQUITY" | "FNO" | "MF";
  executed_at: string | null;
};

export type FundClass = "equity-oriented" | "specified" | "other";

export type LossEntry = { origin_year: number; kind: string; amount: string };

export type Session = {
  trades: Trade[];
  sources: string[];
  year: number;
  fundClasses: Record<string, FundClass>;
  fmv2018: Record<string, string>;
  names: Record<string, string>;
  broughtForward: LossEntry[];
  /** Fund classes pre-filled from a CAS guess, not yet confirmed by the user. */
  unconfirmed: string[];
};

export const EMPTY_SESSION: Session = {
  trades: [],
  sources: [],
  year: 2025,
  fundClasses: {},
  fmv2018: {},
  names: {},
  broughtForward: [],
  unconfirmed: [],
};

type Change = Partial<Session> | ((current: Session) => Partial<Session>);
/** Functional changes merge against the latest state (no stale overwrites after an await). */
type Store = { session: Session; update: (change: Change) => void };

const SessionContext = createContext<Store | null>(null);

export function SessionProvider({ children, initial = EMPTY_SESSION }: {
  children: ReactNode;
  initial?: Session;
}) {
  const [session, setSession] = useState(initial);
  const store = useMemo(
    () => ({
      session,
      update: (change: Change) =>
        setSession((s) => ({ ...s, ...(typeof change === "function" ? change(s) : change) })),
    }),
    [session],
  );
  return <SessionContext.Provider value={store}>{children}</SessionContext.Provider>;
}

export function useSession(): Store {
  const store = useContext(SessionContext);
  if (!store) throw new Error("useSession must be used inside SessionProvider");
  return store;
}

/** Engine parameters for compute/export calls. */
export function computeParams(session: Session) {
  return {
    year: session.year,
    trades: session.trades,
    fund_classes: session.fundClasses,
    fmv_2018: session.fmv2018,
    brought_forward: session.broughtForward,
  };
}
