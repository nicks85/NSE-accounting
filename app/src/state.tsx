import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { rpc } from "./engine";

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

/** How a hand-entered purchase was acquired; guides which date and price the user enters (Q-029). */
export type Acquired = "bought" | "ipo" | "bonus" | "gift" | "esop" | "transfer";

/** A purchase the user entered for a sale with missing purchase history. */
export type ManualBuy = { trade: Trade; how: Acquired; forTrade: string };

/** One imported file in the ledger's history (brief 0001 D3). */
export type ImportBatch = {
  id: number; kind: string; broker: string | null; file_name: string | null; file_sha256: string | null;
  imported_at: string; date_from: string | null; date_to: string | null; rows_read: number | null;
  trades_added: number; duplicates_skipped: number | null;
};

export type Session = {
  /** Every trade saved in the ledger for this profile, as the engine replays them. */
  trades: Trade[];
  /** The ledger profile in use; null until the ledger has loaded. */
  profileId: number | null;
  /** Live imports, oldest first. */
  batches: ImportBatch[];
  /** Why the ledger couldn't be opened, if it couldn't. */
  ledgerError: string | null;
  year: number;
  fundClasses: Record<string, FundClass>;
  fmv2018: Record<string, string>;
  names: Record<string, string>;
  broughtForward: LossEntry[];
  /** Fund classes pre-filled from a CAS guess, not yet confirmed by the user. */
  unconfirmed: string[];
  /** Purchases entered by hand for sales whose purchase isn't in any imported file. */
  manualBuys: ManualBuy[];
  /** Sell trade ids the user chose to leave out (missing purchase history, Q-031). */
  excluded: string[];
};

export const EMPTY_SESSION: Session = {
  trades: [],
  profileId: null,
  batches: [],
  ledgerError: null,
  year: 2025,
  fundClasses: {},
  fmv2018: {},
  names: {},
  broughtForward: [],
  unconfirmed: [],
  manualBuys: [],
  excluded: [],
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
    trades: [...session.trades, ...session.manualBuys.map((m) => m.trade)],
    fund_classes: session.fundClasses,
    fmv_2018: session.fmv2018,
    brought_forward: session.broughtForward,
    excluded: session.excluded,
  };
}

/** localStorage key naming the ledger profile to open (a profile switcher arrives in task 3). */
export const PROFILE_KEY = "kosh.profile";

export type LedgerState = { trades: Trade[]; batches: ImportBatch[] };

/** Opens the saved ledger profile once and loads its trades and import history. */
export function LedgerLoader() {
  const { update } = useSession();
  useEffect(() => {
    let name = "Me";
    try {
      name = localStorage.getItem(PROFILE_KEY) || "Me";
    } catch {
      // storage unavailable: use the default profile
    }
    rpc<LedgerState & { profile: { id: number; name: string } }>("ledger_profile", { name })
      .then((r) => update({ profileId: r.profile.id, trades: r.trades, batches: r.batches, ledgerError: null }))
      .catch((e: Error) => update({ ledgerError: e.message }));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once per launch
  }, []);
  return null;
}
