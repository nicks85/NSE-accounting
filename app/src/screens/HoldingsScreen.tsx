import { useState, type FormEvent } from "react";
import { rpc } from "../engine";
import { inr, qty, useReport } from "../report";
import { accountKey, ledgerFields, useSession, type LedgerState, type ManualBuy, type Session } from "../state";

const SEGMENT_LABEL: Record<string, string> = { EQUITY: "Shares", MF: "Mutual fund", FNO: "F&O" };

/** Run a ledger change and refresh accounts, transfers and trades from its reply. */
function useLedgerAction() {
  const { session, update } = useSession();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** ``relabel`` updates hand-entered purchases the page holds: they name their account, and the
   *  page saves them, so they must follow a rename or a move here (not be reloaded, which
   *  could race with an edit still being saved). */
  async function run(method: string, params: object,
    relabel?: (buy: ManualBuy, accounts: string[]) => ManualBuy): Promise<boolean> {
    const profileId = session.profileId;
    setBusy(true);
    setError(null);
    update({ working: true });
    try {
      const result = await rpc<LedgerState>(method, { profile_id: profileId, ...params });
      update((s: Session) => (s.profileId !== profileId ? {}
        : { ...ledgerFields(result), ...(relabel ? { manualBuys: s.manualBuys.map((b) => relabel(b, result.accounts ?? [])) } : {}) }));
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    } finally {
      setBusy(false);
      update({ working: false });
    }
  }
  return { busy: busy || session.working, error, run };
}

type Run = (method: string, params: object, relabel?: (buy: ManualBuy, accounts: string[]) => ManualBuy) => Promise<boolean>;

/** The stored spelling of an account name (the engine matches names exactly once saved). */
function stored(name: string, accounts: string[]): string {
  return accounts.find((a) => accountKey(a) === accountKey(name)) ?? name;
}

/** Share trades with no account yet: imported ones and hand-entered purchases. */
function unassignedTrades(session: Session) {
  return [...session.trades, ...session.manualBuys.map((b) => b.trade)]
    .filter((t) => t.segment === "EQUITY" && !t.account);
}

/** Unassigned share trades one by one, for lots that belong to different accounts. */
function Unassigned({ run, busy }: { run: Run; busy: boolean }) {
  const { session } = useSession();
  const [chosen, setChosen] = useState<Record<string, string>>({});
  const trades = unassignedTrades(session);
  return (
    <details>
      <summary>Or put them in accounts one by one</summary>
      <table aria-label="Trades with no account">
        <thead><tr><th>Date</th><th>Shares</th><th>Side</th><th className="num">Quantity</th><th>Account</th><th></th></tr></thead>
        <tbody>
          {trades.map((t) => (
            <tr key={t.trade_id}>
              <td>{t.trade_date}</td><td>{session.names[t.instrument] ?? t.instrument}</td><td>{t.side}</td>
              <td className="num">{qty(t.quantity)}</td>
              <td>
                <select aria-label={`Account for ${t.trade_id}`} value={chosen[t.trade_id] ?? ""}
                  onChange={(e) => setChosen((c) => ({ ...c, [t.trade_id]: e.target.value }))}>
                  <option value="">Choose…</option>
                  {session.accounts.map((a) => <option key={a} value={a}>{a}</option>)}
                </select>
              </td>
              <td>
                <button type="button" className="link" disabled={busy || !chosen[t.trade_id]} aria-label={`Move ${t.trade_id}`}
                  onClick={() => void run("ledger_assign_account", { account: chosen[t.trade_id], trade_ids: [t.trade_id] },
                    (b, accounts) => b.trade.trade_id === t.trade_id ? { ...b, trade: { ...b.trade, account: stored(chosen[t.trade_id], accounts) } } : b)}>Move</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

/** The person's demat accounts: rename, and put trades with no account into one (brief 0003). */
function Accounts() {
  const { session } = useSession();
  const { busy, error, run } = useLedgerAction();
  const [renaming, setRenaming] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [target, setTarget] = useState("");
  const unassigned = unassignedTrades(session).length;
  if (session.accounts.length === 0 && unassigned === 0) return null;
  return (
    <section aria-label="Demat accounts">
      <h3>Demat accounts</h3>
      <p className="muted">
        Shares are matched first-in-first-out within each demat account, not across them (CBDT Circular 768). Your tax is
        still worked out on all accounts together.
      </p>
      <ul>
        {session.accounts.map((a) => (
          <li key={a}>
            {renaming === a ? (
              <form className="inline-form" aria-label={`Rename ${a}`} onSubmit={async (e: FormEvent) => {
                e.preventDefault();
                const renamed = name.trim().replace(/\s+/g, " ");
                if (await run("ledger_rename_account", { old: a, new: renamed }, (b, accounts) =>
                  b.trade.account && accountKey(b.trade.account) === accountKey(a)
                    ? { ...b, trade: { ...b.trade, account: stored(renamed, accounts) } } : b,
                )) setRenaming(null);
              }}>
                <label htmlFor={`rename-${a}`}>New name for {a}</label>
                <input id={`rename-${a}`} value={name} maxLength={60} onChange={(e) => setName(e.target.value)} />
                <button type="submit" className="primary" disabled={busy || !name.trim()}>Rename</button>
                <button type="button" onClick={() => setRenaming(null)}>Cancel</button>
              </form>
            ) : (
              <>{a} <button type="button" className="link" aria-label={`Rename ${a}`} onClick={() => { setRenaming(a); setName(a); }}>Rename</button></>
            )}
          </li>
        ))}
      </ul>
      {unassigned > 0 && (
        <form className="notice" aria-label="Put trades in an account" onSubmit={(e: FormEvent) => {
          e.preventDefault();
          const into = target.trim().replace(/\s+/g, " ");
          void run("ledger_assign_account", { account: into }, (b, accounts) =>
            b.trade.segment === "EQUITY" && !b.trade.account ? { ...b, trade: { ...b.trade, account: stored(into, accounts) } } : b);
        }}>
          {unassigned} share trade{unassigned === 1 ? "" : "s"} (entered by hand or saved before accounts existed) {unassigned === 1 ? "isn't" : "aren't"} in
          a demat account yet, so {unassigned === 1 ? "it is" : "they are"} matched only with each other.{" "}
          <label htmlFor="assign-to">Put them in</label>{" "}
          <input id="assign-to" list="known-accounts-holdings" value={target} placeholder="e.g. Zerodha" onChange={(e) => setTarget(e.target.value)} />{" "}
          <button type="submit" className="primary" disabled={busy || !target.trim()}>Move</button>
        </form>
      )}
      {unassigned > 1 && session.accounts.length > 1 && <Unassigned run={run} busy={busy} />}
      <datalist id="known-accounts-holdings">{session.accounts.map((a) => <option key={a} value={a} />)}</datalist>
      {error && <div className="error" role="alert">{error}</div>}
    </section>
  );
}

/** Shares moved between two of the person's own accounts (brief 0003 C). */
function Transfers() {
  const { session } = useSession();
  const { busy, error, run } = useLedgerAction();
  const [on, setOn] = useState("");
  const [isin, setIsin] = useState("");
  const [quantity, setQuantity] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [confirming, setConfirming] = useState<string | null>(null);
  const shares = [...new Set(session.trades.filter((t) => t.segment === "EQUITY").map((t) => t.instrument))].sort();
  if (session.accounts.length < 2 && session.transfers.length === 0) return null;
  const cleanQty = quantity.replace(/,/g, "").trim();
  const ready = on && isin && /^\d+(\.\d+)?$/.test(cleanQty) && Number(cleanQty) > 0 && from && to && from !== to;
  return (
    <section aria-label="Transfers between your accounts">
      <h3>Transfers between your accounts</h3>
      <p className="muted">
        Moving shares between your own demat accounts isn’t a sale. They keep their purchase date and cost, and in the new
        account they queue behind what it already held on the day they arrived (CBDT Circular 768).{" "}
        <span className="pill pill-warn" title="Best guess; to be verified">UNVERIFIED Q-026</span>
      </p>
      {session.transfers.length > 0 && (
        <table aria-label="Transfers">
          <thead><tr><th>Date</th><th>Shares</th><th className="num">Quantity</th><th>From</th><th>To</th><th></th></tr></thead>
          <tbody>
            {session.transfers.map((t) => (
              <tr key={t.transfer_id}>
                <td>{t.on}</td><td>{session.names[t.isin] ?? t.instrument}</td><td className="num">{qty(t.quantity)}</td>
                <td>{t.from_account}</td><td>{t.to_account}</td>
                <td>{confirming === t.transfer_id ? (
                  <>
                    <button type="button" className="link" disabled={busy} aria-label={`Yes, remove the transfer on ${t.on}`} onClick={async () => { await run("ledger_remove_transfer", { transfer_id: t.transfer_id }); setConfirming(null); }}>Remove it</button>{" "}
                    <button type="button" className="link" onClick={() => setConfirming(null)}>Keep</button>
                  </>
                ) : (
                  <button type="button" className="link" aria-label={`Remove the transfer on ${t.on}`} onClick={() => setConfirming(t.transfer_id)}>Remove</button>
                )}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <form className="inline-form" aria-label="Add a transfer" onSubmit={async (e: FormEvent) => {
        e.preventDefault();
        if (await run("ledger_add_transfer", { on, instrument: isin, quantity: cleanQty, from_account: from, to_account: to })) {
          setQuantity("");
        }
      }}>
        <div><label htmlFor="transfer-on">Date</label><input id="transfer-on" type="date" value={on} onChange={(e) => setOn(e.target.value)} /></div>
        <div>
          <label htmlFor="transfer-isin">Shares</label>
          <select id="transfer-isin" value={isin} onChange={(e) => setIsin(e.target.value)}>
            <option value="">Choose…</option>
            {shares.map((s) => <option key={s} value={s}>{session.names[s] ?? s}</option>)}
          </select>
        </div>
        <div><label htmlFor="transfer-qty">Quantity</label><input id="transfer-qty" inputMode="decimal" value={quantity} onChange={(e) => setQuantity(e.target.value)} /></div>
        <div>
          <label htmlFor="transfer-from">From</label>
          <select id="transfer-from" value={from} onChange={(e) => setFrom(e.target.value)}>
            <option value="">Choose…</option>
            {session.accounts.map((a) => <option key={a} value={a}>{a}</option>)}
          </select>
        </div>
        <div>
          <label htmlFor="transfer-to">To</label>
          <select id="transfer-to" value={to} onChange={(e) => setTo(e.target.value)}>
            <option value="">Choose…</option>
            {session.accounts.filter((a) => a !== from).map((a) => <option key={a} value={a}>{a}</option>)}
          </select>
        </div>
        <button type="submit" className="primary" disabled={busy || !ready}>Add transfer</button>
      </form>
      {error && <div className="error" role="alert">Not saved: {error}</div>}
    </section>
  );
}

export function HoldingsScreen() {
  const { session } = useSession();
  const state = useReport();
  const byAccount = session.accounts.length > 1 || session.transfers.length > 0;
  return (
    <div>
      {state.status === "idle" && <p className="muted">Import trades first.</p>}
      {state.status === "loading" && <p aria-live="polite">Calculating…</p>}
      {state.status === "error" && <div className="error" role="alert">Couldn’t calculate: {state.message}</div>}
      {state.status === "ready" && (
        <>
          <p className="muted">
            Lots still held at the end of {state.report.tax_year}, after first-in-first-out matching
            (cost includes brokerage and charges; for fund units, per folio).
          </p>
          {state.report.open_lots.length === 0 ? (
            <p>No open holdings.</p>
          ) : (
            <table aria-label="Open lots">
              <thead>
                <tr><th>Instrument</th><th>Type</th>{byAccount && <th>Account</th>}<th>Acquired</th><th className="num">Quantity</th><th className="num">Cost</th></tr>
              </thead>
              <tbody>
                {state.report.open_lots.map((lot, i) => (
                  <tr key={i}>
                    <td>{session.names[lot.isin] ? <>{session.names[lot.isin]}<br /><span className="muted">{lot.instrument}</span></> : lot.instrument}</td>
                    <td>{SEGMENT_LABEL[lot.segment] ?? lot.segment}</td>
                    {byAccount && <td>{lot.account ?? (lot.segment === "MF" ? "—" : "Not assigned")}</td>}
                    <td>{lot.acquired_on}{lot.entered_on && <><br /><span className="muted">arrived {lot.entered_on}</span></>}</td>
                    <td className="num">{qty(lot.quantity)}</td>
                    <td className="num">{inr(lot.cost)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
      <Accounts />
      <Transfers />
    </div>
  );
}
