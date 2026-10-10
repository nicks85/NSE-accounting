import { useState, type FormEvent } from "react";
import { rpc } from "../engine";
import { useSession, type FundClass, type Trade } from "../state";

type Broker = "zerodha" | "upstox" | "angelone" | "mapped" | "cas";

const SOURCES: { id: Broker; label: string; hint: string; accept: string }[] = [
  { id: "zerodha", label: "Zerodha tradebook", hint: "Console → Reports → Tradebook (CSV or XLSX). Several yearly files are fine.", accept: ".csv,.xlsx" },
  { id: "upstox", label: "Upstox tradebook", hint: "Trade report (CSV or XLSX).", accept: ".csv,.xlsx" },
  { id: "angelone", label: "Angel One trade history", hint: "Angel One → Account → Trades & Charges → download trade history (XLSX). Several files are fine.", accept: ".xlsx,.csv" },
  { id: "mapped", label: "Groww or other (map columns)", hint: "Tell Kosh which column holds each field. Groww XLSX files are protected with your PAN.", accept: ".csv,.xlsx" },
  { id: "cas", label: "Mutual fund CAS (PDF)", hint: "Detailed CAS from CAMS or KFintech, covering your whole history.", accept: ".pdf" },
];

export const MAPPED_FIELDS: { field: string; label: string; required?: boolean }[] = [
  { field: "trade_date", label: "Trade date", required: true },
  { field: "side", label: "Buy / sell", required: true },
  { field: "quantity", label: "Quantity", required: true },
  { field: "price", label: "Price", required: true },
  { field: "trade_id", label: "Trade / order number", required: true },
  { field: "isin", label: "ISIN (shares)" },
  { field: "symbol", label: "Contract symbol (F&O)" },
  { field: "exchange", label: "Exchange" },
  { field: "segment", label: "Segment" },
  { field: "executed_at", label: "Execution time" },
];

type ImportResult = {
  source: string;
  format_confirmed: boolean;
  trades: Trade[];
  warnings: string[];
  suggested_classes: Record<string, FundClass>;
  scheme_names: Record<string, string>;
};

/** FIFO order within a day follows execution time when the broker gives it. */
function byDateThenTime(a: Trade, b: Trade): number {
  return a.trade_date.localeCompare(b.trade_date) || (a.executed_at ?? "").localeCompare(b.executed_at ?? "");
}

async function toBase64(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

export function ImportScreen() {
  const { session, update } = useSession();
  const [broker, setBroker] = useState<Broker>("zerodha");
  const [files, setFiles] = useState<File[]>([]);
  const [inputKey, setInputKey] = useState(0); // remount the file input to clear it
  const [password, setPassword] = useState("");
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [brokerName, setBrokerName] = useState("Groww");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [last, setLast] = useState<(ImportResult & { added: number }) | null>(null);
  const source = SOURCES.find((s) => s.id === broker)!;
  const missingColumns = broker === "mapped"
    ? MAPPED_FIELDS.filter((f) => f.required && !mapping[f.field]?.trim()).map((f) => f.label)
    : [];
  const missingIds = broker === "mapped" && !mapping.isin?.trim() && !mapping.symbol?.trim();
  const missingVenue = broker === "mapped" && !mapping.exchange?.trim() && !mapping.segment?.trim();

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const params = {
        broker,
        password: password || undefined,
        files: await Promise.all(files.map(async (f) => ({ name: f.name, data_base64: await toBase64(f) }))),
        ...(broker === "mapped"
          ? { mapping, key: brokerName.toUpperCase().replace(/[^A-Z0-9]/g, "") || "MAPPED", source: `${brokerName} (mapped)` }
          : {}),
      };
      const result = await rpc<ImportResult>("import", params);
      const names = files.map((f) => f.name).join(", ");
      let added = 0;
      // Merge against the latest state, not the one captured before the await.
      update((current) => {
        const known = new Set(current.trades.map((t) => t.trade_id));
        const fresh = result.trades.filter((t) => !known.has(t.trade_id));
        added = fresh.length;
        const guessed = Object.keys(result.suggested_classes).filter((isin) => !(isin in current.fundClasses));
        return {
          trades: [...current.trades, ...fresh].sort(byDateThenTime),
          sources: fresh.length === 0 ? current.sources : [...current.sources, `${result.source}: ${names}`],
          fundClasses: { ...result.suggested_classes, ...current.fundClasses },
          unconfirmed: [...current.unconfirmed, ...guessed],
          names: { ...result.scheme_names, ...current.names },
        };
      });
      setLast({ ...result, added });
      setPassword("");
      setFiles([]);
      setInputKey((k) => k + 1); // so the same file can be chosen again
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const segments = session.trades.reduce<Record<string, number>>((acc, t) => {
    acc[t.segment] = (acc[t.segment] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div>
      <form onSubmit={submit} aria-label="Import a statement">
        <fieldset>
          <legend>1. Source</legend>
          {SOURCES.map((s) => (
            <label key={s.id} className="choice">
              <input type="radio" name="broker" value={s.id} checked={broker === s.id} onChange={() => { setBroker(s.id); setFiles([]); setInputKey((k) => k + 1); setLast(null); }} />
              {s.label}
            </label>
          ))}
          <p className="muted">{source.hint}</p>
        </fieldset>

        <fieldset>
          <legend>2. File{broker === "cas" ? "" : "s"}</legend>
          <label htmlFor="files">Choose {broker === "cas" ? "the CAS PDF" : "tradebook file(s)"}</label>
          <input key={inputKey} id="files" type="file" accept={source.accept} multiple={broker !== "cas"} onChange={(e) => setFiles(Array.from(e.target.files ?? []))} />
          <label htmlFor="password">Password {broker === "cas" ? "(required for CAS)" : "(only for protected XLSX)"}</label>
          <input id="password" type="password" autoComplete="off" value={password} onChange={(e) => setPassword(e.target.value)} />
        </fieldset>

        {broker === "mapped" && (
          <fieldset>
            <legend>3. Column names in your file</legend>
            <label htmlFor="broker-name">Broker name</label>
            <input id="broker-name" value={brokerName} onChange={(e) => setBrokerName(e.target.value)} />
            <div className="grid">
              {MAPPED_FIELDS.map(({ field, label, required }) => (
                <div key={field}>
                  <label htmlFor={`map-${field}`}>{label}{required ? " *" : ""}</label>
                  <input id={`map-${field}`} value={mapping[field] ?? ""} placeholder="column header" onChange={(e) => setMapping({ ...mapping, [field]: e.target.value })} />
                </div>
              ))}
            </div>
          </fieldset>
        )}

        {broker === "mapped" && (missingColumns.length > 0 || missingIds || missingVenue) && (
          <p className="muted" role="status">
            Still needed: {[...missingColumns, ...(missingIds ? ["ISIN or contract symbol"] : []),
              ...(missingVenue ? ["exchange or segment"] : [])].join(", ")}
          </p>
        )}
        <button type="submit" className="primary" disabled={busy || files.length === 0 || (broker === "cas" && !password)
          || missingColumns.length > 0 || missingIds || missingVenue}>
          {busy ? "Importing…" : "Import files"}
        </button>
      </form>

      {error && <div className="error" role="alert">Import failed: {error}</div>}

      {last && (
        <section aria-label="Last import">
          <h3>Imported {last.trades.length} trade{last.trades.length === 1 ? "" : "s"} from {last.source}</h3>
          {last.added < last.trades.length && <p>{last.trades.length - last.added} were already loaded and were skipped.</p>}
          {!last.format_confirmed && (
            <div className="notice">This file format is based on public documentation and isn't confirmed against real files yet. Check the trades against your broker statement.</div>
          )}
          <details>
            <summary>{last.warnings.length} note{last.warnings.length === 1 ? "" : "s"} from the importer</summary>
            <ul>{last.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
          </details>
        </section>
      )}

      <section aria-label="Loaded data">
        <h3>Loaded so far</h3>
        {session.trades.length === 0 ? (
          <p className="muted">Nothing imported yet.</p>
        ) : (
          <>
            <p>
              {session.trades.length} trades — {Object.entries(segments).map(([seg, n]) => `${n} ${seg === "EQUITY" ? "shares" : seg === "FNO" ? "F&O" : "mutual fund"}`).join(", ")}
            </p>
            <ul>{session.sources.map((s, i) => <li key={i}>{s}</li>)}</ul>
            <button type="button" onClick={() => {
              update({ trades: [], sources: [], fundClasses: {}, names: {}, fmv2018: {}, unconfirmed: [], broughtForward: [],
                       manualBuys: [], excluded: [] });
              setLast(null);
            }}>Clear all</button>
          </>
        )}
      </section>
    </div>
  );
}
