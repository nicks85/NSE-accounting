import { useState, type FormEvent } from "react";
import { rpc } from "../engine";
import { useSession, type FundClass, type ImportBatch, type LedgerState, type Session, type Trade } from "../state";

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

/** What happened to one file (brief 0001 D3). */
type FileOutcome = {
  name: string;
  added: number;
  duplicates: number;
  already_imported_on: string | null;
  conflicts: { new: Trade; existing: Trade; reason: "same_id" | "other_source" }[];
  /** Saved, but matching a saved trade from another source (no times to tell them apart). */
  possible_duplicates: number;
};

type ImportResult = LedgerState & {
  files: FileOutcome[];
  source: string | null;
  format_confirmed: boolean;
  warnings: string[];
  suggested_classes: Record<string, FundClass>;
  scheme_names: Record<string, string>;
};

const BROKER_LABEL: Record<string, string> = {
  zerodha: "Zerodha", upstox: "Upstox", angelone: "Angel One", cas: "CAS",
};

function when(iso: string): string {
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? iso : at.toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
}

function FileLine({ file }: { file: FileOutcome }) {
  if (file.already_imported_on) {
    return <li>{file.name}: already imported on {when(file.already_imported_on)}. Nothing was read from it.</li>;
  }
  if (file.conflicts.length > 0) {
    return (
      <li>
        <strong>{file.name}: not imported.</strong>{" "}
        {file.conflicts.some((c) => c.reason === "same_id") && <>
          Some trades have the same trade number as one already saved, but different details. Kosh won't overwrite
          either; check which file is right, undo the wrong import below if needed, then import again.{" "}
        </>}
        {file.conflicts.some((c) => c.reason === "other_source") && <>
          Some trades match a saved trade from another source to the second (same time, share, quantity and price), so
          they look like the same trades imported again under another name. Use the same broker name as before, or undo
          the earlier import first.
        </>}
        <table aria-label={`Conflicts in ${file.name}`}>
          <thead><tr><th></th><th>Date</th><th>Instrument</th><th>Side</th><th className="num">Qty</th><th className="num">Price</th></tr></thead>
          <tbody>
            {file.conflicts.flatMap((c, i) => [["In this file", c.new], ["Already saved", c.existing]].map(([label, t]) => {
              const trade = t as Trade;
              return (
                <tr key={`${i}-${label as string}`}>
                  <td>{label as string}</td><td>{trade.trade_date}</td><td>{trade.instrument}</td><td>{trade.side}</td>
                  <td className="num">{trade.quantity}</td><td className="num">{trade.price}</td>
                </tr>
              );
            }))}
          </tbody>
        </table>
      </li>
    );
  }
  return (
    <li>
      {file.name}: {file.added} new trade{file.added === 1 ? "" : "s"}
      {file.duplicates > 0 && <>, {file.duplicates} already in your ledger and skipped</>}.
      {file.possible_duplicates > 0 && (
        <div className="notice">
          {file.possible_duplicates} of these match a trade saved from another source on the same day, share, quantity
          and price. This file has no trade times, so Kosh can't tell whether they're the same trades imported under
          another broker name. If they are, undo this import and import it again under the earlier broker name.
        </div>
      )}
    </li>
  );
}

/** Reload trades and history from the ledger after a failure, so the screen never shows stale data. */
async function reload(profileId: number | null, update: ReturnType<typeof useSession>["update"]) {
  if (profileId === null) return;
  try {
    const { trades, batches } = await rpc<LedgerState>("ledger_state", { profile_id: profileId });
    if (!Array.isArray(trades) || !Array.isArray(batches)) return;
    update((current) => ({ trades, batches, ...forgetRemoved(current, trades) }));
  } catch {
    // the error already shown is the useful one
  }
}

/** Hand-entered purchases and exclusions for sales that are no longer saved (their import was undone). */
function forgetRemoved(current: Session, trades: Trade[]): Pick<Session, "manualBuys" | "excluded"> {
  const ids = new Set(trades.map((t) => t.trade_id));
  const kept = (id: string) => ids.has(id) || ids.has(id.replace(/#delivery$/, ""));
  return {
    manualBuys: current.manualBuys.filter((m) => kept(m.forTrade)),
    excluded: current.excluded.filter(kept),
  };
}

function History() {
  const { session, update } = useSession();
  const [confirming, setConfirming] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function undo(batch: ImportBatch) {
    setError(null);
    try {
      const result = await rpc<LedgerState>("ledger_undo", { profile_id: session.profileId, batch_id: batch.id });
      update((current) => ({ trades: result.trades, batches: result.batches, ...forgetRemoved(current, result.trades) }));
    } catch (e) {
      setError((e as Error).message);
      await reload(session.profileId, update);
    } finally {
      setConfirming(null);
    }
  }
  if (session.batches.length === 0) return <p className="muted">Nothing imported yet.</p>;
  return (
    <>
      <table aria-label="Import history">
        <thead><tr><th>File</th><th>Source</th><th>Trades from</th><th>to</th><th className="num">Added</th><th>Imported on</th><th></th></tr></thead>
        <tbody>
          {session.batches.map((b) => (
            <tr key={b.id}>
              <td>{b.file_name ?? "—"}</td>
              <td>{BROKER_LABEL[b.broker ?? ""] ?? b.broker ?? b.kind}</td>
              <td>{b.date_from ?? "—"}</td><td>{b.date_to ?? "—"}</td>
              <td className="num">{b.trades_added}</td>
              <td>{when(b.imported_at)}</td>
              <td>
                {confirming === b.id ? (
                  <>
                    <button type="button" className="link" onClick={() => undo(b)}>Remove {b.trades_added} trade{b.trades_added === 1 ? "" : "s"}</button>{" "}
                    <button type="button" className="link" onClick={() => setConfirming(null)}>Keep</button>
                  </>
                ) : (
                  <button type="button" className="link" aria-label={`Undo import of ${b.file_name ?? b.id}`} onClick={() => setConfirming(b.id)}>Undo</button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {error && <div className="error" role="alert">Undo failed: {error}</div>}
    </>
  );
}

async function toBase64(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

const added = (result: ImportResult) => result.files.reduce((n, f) => n + f.added, 0);

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
  const [last, setLast] = useState<ImportResult | null>(null);
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
      if (session.profileId === null) throw new Error("your saved data hasn't loaded yet");
      const result = await rpc<ImportResult>("ledger_import", { ...params, profile_id: session.profileId });
      // Merge against the latest state, not the one captured before the await.
      update((current) => {
        const guessed = Object.keys(result.suggested_classes).filter((isin) => !(isin in current.fundClasses));
        return {
          trades: result.trades,
          batches: result.batches,
          fundClasses: { ...result.suggested_classes, ...current.fundClasses },
          unconfirmed: [...current.unconfirmed, ...guessed],
          names: { ...result.scheme_names, ...current.names },
        };
      });
      setLast(result);
      setPassword("");
      setFiles([]);
      setInputKey((k) => k + 1); // so the same file can be chosen again
    } catch (e) {
      setError((e as Error).message);
      await reload(session.profileId, update);  // the ledger is the truth, whatever failed
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

      {session.ledgerError && (
        <div className="error" role="alert">Your saved data couldn't be opened: {session.ledgerError}</div>
      )}
      {error && <div className="error" role="alert">Import failed: {error}</div>}

      {last && (
        <section aria-label="Last import">
          <h3>Imported {added(last)} new trade{added(last) === 1 ? "" : "s"}{last.source ? ` from ${last.source}` : ""}</h3>
          <ul>{last.files.map((f, i) => <FileLine key={`${i}-${f.name}`} file={f} />)}</ul>
          {!last.format_confirmed && added(last) > 0 && (
            <div className="notice">This file format is based on public documentation and isn't confirmed against real files yet. Check the trades against your broker statement.</div>
          )}
          <details>
            <summary>{last.warnings.length} note{last.warnings.length === 1 ? "" : "s"} from the importer</summary>
            <ul>{last.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
          </details>
        </section>
      )}

      <section aria-label="Saved data">
        <h3>Saved on this computer</h3>
        {session.trades.length > 0 && (
          <p>
            {session.trades.length} trades — {Object.entries(segments).map(([seg, n]) => `${n} ${seg === "EQUITY" ? "shares" : seg === "FNO" ? "F&O" : "mutual fund"}`).join(", ")}.
            They stay saved when you close Kosh, so next year you only import the new year's file.
          </p>
        )}
        <History />
      </section>
    </div>
  );
}
