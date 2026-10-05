import { inr, qty, useReport } from "../report";
import { useSession } from "../state";

const SEGMENT_LABEL: Record<string, string> = { EQUITY: "Shares", MF: "Mutual fund", FNO: "F&O" };

export function HoldingsScreen() {
  const { session } = useSession();
  const state = useReport();
  if (state.status === "idle") return <p className="muted">Import trades first.</p>;
  if (state.status === "loading") return <p aria-live="polite">Calculating…</p>;
  if (state.status === "error") return <div className="error" role="alert">Couldn’t calculate: {state.message}</div>;
  const lots = state.report.open_lots;
  return (
    <div>
      <p className="muted">
        Lots still held at the end of {state.report.tax_year}, after first-in-first-out matching
        (cost includes brokerage and charges; for fund units, per folio).
      </p>
      {lots.length === 0 ? (
        <p>No open holdings.</p>
      ) : (
        <table aria-label="Open lots">
          <thead>
            <tr><th>Instrument</th><th>Type</th><th>Acquired</th><th className="num">Quantity</th><th className="num">Cost</th></tr>
          </thead>
          <tbody>
            {lots.map((lot, i) => (
              <tr key={i}>
                <td>{session.names[lot.isin] ? <>{session.names[lot.isin]}<br /><span className="muted">{lot.instrument}</span></> : lot.instrument}</td>
                <td>{SEGMENT_LABEL[lot.segment] ?? lot.segment}</td>
                <td>{lot.acquired_on}</td>
                <td className="num">{qty(lot.quantity)}</td>
                <td className="num">{inr(lot.cost)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
