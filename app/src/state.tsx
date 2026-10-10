import { createContext, useContext, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
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
  /** The demat account it was made in; null for fund units and unassigned trades (brief 0003). */
  account?: string | null;
  /** For a lot moved in from another of the person's accounts: when it arrived. */
  entered_on?: string | null;
  /** The charges (except STT) by type, when the file gives them (brief 0005). */
  charge_parts?: Record<string, string>;
};

/** Shares moved between two of the person's own demat accounts (brief 0003 C). */
export type Transfer = {
  transfer_id: string; on: string; instrument: string; isin: string; quantity: string;
  from_account: string; to_account: string;
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

export type Residency = "RES" | "NOR" | "NRI";

export type Session = {
  /** Every trade saved in the ledger for this profile, as the engine replays them. */
  trades: Trade[];
  /** The ledger profile in use; null until the ledger has loaded. */
  profileId: number | null;
  /** Live imports, oldest first. */
  batches: ImportBatch[];
  /** The person's demat accounts, by name (brief 0003). */
  accounts: string[];
  /** Moves between the person's own accounts. */
  transfers: Transfer[];
  /** Companies imported by name only (no ISIN yet), with their trade counts (brief 0007). */
  namesUnmapped: { name: string; trades: number }[];
  /** Names the person has confirmed, and their ISIN. */
  namesMapped: { name: string; isin: string }[];
  /** Why the ledger couldn't be opened, if it couldn't. */
  ledgerError: string | null;
  /** Every person kept in this ledger (task 3: one file, several people). */
  profiles: Profile[];
  /** True once the profile's saved settings have been loaded; nothing is saved before that. */
  settingsLoaded: boolean;
  /** Why the last settings change couldn't be saved, if it couldn't. */
  saveError: string | null;
  /** An import or undo is running: switching person waits until it's done. */
  working: boolean;
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
  /** Start year → whether that year's return was filed by the due date (Q-011); absent = not sure. */
  filedOnTime: Record<string, boolean>;
  /** Start year → residential status (brief 0006); absent = resident. */
  residency: Record<string, Residency>;
  /** Bumped when something outside the inputs changes the report (marking a year filed). */
  revision: number;
};

export const EMPTY_SESSION: Session = {
  trades: [],
  profileId: null,
  batches: [],
  accounts: [],
  transfers: [],
  namesUnmapped: [],
  namesMapped: [],
  ledgerError: null,
  profiles: [],
  settingsLoaded: false,
  saveError: null,
  working: false,
  year: 2025,
  fundClasses: {},
  fmv2018: {},
  names: {},
  broughtForward: [],
  unconfirmed: [],
  manualBuys: [],
  excluded: [],
  filedOnTime: {},
  residency: {},
  revision: 0,
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
    transfers: session.transfers,
    residency: session.residency[String(session.year)] ?? "RES",
    late_returns: Object.entries(session.filedOnTime).filter(([, onTime]) => !onTime).map(([year]) => Number(year)),
    profile_id: session.profileId ?? undefined,
  };
}

/** localStorage key naming the ledger profile to open on launch. */
export const PROFILE_KEY = "kosh.profile";

export type Profile = { id: number; name: string };
export type LedgerState = {
  trades: Trade[]; batches: ImportBatch[]; accounts?: string[]; transfers?: Transfer[];
  names_unmapped?: { name: string; trades: number }[]; names_mapped?: { name: string; isin: string }[];
};

/** The session fields a ledger reply refreshes. */
export function ledgerFields(state: LedgerState): Pick<Session,
  "trades" | "batches" | "accounts" | "transfers" | "namesUnmapped" | "namesMapped"> {
  return {
    trades: state.trades, batches: state.batches, accounts: state.accounts ?? [], transfers: state.transfers ?? [],
    namesUnmapped: state.names_unmapped ?? [], namesMapped: state.names_mapped ?? [],
  };
}

/** Settings as the engine stores them (brief 0001 task 3). */
export type SavedSettings = {
  fund_classes: Record<string, FundClass>;
  unconfirmed: string[];
  fmv_2018: Record<string, string>;
  names: Record<string, string>;
  brought_forward: LossEntry[];
  manual_buys: { trade: Trade; how: Acquired; for_trade: string }[];
  excluded: string[];
  filed_on_time: Record<string, boolean>;
  residency?: Record<string, Residency>;
};

type Opened = LedgerState & { profile: Profile; profiles: Profile[]; settings: SavedSettings };

function toSaved(s: Session): SavedSettings {
  return {
    fund_classes: s.fundClasses, unconfirmed: s.unconfirmed, fmv_2018: s.fmv2018, names: s.names,
    brought_forward: s.broughtForward,
    manual_buys: s.manualBuys.map((m) => ({ trade: m.trade, how: m.how, for_trade: m.forTrade })),
    excluded: s.excluded,
    filed_on_time: s.filedOnTime,
    residency: s.residency,
  };
}

/** Session fields from settings the engine sent (e.g. after a change it made to them). */
export function fromSaved(saved: SavedSettings): Partial<Session> {
  return {
    fundClasses: saved.fund_classes, unconfirmed: saved.unconfirmed, fmv2018: saved.fmv_2018, names: saved.names,
    broughtForward: saved.brought_forward,
    manualBuys: saved.manual_buys.map((m) => ({ trade: m.trade, how: m.how, forTrade: m.for_trade })),
    excluded: saved.excluded,
    filedOnTime: saved.filed_on_time ?? {},
    residency: saved.residency ?? {},
  };
}

function storedProfileName(): string {
  try {
    return localStorage.getItem(PROFILE_KEY) || "Me";
  } catch {
    return "Me"; // storage unavailable: use the default profile
  }
}

/** Open the profile called `name` and replace the whole session with its saved data. */
export async function openProfile(name: string, update: Store["update"]): Promise<void> {
  update({ settingsLoaded: false });
  try {
    const opened = await rpc<Opened>("ledger_profile", { name });
    try {
      localStorage.setItem(PROFILE_KEY, opened.profile.name);
    } catch {
      // not remembered for next launch; this session still works
    }
    update({
      profileId: opened.profile.id, profiles: opened.profiles, ...ledgerFields(opened),
      ...fromSaved(opened.settings), ledgerError: null, saveError: null, settingsLoaded: true,
    });
  } catch (e) {
    // The person shown before stays open and editable; only a first launch has nothing loaded.
    update((current) => ({ ledgerError: (e as Error).message, settingsLoaded: current.profileId !== null }));
  }
}

/** Settings saves: one at a time, in order, since two requests in flight could land in either
 * order. While one is running, only the newest waiting set per person is kept. */
const saves = {
  running: false, paused: false, waiting: new Map<number, string>(), idle: [] as (() => void)[],
  flush: () => {},
};

/** Runs ``change`` (an engine call that changes this person's saved settings itself, such as
 * matching a company name to its ISIN, whose reply carries the settings) once every queued
 * settings save has landed, and holds new saves until it is done. A set queued meanwhile was
 * made before the change and is dropped: the reply's settings replace it. Changes run one at a
 * time. */
let changes: Promise<unknown> = Promise.resolve();
export function afterSettingsSaved<T>(profileId: number | null, change: () => Promise<T>): Promise<T> {
  const run = changes.then(async () => {
    if (saves.running || saves.waiting.size > 0) {
      await new Promise<void>((resolve) => saves.idle.push(resolve));
    }
    saves.paused = true;
    try {
      return await change();
    } finally {
      if (profileId !== null) saves.waiting.delete(profileId);
      saves.paused = false;
      saves.flush();
    }
  });
  changes = run.catch(() => undefined);
  return run;
}

/** Opens the saved profile once on launch, then saves every settings change straight away. */
export function LedgerLoader() {
  const { session, update } = useSession();
  useEffect(() => {
    void openProfile(storedProfileName(), update);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once per launch
  }, []);

  // What the ledger holds for this profile, so loading a profile doesn't save it straight back.
  const saved = useRef<string | null>(null);
  const queue = useRef(saves);
  const flush = () => {
    const q = queue.current;
    if (q.running || q.paused) return;
    const next = q.waiting.entries().next();
    if (next.done) {
      q.idle.splice(0).forEach((resolve) => resolve());
      return;
    }
    const [pid, settings] = next.value;
    q.waiting.delete(pid);
    q.running = true;
    rpc("ledger_save_settings", { profile_id: pid, settings: JSON.parse(settings) })
      .then(() => update((s) => (s.profileId === pid ? { saveError: null } : {})))
      .catch((e: Error) => update((s) => ({
        // A failed save for someone no longer shown still needs saying, with whose it was.
        saveError: s.profileId === pid ? e.message
          : `${s.profiles.find((p) => p.id === pid)?.name ?? "another person"}: ${e.message}`,
      })))
      .finally(() => {
        q.running = false;
        flush();
      });
  };
  saves.flush = flush;
  const { profileId, settingsLoaded } = session;
  const current = JSON.stringify(toSaved(session));
  useEffect(() => {
    if (!settingsLoaded || profileId === null) {
      saved.current = null;
      return;
    }
    if (saved.current === null) {
      saved.current = current; // just loaded
      return;
    }
    if (saved.current === current) return;
    saved.current = current;
    queue.current.waiting.delete(profileId); // re-insert so the order follows the latest change
    queue.current.waiting.set(profileId, current);
    flush();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- save when the settings change
  }, [current, profileId, settingsLoaded]);
  return null;
}

/** Choose which person's data is shown, or add a person (task 3). */
export function ProfileSwitcher() {
  const { session, update } = useSession();
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const taken = session.profiles.some((p) => p.name.toLowerCase() === name.trim().toLowerCase());
  async function add(event: FormEvent) {
    event.preventDefault();
    if (!name.trim() || taken) return;
    await openProfile(name.trim(), update);
    setAdding(false);
    setName("");
  }
  if (session.profileId === null) return null;
  return (
    <div className="profiles">
      <label htmlFor="profile">Person</label>
      <select id="profile" value={session.profileId} disabled={!session.settingsLoaded || session.working}
        onChange={(e) => {
          const chosen = session.profiles.find((p) => p.id === Number(e.target.value));
          if (chosen) void openProfile(chosen.name, update);
        }}>
        {session.profiles.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
      </select>
      {adding ? (
        <form onSubmit={add} className="inline-form" aria-label="Add a person">
          <label htmlFor="new-profile">Name</label>
          <input id="new-profile" value={name} maxLength={60} onChange={(e) => setName(e.target.value)} />
          <button type="submit" className="primary" disabled={!name.trim() || taken || session.working}>Add</button>
          <button type="button" onClick={() => { setAdding(false); setName(""); }}>Cancel</button>
          {taken && <span className="muted" role="status">There is already a person with this name.</span>}
        </form>
      ) : (
        <button type="button" className="link" disabled={session.working} onClick={() => setAdding(true)}>Add a person</button>
      )}
      {!session.settingsLoaded && <span className="muted" role="status">Opening…</span>}
      {session.ledgerError && session.settingsLoaded && (
        <div className="error" role="alert">Couldn't switch person: {session.ledgerError}</div>
      )}
      {session.saveError && <div className="error" role="alert">Your last change wasn't saved: {session.saveError}</div>}
    </div>
  );
}

/** What identifies an account name: letters and digits, ignoring case (as the engine does). */
export function accountKey(name: string): string {
  return name.toLowerCase().replace(/[^0-9a-z]/g, "");
}
