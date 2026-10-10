import { useState, type FormEvent } from "react";
import { inr, useReport } from "../report";
import { useSession, type LossEntry } from "../state";

export const LOSS_KINDS = [
  { value: "Short-term capital loss", years: 8 },
  { value: "Long-term capital loss", years: 8 },
  { value: "Speculative business loss", years: 4 },
  { value: "Non-speculative business loss", years: 8 },
];

function yearLabel(start: number): string {
  return `${start >= 2026 ? "TY" : "FY"} ${start}-${String((start + 1) % 100).padStart(2, "0")}`;
}

function LossTable({ label, losses }: { label: string; losses: LossEntry[] }) {
  return (
    <table aria-label={label}>
      <thead><tr><th>Loss</th><th>Year of loss</th><th className="num">Amount</th></tr></thead>
      <tbody>
        {losses.map((l, i) => (
          <tr key={i}><td>{l.kind}</td><td>{yearLabel(l.origin_year)}</td><td className="num">{inr(l.amount)}</td></tr>
        ))}
      </tbody>
    </table>
  );
}

/** Was each relevant year's return filed by the due date? (Q-011; 2025 Act s.121, 1961 Act s.80) */
function OnTime() {
  const { session, update } = useSession();
  const years = [...new Set([...session.broughtForward.map((l) => l.origin_year), session.year])].sort((a, b) => b - a);
  const set = (year: number, value: string) => update((s) => {
    const filedOnTime = { ...s.filedOnTime };
    if (value === "") delete filedOnTime[year];
    else filedOnTime[year] = value === "yes";
    return { filedOnTime };
  });
  return (
    <section aria-label="Returns filed on time">
      <h3>Returns filed on time</h3>
      <p className="muted">
        A loss carries forward only if it was shown in a return filed by the due date. If a year’s return was late,
        its losses can’t be set off in later years (2025 Act s.121; 1961 Act s.80). <span className="pill pill-warn"
        title="Best guess; to be verified">UNVERIFIED Q-011</span>
      </p>
      {years.map((year) => {
        const value = session.filedOnTime[year];
        return (
          <div key={year} className="row">
            <label htmlFor={`on-time-${year}`}>Return for {yearLabel(year)} filed by the due date?</label>
            <select id={`on-time-${year}`} value={value === undefined ? "" : value ? "yes" : "no"}
              onChange={(e) => set(year, e.target.value)}>
              <option value="">Not sure / not filed yet</option>
              <option value="yes">Yes, on time</option>
              <option value="no">No, filed late</option>
            </select>
          </div>
        );
      })}
    </section>
  );
}

export function LossesScreen() {
  const { session, update } = useSession();
  const state = useReport();
  const [kind, setKind] = useState(LOSS_KINDS[0].value);
  const [origin, setOrigin] = useState(session.year - 1);
  const [amount, setAmount] = useState("");
  const [error, setError] = useState<string | null>(null);
  const earliest = session.year - 8;
  const years = Array.from({ length: session.year - earliest }, (_, i) => session.year - 1 - i);

  function add(event: FormEvent) {
    event.preventDefault();
    const cleaned = amount.replace(/,/g, "").trim();
    if (!/^\d+(\.\d{1,2})?$/.test(cleaned) || Number(cleaned) === 0) {
      setError("Enter the loss as a positive amount in rupees, e.g. 25000 or 25,000.50");
      return;
    }
    setError(null);
    update({ broughtForward: [...session.broughtForward, { origin_year: origin, kind, amount: cleaned }] });
    setAmount("");
  }

  return (
    <div>
      <section aria-label="Brought forward">
        <h3>Losses brought forward into {yearLabel(session.year)}</h3>
        <p className="muted">
          Unabsorbed losses from earlier returns (see Schedule CFL of last year’s ITR). Capital and
          F&amp;O losses carry forward 8 years, intraday (speculative) losses 4 years — and only if
          that year’s return was filed on time (2025 Act s.121, 1961 Act s.80).
        </p>
        {session.broughtForward.length > 0 && (
          <table aria-label="Entered losses">
            <thead><tr><th>Loss</th><th>Year of loss</th><th className="num">Amount</th><th></th></tr></thead>
            <tbody>
              {session.broughtForward.map((l, i) => (
                <tr key={i}>
                  <td>{l.kind}</td><td>{yearLabel(l.origin_year)}</td><td className="num">{inr(l.amount)}</td>
                  <td><button type="button" className="link" onClick={() => update({ broughtForward: session.broughtForward.filter((_, j) => j !== i) })}>Remove</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <form onSubmit={add} aria-label="Add a brought-forward loss" className="inline-form">
          <div>
            <label htmlFor="loss-kind">Kind</label>
            <select id="loss-kind" value={kind} onChange={(e) => setKind(e.target.value)}>
              {LOSS_KINDS.map((k) => <option key={k.value} value={k.value}>{k.value}</option>)}
            </select>
          </div>
          <div>
            <label htmlFor="loss-year">Year it arose</label>
            <select id="loss-year" value={origin} onChange={(e) => setOrigin(Number(e.target.value))}>
              {years.map((y) => <option key={y} value={y}>{yearLabel(y)}</option>)}
            </select>
          </div>
          <div>
            <label htmlFor="loss-amount">Amount (₹)</label>
            <input id="loss-amount" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="25000" />
          </div>
          <button type="submit" className="primary">Add loss</button>
        </form>
        {error && <div className="error" role="alert">{error}</div>}
      </section>

      <OnTime />

      {state.status === "ready" && (
        <section aria-label="Result">
          <h3>After {state.report.tax_year}</h3>
          <p className="muted">How much of each loss was used, and against what, is listed on the Gains screen under “How losses and the exemption were applied”.</p>
          {state.report.carried_forward.length === 0 ? <p>No losses to carry forward.</p>
            : <><p>Carry these forward in Schedule CFL of this year’s return:</p><LossTable label="Carried forward" losses={state.report.carried_forward} /></>}
          {(state.report.lapsed ?? []).length > 0 && (
            <><div className="notice">Not set off, because that year’s return was filed late (2025 Act s.121; 1961 Act s.80):</div>
              <LossTable label="Lapsed" losses={state.report.lapsed!} /></>
          )}
          {(state.report.not_carried ?? []).length > 0 && (
            <><div className="notice">This year’s return is marked as filed late, so these losses don’t carry forward:</div>
              <LossTable label="Not carried forward" losses={state.report.not_carried!} /></>
          )}
          {state.report.expired.length > 0 && (
            <><div className="notice">These losses are past their carry-forward period and were not used:</div>
              <LossTable label="Expired" losses={state.report.expired} /></>
          )}
        </section>
      )}
      {state.status === "error" && <div className="error" role="alert">Couldn’t calculate: {state.message}</div>}
    </div>
  );
}
