import { useState, type FormEvent } from "react";
import { inr, qty, type Report, type Shortfall } from "../report";
import { useSession, type Acquired, type ManualBuy } from "../state";

/** What to enter for each way of acquiring shares (best guess, docs/OPEN_QUESTIONS.md Q-029). */
const HOW: { value: Acquired; label: string; hint: string }[] = [
  { value: "bought", label: "Bought on the exchange", hint: "Use the trade date and price from your contract note or an older tradebook." },
  { value: "ipo", label: "IPO allotment", hint: "Use the allotment date and the issue price you paid." },
  { value: "bonus", label: "Bonus shares", hint: "Use the allotment date. Bonus shares cost nothing, so enter 0 as the price." },
  { value: "gift", label: "Gift or inheritance", hint: "Use the date and price at which the previous owner bought them: their holding period and cost carry over to you." },
  { value: "esop", label: "ESOP from my employer", hint: "Use the date you exercised and the market value per share that was taxed as salary (see your Form 16)." },
  { value: "transfer", label: "Moved from another demat account", hint: "Use the original purchase date and price at your previous broker. A move between your own accounts isn't a sale." },
];

const NUMBER = /^\d+(\.\d+)?$/;

function nextId(buys: ManualBuy[]): string {
  const used = new Set(buys.map((b) => b.trade.trade_id));
  let n = buys.length + 1;
  while (used.has(`MANUAL:${n}`)) n += 1;
  return `MANUAL:${n}`;
}

function PurchaseForm({ gap, onDone }: { gap: Shortfall; onDone: () => void }) {
  const { update } = useSession();
  const [how, setHow] = useState<Acquired>("bought");
  const [date, setDate] = useState("");
  const [quantity, setQuantity] = useState(gap.quantity);
  const [price, setPrice] = useState("");
  const [charges, setCharges] = useState("");
  const unit = gap.segment === "MF" ? "unit" : "share";
  const id = gap.trade_id.replace(/[^A-Za-z0-9]/g, "-");
  const clean = (v: string) => v.replace(/,/g, "").trim();
  const problems = [
    !date ? "purchase date" : date >= gap.sold_on ? "a purchase date before the sale" : null,
    !NUMBER.test(clean(quantity)) || Number(clean(quantity)) <= 0 ? "quantity" : null,
    !NUMBER.test(clean(price)) ? "price" : how !== "bonus" && Number(clean(price)) === 0 ? "a price above 0 (0 only for bonus shares)" : null,
    clean(charges) !== "" && !NUMBER.test(clean(charges)) ? "charges as a number" : null,
  ].filter((p): p is string => p !== null);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (problems.length > 0) return;
    update((s) => ({
      manualBuys: [...s.manualBuys, {
        how, forTrade: gap.trade_id,
        trade: {
          trade_id: nextId(s.manualBuys), trade_date: date, instrument: gap.instrument, side: "BUY",
          quantity: clean(quantity), price: clean(price), charges: clean(charges) || "0", stt: "0",
          segment: gap.segment, executed_at: null,
        },
      }],
    }));
    onDone();
  }

  return (
    <form onSubmit={submit} aria-label={`Purchase for sale ${gap.trade_id}`} className="purchase-form">
      <div className="grid">
        <div>
          <label htmlFor={`how-${id}`}>How you got these {unit}s</label>
          <select id={`how-${id}`} value={how} onChange={(e) => setHow(e.target.value as Acquired)}>
            {HOW.map((h) => <option key={h.value} value={h.value}>{h.label}</option>)}
          </select>
        </div>
        <div>
          <label htmlFor={`date-${id}`}>Purchase date</label>
          <input id={`date-${id}`} type="date" value={date} max={gap.sold_on} onChange={(e) => setDate(e.target.value)} />
        </div>
        <div>
          <label htmlFor={`qty-${id}`}>Quantity</label>
          <input id={`qty-${id}`} inputMode="decimal" value={quantity} onChange={(e) => setQuantity(e.target.value)} />
        </div>
        <div>
          <label htmlFor={`price-${id}`}>Price per {unit} (₹)</label>
          <input id={`price-${id}`} inputMode="decimal" placeholder="e.g. 245.50" value={price} onChange={(e) => setPrice(e.target.value)} />
        </div>
        <div>
          <label htmlFor={`charges-${id}`}>Charges, excluding STT (₹, optional)</label>
          <input id={`charges-${id}`} inputMode="decimal" placeholder="0" value={charges} onChange={(e) => setCharges(e.target.value)} />
        </div>
      </div>
      <p className="muted">{HOW.find((h) => h.value === how)!.hint} <span className="pill pill-warn" title="Best guess; to be verified">UNVERIFIED Q-029</span></p>
      {problems.length > 0 && <p className="muted" role="status">Still needed: {problems.join(", ")}</p>}
      <button type="submit" className="primary" disabled={problems.length > 0}>Add purchase</button>{" "}
      <button type="button" onClick={onDone}>Cancel</button>
    </form>
  );
}

function GapRow({ gap }: { gap: Shortfall }) {
  const { session, update } = useSession();
  const [open, setOpen] = useState(false);
  const name = session.names[gap.isin];
  return (
    <li>
      <p>
        <strong>{name ?? gap.instrument}</strong>: sold {qty(gap.quantity)} on {gap.sold_on} for {inr(gap.sale_value)}, but
        no earlier purchase of {qty(gap.quantity)} {gap.segment === "MF" ? "units" : "shares"} is in your files.
      </p>
      {open ? <PurchaseForm gap={gap} onDone={() => setOpen(false)} /> : (
        <>
          <button type="button" className="primary" onClick={() => setOpen(true)}>Add the purchase</button>{" "}
          <button type="button" onClick={() => update((s) => ({ excluded: [...s.excluded, gap.trade_id] }))}>
            Exclude this sale
          </button>
        </>
      )}
    </li>
  );
}

/** Sales with no matching purchase: add the purchase or exclude the sale (brief 0001 D5). */
export function MissingHistory({ report }: { report: Report }) {
  const { session, update } = useSession();
  const added = session.manualBuys;
  const excluded = report.excluded_sales;
  if (report.missing_history.length === 0 && added.length === 0 && session.excluded.length === 0) return null;
  return (
    <section aria-label="Missing purchase history" className="needs">
      {report.missing_history.length > 0 && (
        <>
          <h3>Missing purchase history ({report.missing_history.length})</h3>
          <p>
            These sales have no earlier purchase in your files, so Kosh can't know what they cost, and it won't guess.
            Import the older tradebook if you have it, or add the purchase here. Until each is resolved, the tax figure is
            withheld and export is off. You can also exclude a sale, but your gains will then be understated.
          </p>
          <ul className="gaps">{report.missing_history.map((g) => <GapRow key={`${g.trade_id}-${g.quantity}`} gap={g} />)}</ul>
        </>
      )}
      {added.length > 0 && (
        <>
          <h4>Purchases you added</h4>
          <table>
            <thead><tr><th>Shares</th><th>How</th><th>Date</th><th className="num">Qty</th><th className="num">Price</th><th className="num">Charges</th><th></th></tr></thead>
            <tbody>
              {added.map((b) => (
                <tr key={b.trade.trade_id}>
                  <td>{session.names[b.trade.instrument] ?? b.trade.instrument}</td>
                  <td>{HOW.find((h) => h.value === b.how)!.label}</td>
                  <td>{b.trade.trade_date}</td>
                  <td className="num">{qty(b.trade.quantity)}</td>
                  <td className="num">{inr(b.trade.price)}</td>
                  <td className="num">{inr(b.trade.charges)}</td>
                  <td><button type="button" className="link" onClick={() => update((s) => ({ manualBuys: s.manualBuys.filter((m) => m.trade.trade_id !== b.trade.trade_id) }))}>Remove</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      {session.excluded.length > 0 && (
        <>
          <h4>Excluded sales</h4>
          {excluded.length > 0 && <p className="muted">Left out of this year's figures: {excluded.length} sale(s), {inr(report.excluded_value)} of sale value.</p>}
          <ul>
            {session.excluded.map((id) => {
              const gap = excluded.find((g) => g.trade_id === id || g.trade_id === `${id}#delivery`);
              return (
                <li key={id}>
                  {gap ? <>{session.names[gap.isin] ?? gap.instrument}, sold {gap.sold_on}: {qty(gap.quantity)} for {inr(gap.sale_value)}</> : <>Sale {id} (not in this year: another tax year, or its purchase has since been found)</>}{" "}
                  <button type="button" className="link" onClick={() => update((s) => ({ excluded: s.excluded.filter((e) => e !== id) }))}>Include again</button>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </section>
  );
}
