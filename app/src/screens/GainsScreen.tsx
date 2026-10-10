import { Fragment, useState } from "react";
import { inr, isZero, qty, useReport, type BusinessLine, type Citation, type GainLine, type Report } from "../report";
import { useSession, type FundClass } from "../state";
import { MissingHistory } from "./MissingHistory";

const YEARS = [
  { start: 2024, label: "FY 2024-25 (AY 2025-26)" },
  { start: 2025, label: "FY 2025-26 (AY 2026-27)" },
  { start: 2026, label: "TY 2026-27 (Income-tax Act 2025)" },
];

const CLASSES: { value: FundClass; label: string }[] = [
  { value: "equity-oriented", label: "Equity-oriented (≥65% Indian listed equity)" },
  { value: "specified", label: "Specified / debt (>65% debt & money market)" },
  { value: "other", label: "Other (hybrid, international, gold…)" },
];

export function Why({ citations }: { citations: Citation[] }) {
  return (
    <ul className="why">
      {citations.map((c, i) => (
        <li key={i}>
          <strong>{c.section}</strong> — {c.topic}
          {c.unverified && <span className="pill pill-warn" title="Best guess; to be verified">UNVERIFIED {c.question}</span>}
          {(c.act_1961 || c.act_2025) && (
            <span className="muted"> (1961 Act: {c.act_1961 ?? "—"}; 2025 Act: {c.act_2025 ?? "—"})</span>
          )}
          <span className="source">Source (copy into a browser if you want to check): <code>{c.url}</code></span>
        </li>
      ))}
    </ul>
  );
}

function FmvInput({ isin, missing }: { isin: string; missing: boolean }) {
  const { session, update } = useSession();
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="row">
      <label htmlFor={`fmv-${isin}`}>31-Jan-2018 price for {session.names[isin] ?? isin} (₹ per share/unit)</label>
      <input
        id={`fmv-${isin}`}
        inputMode="decimal"
        placeholder="e.g. 1,234.55"
        defaultValue={session.fmv2018[isin] ?? ""}
        aria-invalid={error !== null}
        aria-describedby={error ? `fmv-${isin}-error` : undefined}
        onBlur={(e) => {
          const value = e.target.value.replace(/,/g, "").trim();
          if (value === "") {
            setError(null);
            update((s) => {
              const fmv2018 = { ...s.fmv2018 };
              delete fmv2018[isin];
              return { fmv2018 };
            });
          } else if (/^\d+(\.\d+)?$/.test(value) && !isZero(value)) {
            setError(null);
            update((s) => ({ fmv2018: { ...s.fmv2018, [isin]: value } }));
          } else {
            setError("Enter a price like 1234.55");
          }
        }}
      />
      {error && <span id={`fmv-${isin}-error`} className="pill pill-error">{error}</span>}
      {!error && (missing
        ? <span className="pill pill-warn">missing — actual cost used</span>
        : <span className="muted">Highest price on 31-Jan-2018 (NAV for unlisted fund units).</span>)}
    </div>
  );
}

function NeedsInput({ report, unclassified }: { report: Report; unclassified: string[] }) {
  const { session, update } = useSession();
  // Pre-2018 holdings sold long-term: ask for (or let the user edit) the 31-Jan-2018 price.
  const fmvNeeded = [...new Set(report.capital_gains
    .filter((l) => l.acquired_on <= "2018-01-31" && l.bucket.startsWith("LTCG") && !l.manual)
    .map((l) => l.isin))];
  const funds = [...new Set([...unclassified, ...session.unconfirmed])];
  if (funds.length === 0 && fmvNeeded.length === 0) return null;
  const confirm = (isin: string) => update((s) => ({ unconfirmed: s.unconfirmed.filter((i) => i !== isin) }));
  return (
    <section aria-label="Needs your input" className="needs">
      <h3>Needs your input</h3>
      {funds.map((isin) => (
        <div key={isin} className="row">
          <label htmlFor={`class-${isin}`}>Fund class for {session.names[isin] ?? isin}</label>
          <select
            id={`class-${isin}`}
            value={session.fundClasses[isin] ?? ""}
            onChange={(e) => {
              const value = e.target.value as FundClass;
              update((s) => ({ fundClasses: { ...s.fundClasses, [isin]: value },
                               unconfirmed: s.unconfirmed.filter((i) => i !== isin) }));
            }}
          >
            <option value="" disabled>Choose…</option>
            {CLASSES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
          </select>
          {unclassified.includes(isin) && <span className="pill pill-warn">not classified — treated as “other”</span>}
          {session.unconfirmed.includes(isin) && (
            <>
              <span className="pill pill-warn">guessed from the CAS — please confirm</span>{" "}
              <button type="button" className="link" onClick={() => confirm(isin)}>Confirm</button>
            </>
          )}
        </div>
      ))}
      {fmvNeeded.map((isin) => <FmvInput key={isin} isin={isin} missing={!(isin in session.fmv2018)} />)}
    </section>
  );
}

function LineRow({ line }: { line: GainLine }) {
  const [open, setOpen] = useState(false);
  const { session } = useSession();
  return (
    <Fragment>
      <tr>
        <td>{session.names[line.isin] ? <>{session.names[line.isin]}<br /><span className="muted">{line.instrument}</span></> : line.instrument}</td>
        <td>{line.acquired_on}</td>
        <td>{line.sold_on}</td>
        <td className="num">{qty(line.quantity)}</td>
        <td className="num">{inr(line.sale_value)}</td>
        <td className="num">{line.manual ? "—" : inr(line.cost)}</td>
        <td className="num">{line.manual ? "—" : inr(line.gain)}</td>
        <td>{line.manual ? <span className="pill pill-warn">MANUAL</span> : line.bucket}</td>
        <td>
          <button type="button" className="link" aria-expanded={open} onClick={() => setOpen(!open)}>
            {open ? "Hide" : "Why?"}
          </button>
        </td>
      </tr>
      {open && (
        <tr className="why-row">
          <td colSpan={9}>
            <p>
              Held from {line.acquired_on} to {line.sold_on}; long-term if sold after {line.long_term_after}.
              Sale {inr(line.sale_value)} − expenses {inr(line.transfer_expenses)} − cost {inr(line.cost)}
              {line.grandfathered_fmv !== null && <> (actual cost {inr(line.actual_cost)}, 31-Jan-2018 value {inr(line.grandfathered_fmv)})</>}
              {!isZero(line.stripped_loss) && <> + ignored bonus-stripping loss {inr(line.stripped_loss)}</>}
              {" "}= {inr(line.gain)}.
            </p>
            <Why citations={line.citations} />
          </td>
        </tr>
      )}
    </Fragment>
  );
}

function BusinessRow({ line }: { line: BusinessLine }) {
  const [open, setOpen] = useState(false);
  return (
    <Fragment>
      <tr>
        <td>{line.instrument}</td><td>{line.opened_on}</td><td>{line.closed_on}</td>
        <td className="num">{qty(line.quantity)}</td><td className="num">{inr(line.income)}</td>
        <td>{line.speculative ? "Speculative (intraday)" : "Non-speculative (F&O)"}</td>
        <td><button type="button" className="link" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? "Hide" : "Why?"}</button></td>
      </tr>
      {open && <tr className="why-row"><td colSpan={7}><Why citations={line.citations} /></td></tr>}
    </Fragment>
  );
}

export function GainsScreen() {
  const { session, update } = useSession();
  const state = useReport();
  return (
    <div>
      <label htmlFor="year">Tax year</label>
      <select id="year" value={session.year} onChange={(e) => update({ year: Number(e.target.value) })}>
        {YEARS.map((y) => <option key={y.start} value={y.start}>{y.label}</option>)}
      </select>

      {state.status === "idle" && <p className="muted">Import trades first.</p>}
      {state.status === "loading" && <p aria-live="polite">Calculating…</p>}
      {state.status === "error" && <div className="error" role="alert">Couldn’t calculate: {state.message}</div>}
      {state.status === "ready" && (
        <>
          {state.stale && <p className="muted" aria-live="polite">Updating…</p>}
          <NeedsInput report={state.report} unclassified={state.unclassified} />
          <MissingHistory report={state.report} />

          <section aria-label="Summary">
            <h3>Summary — {state.report.tax_year} ({state.report.act})</h3>
            <table>
              <tbody>
                {state.report.summary.bucket_nets.map((a) => <tr key={`n${a.label}`}><td>Net {a.label}</td><td className="num">{inr(a.amount)}</td></tr>)}
                {state.report.summary.exemption_used.map((a) => <tr key={`e${a.label}`}><td>₹1.25 lakh exemption used ({a.label})</td><td className="num">{inr(a.amount)}</td></tr>)}
                {state.report.summary.taxable.map((a) => <tr key={`t${a.label}`}><td>Taxable {a.label}</td><td className="num">{inr(a.amount)}</td></tr>)}
                <tr className="total">
                  <td>Tax at special rates (rounded to ₹10)</td>
                  <td className="num">{state.report.complete
                    ? inr(state.report.summary.special_rate_tax_rounded)
                    : <span className="pill pill-warn">Incomplete: {state.report.missing_history.length} sale(s) missing purchase history</span>}</td>
                </tr>
                <tr><td>Intraday (speculative) income after set-off</td><td className="num">{inr(state.report.summary.speculative_after_setoff)}</td></tr>
                <tr><td>F&amp;O (non-speculative) income after set-off</td><td className="num">{inr(state.report.summary.non_speculative_after_setoff)}</td></tr>
              </tbody>
            </table>
            {!state.report.complete && (
              <p role="status">The tax figure is withheld because some sales have no purchase to match. The amounts above
                leave those sales out and may change once the purchases are added.</p>
            )}
            {state.report.excluded_sales.length > 0 && (
              <p role="status">Excludes {state.report.excluded_sales.length} sale(s) ({inr(state.report.excluded_value)} sale value) with missing purchase history.</p>
            )}
            <p className="muted">Slab-rate gains and business income are taxed at your slab rates, outside the special-rate figure. Surcharge and cess are not included.</p>
          </section>

          <section aria-label="Capital gains">
            <h3>Capital gains ({state.report.capital_gains.length})</h3>
            {state.report.capital_gains.length === 0 ? <p className="muted">No sales in this year.</p> : (
              <table>
                <thead><tr><th>Instrument</th><th>Bought</th><th>Sold</th><th className="num">Qty</th><th className="num">Sale value</th><th className="num">Cost used</th><th className="num">Gain</th><th>Taxed as</th><th></th></tr></thead>
                <tbody>{state.report.capital_gains.map((l) => <LineRow key={`${l.open_trade_id}>${l.close_trade_id}`} line={l} />)}</tbody>
              </table>
            )}
          </section>

          {state.report.business_lines.length > 0 && (
            <section aria-label="Trading income">
              <h3>Intraday and F&amp;O</h3>
              <table>
                <thead><tr><th>Instrument</th><th>Opened</th><th>Closed</th><th className="num">Qty</th><th className="num">Income after STT</th><th>Kind</th><th></th></tr></thead>
                <tbody>
                  {state.report.business_lines.map((b, i) => <BusinessRow key={i} line={b} />)}
                </tbody>
              </table>
            </section>
          )}

          {state.report.setoff_steps.length > 0 && (
            <section aria-label="Set-off">
              <h3>How losses and the exemption were applied</h3>
              <table>
                <thead><tr><th>Loss / relief</th><th>Against</th><th className="num">Amount</th><th>Rule</th></tr></thead>
                <tbody>
                  {state.report.setoff_steps.map((s, i) => (
                    <tr key={i}><td>{s.loss}</td><td>{s.against}</td><td className="num">{inr(s.amount)}</td><td><Why citations={[s.citation]} /></td></tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}

          <section aria-label="Warnings">
            <h3>Warnings and assumptions ({state.report.warnings.length})</h3>
            {state.report.warnings.map((w, i) => (
              <div key={i} className={w.code === "UNVERIFIED" ? "notice" : "note"}>
                <strong>{w.code}{w.question ? ` ${w.question}` : ""}</strong> {w.message}
              </div>
            ))}
          </section>
        </>
      )}
    </div>
  );
}
