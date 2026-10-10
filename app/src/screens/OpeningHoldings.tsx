import { useState, type FormEvent } from "react";
import { rpc } from "../engine";
import { useSession, type Acquired, type LedgerState } from "../state";
import { saveFile } from "./ExportScreen";

const HOW: { value: Acquired; label: string }[] = [
  { value: "bought", label: "Bought on the exchange" },
  { value: "ipo", label: "IPO allotment" },
  { value: "bonus", label: "Bonus shares (price 0)" },
  { value: "gift", label: "Gift or inheritance" },
  { value: "esop", label: "ESOP from my employer" },
  { value: "transfer", label: "Moved from another demat account" },
];

type Row = { isin: string; name: string; quantity: string; buy_date: string; price: string; charges: string; how_acquired: Acquired };
const EMPTY: Row = { isin: "", name: "", quantity: "", buy_date: "", price: "", charges: "", how_acquired: "bought" };

/** Download Kosh's CSV template for opening holdings. */
export function TemplateButton() {
  const [error, setError] = useState<string | null>(null);
  return (
    <>
      <button type="button" onClick={async () => {
        setError(null);
        try {
          const { name, csv } = await rpc<{ name: string; csv: string }>("opening_template");
          await saveFile(name, csv, "text/csv");
        } catch (e) {
          setError((e as Error).message);
        }
      }}>Download the template</button>
      {error && <span className="pill pill-error">{error}</span>}
    </>
  );
}

/** Enter shares held before the first imported tradebook, one row per lot (brief 0001 task 7). */
export function OpeningForm() {
  const { session, update } = useSession();
  const [rows, setRows] = useState<Row[]>([{ ...EMPTY }]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const set = (i: number, change: Partial<Row>) => setRows((all) => all.map((r, j) => (j === i ? { ...r, ...change } : r)));
  const filled = rows.filter((r) => Object.entries(r).some(([k, v]) => k !== "how_acquired" && v.trim()));
  const incomplete = filled.some((r) => !r.isin.trim() || !r.quantity.trim() || !r.buy_date || !r.price.trim());

  async function save(event: FormEvent) {
    event.preventDefault();
    const profileId = session.profileId;
    setBusy(true);
    setError(null);
    setMessage(null);
    update({ working: true });
    try {
      const result = await rpc<LedgerState & { added: number; duplicates: number; names: Record<string, string>;
        conflicts: unknown[]; warnings: string[] }>(
        "ledger_add_opening", { profile_id: profileId, rows: filled.map((r) => ({ ...r, quantity: r.quantity.replace(/,/g, ""),
          price: r.price.replace(/,/g, ""), charges: r.charges.replace(/,/g, "") })) });
      update((s) => (s.profileId !== profileId ? {}
        : { trades: result.trades, batches: result.batches, names: { ...result.names, ...s.names } }));
      if (result.conflicts.length > 0) {
        setError(`${result.conflicts.length} lot(s) are already saved with other charges or another way of acquiring them, ` +
          "so nothing was saved. To correct a lot, undo the earlier entry in the import history below, then enter it again.");
        return;
      }
      setMessage(`Saved ${result.added} holding${result.added === 1 ? "" : "s"}` +
        (result.duplicates ? `; ${result.duplicates} already saved and skipped. A lot with the same date, quantity and price ` +
          "counts as the same lot: enter identical lots in one go, or as one lot with the total quantity." : ".") +
        (result.warnings.length ? ` Note: ${result.warnings.join("; ")}.` : ""));
      setRows([{ ...EMPTY }]);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      update({ working: false });
    }
  }

  return (
    <form onSubmit={save} aria-label="Enter holdings by hand">
      <fieldset>
        <legend>Or enter them here</legend>
        <p className="muted">
          One row per lot, with the date and price you (or, for a gift, the previous owner) bought at.
          <span className="pill pill-warn" title="Best guess; to be verified">UNVERIFIED Q-029</span>
        </p>
        <table className="entry">
          <thead><tr><th>ISIN</th><th>Name (optional)</th><th>Quantity</th><th>Bought on</th><th>Price (₹)</th><th>Charges (₹)</th><th>How acquired</th><th></th></tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                <td><input aria-label={`ISIN, lot ${i + 1}`} value={r.isin} maxLength={12} placeholder="INE…" onChange={(e) => set(i, { isin: e.target.value.toUpperCase() })} /></td>
                <td><input aria-label={`Name, lot ${i + 1}`} value={r.name} onChange={(e) => set(i, { name: e.target.value })} /></td>
                <td><input aria-label={`Quantity, lot ${i + 1}`} inputMode="decimal" value={r.quantity} onChange={(e) => set(i, { quantity: e.target.value })} /></td>
                <td><input aria-label={`Bought on, lot ${i + 1}`} type="date" value={r.buy_date} onChange={(e) => set(i, { buy_date: e.target.value })} /></td>
                <td><input aria-label={`Price, lot ${i + 1}`} inputMode="decimal" value={r.price} onChange={(e) => set(i, { price: e.target.value })} /></td>
                <td><input aria-label={`Charges, lot ${i + 1}`} inputMode="decimal" placeholder="0" value={r.charges} onChange={(e) => set(i, { charges: e.target.value })} /></td>
                <td>
                  <select aria-label={`How acquired, lot ${i + 1}`} value={r.how_acquired} onChange={(e) => set(i, { how_acquired: e.target.value as Acquired })}>
                    {HOW.map((h) => <option key={h.value} value={h.value}>{h.label}</option>)}
                  </select>
                </td>
                <td>{rows.length > 1 && <button type="button" className="link" aria-label={`Remove lot ${i + 1}`} onClick={() => setRows((all) => all.filter((_, j) => j !== i))}>Remove</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <button type="button" onClick={() => setRows((all) => [...all, { ...EMPTY }])}>Add another lot</button>{" "}
        <button type="submit" className="primary" disabled={busy || session.working || filled.length === 0 || incomplete || session.profileId === null}>
          {busy ? "Saving…" : "Save holdings"}
        </button>
        {incomplete && <span className="muted" role="status"> Each lot needs an ISIN, quantity, date and price.</span>}
      </fieldset>
      {message && <p role="status">{message}</p>}
      {error && <div className="error" role="alert">Not saved: {error}</div>}
    </form>
  );
}
