import { useEffect, useRef, useState, type FormEvent } from "react";
import { fileToBase64, rpc } from "../engine";
import { inr } from "../report";
import { Backup } from "./Backup";
import { OpeningForm, TemplateButton } from "./OpeningHoldings";
import { ledgerFields, useSession, type FundClass, type ImportBatch, type LedgerState, type Session, type Trade } from "../state";

type Broker = "zerodha" | "upstox" | "angelone" | "mapped" | "cas" | "opening";

const SOURCES: { id: Broker; label: string; hint: string; accept: string }[] = [
  { id: "zerodha", label: "Zerodha tradebook", hint: "Console → Reports → Tradebook (CSV or XLSX). Several yearly files are fine.", accept: ".csv,.xlsx" },
  { id: "upstox", label: "Upstox tradebook", hint: "Trade report (CSV or XLSX).", accept: ".csv,.xlsx" },
  { id: "angelone", label: "Angel One trade history", hint: "Angel One → Account → Trades & Charges → download trade history (XLSX). Several files are fine.", accept: ".xlsx,.csv" },
  { id: "mapped", label: "Groww or other (map columns)", hint: "Tell Kosh which column holds each field. Groww XLSX files are protected with your PAN.", accept: ".csv,.xlsx" },
  { id: "opening", label: "Holdings from before your first tradebook", hint: "Shares you held before the earliest tradebook you have, entered once with their purchase date and price. Download the template and fill one row per lot, or enter them below. For mutual-fund units held with the fund house, import your CAS instead.", accept: ".csv" },
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
  /** For a file already imported: the account its trades went into. */
  imported_into?: string | null;
  /** Preview details (brief 0004). Absent for a file already imported. */
  sha256: string;
  account?: string | null;
  trades?: number; buys?: number; sells?: number;
  date_from?: string | null; date_to?: string | null;
  charges?: string; stt?: string; stated_charges?: string | null;
  charges_check?: "matches" | "differs" | "none";
  by_name?: string[];
  notes?: string[];
  conflicts: { new: Trade; existing: Trade; reason: "same_id" | "other_source" }[];
  /** Saved, but matching a saved trade from another source (no times to tell them apart). */
  possible_duplicates: number;
};

type ImportResult = LedgerState & {
  saved?: boolean;
  files: FileOutcome[];
  source: string | null;
  format_confirmed: boolean;
  warnings: string[];
  suggested_classes: Record<string, FundClass>;
  scheme_names: Record<string, string>;
};

const BROKER_LABEL: Record<string, string> = {
  zerodha: "Zerodha", upstox: "Upstox", angelone: "Angel One", cas: "CAS", opening: "Opening holdings",
};

function when(iso: string): string {
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? iso : at.toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
}

async function encode(files: File[]) {
  return Promise.all(files.map(async (f) => ({ name: f.name, data_base64: await fileToBase64(f) })));
}

/** A file that saving would store: read cleanly, at least one new trade, no conflicts. */
const savable = (f: FileOutcome) => !f.already_imported_on && f.conflicts.length === 0 && f.added > 0;

function ChargesCheck({ file }: { file: FileOutcome }) {
  if (file.charges === undefined) return null;
  return (
    <li>
      Charges {inr(file.charges)} and STT {inr(file.stt ?? "0")}.{" "}
      {file.charges_check === "matches" && <>That matches the file’s own total of {inr(file.stated_charges!)}.</>}
      {file.charges_check === "differs" && (
        <span className="pill pill-warn">The file’s own total is {inr(file.stated_charges!)}: check the file is complete</span>
      )}
      {file.charges_check === "none" && <span className="muted">This file has no charges summary to check against.</span>}
    </li>
  );
}

function PreviewFile({ file }: { file: FileOutcome }) {
  if (file.already_imported_on || file.conflicts.length > 0) {
    return <div className="preview-file"><h4>{file.name}: won’t be saved</h4><ul><FileLine file={file} /></ul></div>;
  }
  return (
    <div className="preview-file">
      <h4>{file.name}</h4>
      <ul>
        <li>
          {file.trades} trade{file.trades === 1 ? "" : "s"} ({file.buys} buy{file.buys === 1 ? "" : "s"}, {file.sells} sell{file.sells === 1 ? "" : "s"})
          {file.date_from && <> from {file.date_from} to {file.date_to}</>}
          {file.account ? <>, into the demat account {file.account}</> : null}.
        </li>
        <li>
          <strong>{file.added} new</strong>
          {file.duplicates > 0 && <>, {file.duplicates} already saved and will be skipped</>}.
          {file.added === 0 && <> Nothing to save from this file.</>}
        </li>
        <ChargesCheck file={file} />
        {(file.by_name?.length ?? 0) > 0 && (
          <li>
            {file.by_name!.length} compan{file.by_name!.length === 1 ? "y" : "ies"} without an ISIN, imported by name:{" "}
            {file.by_name!.map((n) => n.replace(/^NAME:/, "")).join(", ")}.
          </li>
        )}
        {file.possible_duplicates > 0 && (
          <li className="notice">
            {file.possible_duplicates} trade(s) match a trade saved from another source on the same day, share, quantity
            and price. Without trade times Kosh can’t tell whether they are the same trades under another broker name.
          </li>
        )}
      </ul>
      {(file.notes?.length ?? 0) > 0 && (
        <details>
          <summary>{file.notes!.length} note{file.notes!.length === 1 ? "" : "s"} from the importer (rows skipped and other details)</summary>
          <ul>{file.notes!.map((n, i) => <li key={i}>{n}</li>)}</ul>
        </details>
      )}
    </div>
  );
}

/** What saving would do, file by file; nothing is stored until "Save" (brief 0004). */
function PreviewPanel({ result, busy, onSave, onCancel }: {
  result: ImportResult; busy: boolean; onSave: () => void; onCancel: () => void;
}) {
  const total = result.files.length;
  const ready = result.files.filter(savable).length;
  return (
    <section aria-label="Import preview" className="needs">
      <h3>Check before saving</h3>
      <p className="muted">Nothing has been saved yet.</p>
      {!result.format_confirmed && (
        <div className="notice">This file format is based on public documentation and isn’t confirmed against real files yet. Check the trades against your broker statement.</div>
      )}
      {result.files.map((f, i) => <PreviewFile key={`${i}-${f.name}`} file={f} />)}
      <button type="button" className="primary" disabled={busy || ready === 0} onClick={onSave}>
        {ready === 0 ? "Nothing to save" : ready === total ? `Save ${total === 1 ? "this file" : `all ${total} files`}`
          : `Save ${ready} of ${total} files`}
      </button>{" "}
      <button type="button" onClick={onCancel}>Cancel</button>
    </section>
  );
}

function FileLine({ file }: { file: FileOutcome }) {
  if (file.already_imported_on) {
    return (
      <li>
        {file.name}: already imported on {when(file.already_imported_on)}
        {file.imported_into ? <> into {file.imported_into}</> : null}. Nothing was read from it.
        {file.imported_into && <span className="muted"> To put its trades in another account, undo that import below and import it again.</span>}
      </li>
    );
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
    const state = await rpc<LedgerState>("ledger_state", { profile_id: profileId });
    if (!Array.isArray(state.trades) || !Array.isArray(state.batches)) return;
    update((current) => (current.profileId !== profileId ? {} : { ...ledgerFields(state), ...forgetRemoved(current, state.trades) }));
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
    const profileId = session.profileId;
    setError(null);
    update({ working: true });
    try {
      const result = await rpc<LedgerState>("ledger_undo", { profile_id: profileId, batch_id: batch.id });
      // A reply for another person (switched while waiting) must never touch this session.
      update((current) => (current.profileId !== profileId ? {}
        : { ...ledgerFields(result), ...forgetRemoved(current, result.trades) }));
    } catch (e) {
      setError((e as Error).message);
      await reload(profileId, update);
    } finally {
      update({ working: false });
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
  // The last import's summary belongs to the data it was made in: forget it when the person
  // changes or a backup is restored.
  useEffect(() => setLast(null), [session.profileId, session.revision]);
  const source = SOURCES.find((s) => s.id === broker)!;
  const single = broker === "cas" || broker === "opening";
  // FIFO runs per demat account (brief 0003): one account per broker unless named otherwise.
  const [account, setAccount] = useState<string | null>(null);
  useEffect(() => setAccount(null), [session.profileId]);  // a typed account belongs to one person
  // The preview keeps the chosen files, not their encoded bytes (up to 100 MB): Save reads them
  // again and the engine checks they are byte-for-byte the ones previewed (brief 0004 B1).
  const [preview, setPreview] = useState<{ profileId: number; params: object; files: File[]; result: ImportResult } | null>(null);
  const [drift, setDrift] = useState(false);
  // Bumped whenever the inputs change: a preview reply for older inputs is dropped on arrival.
  const inputs = useRef(0);
  // A preview belongs to these files, this source and account, this person and this ledger.
  useEffect(() => {
    inputs.current += 1;
    setPreview(null);
  }, [broker, account, files, session.profileId, brokerName, mapping, password, session.revision, session.batches]);
  const defaultAccount = broker === "mapped" ? brokerName.trim()
    : broker === "opening" ? (session.accounts.length === 1 ? session.accounts[0] : "")
    : (BROKER_LABEL[broker] ?? "");
  const missingColumns = broker === "mapped"
    ? MAPPED_FIELDS.filter((f) => f.required && !mapping[f.field]?.trim()).map((f) => f.label)
    : [];
  const missingIds = broker === "mapped" && !mapping.isin?.trim() && !mapping.symbol?.trim();
  const missingVenue = broker === "mapped" && !mapping.exchange?.trim() && !mapping.segment?.trim();

  /** Step 1 (brief 0004): read the files and show what saving would do; nothing is stored. */
  async function submit(event: FormEvent) {
    event.preventDefault();
    const profileId = session.profileId;
    const asked = inputs.current;
    const chosen = files;
    setBusy(true);
    setError(null);
    setLast(null);
    setDrift(false);
    try {
      const params = {
        broker,
        password: password || undefined,
        account: broker === "cas" ? undefined : (account ?? defaultAccount).trim() || undefined,
        ...(broker === "mapped"
          ? { mapping, key: brokerName.toUpperCase().replace(/[^A-Z0-9]/g, "") || "MAPPED", source: `${brokerName} (mapped)` }
          : {}),
      };
      if (profileId === null) throw new Error("your saved data hasn't loaded yet");
      const result = await rpc<ImportResult>("ledger_import", {
        ...params, files: await encode(chosen), profile_id: profileId, mode: "preview" });
      if (inputs.current === asked) setPreview({ profileId, params, files: chosen, result });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  /** Step 2: save exactly the bytes that were previewed; the engine checks them (brief 0004 B1). */
  async function save() {
    if (!preview) return;
    const { profileId, params } = preview;
    setBusy(true);
    update({ working: true });
    setError(null);
    try {
      const expected = preview.result.files.map((f) => f.sha256);
      const result = await rpc<ImportResult>("ledger_import", {
        ...params, files: await encode(preview.files), profile_id: profileId, mode: "save", expected });
      const count = (r: ImportResult) => r.files.reduce((n, f) => n + f.added, 0);
      setDrift(count(result) !== count(preview.result));
      // Merge against the latest state, not the one captured before the await, and never into
      // another person's session.
      update((current) => {
        if (current.profileId !== profileId) return {};
        const guessed = Object.keys(result.suggested_classes).filter((isin) => !(isin in current.fundClasses));
        return {
          ...ledgerFields(result),
          fundClasses: { ...result.suggested_classes, ...current.fundClasses },
          unconfirmed: [...current.unconfirmed, ...guessed],
          names: { ...result.scheme_names, ...current.names },
        };
      });
      setLast(result);
      setPreview(null);
      setPassword("");
      setFiles([]);
      setInputKey((k) => k + 1); // so the same file can be chosen again
    } catch (e) {
      setError((e as Error).message);
      await reload(profileId, update);  // the ledger is the truth, whatever failed
    } finally {
      setBusy(false);
      update({ working: false });
    }
  }

  const segments = session.trades.reduce<Record<string, number>>((acc, t) => {
    acc[t.segment] = (acc[t.segment] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div>
      <form onSubmit={submit} aria-label="Import a statement">
        <fieldset disabled={busy}>
          <legend>1. Source</legend>
          {SOURCES.map((s) => (
            <label key={s.id} className="choice">
              <input type="radio" name="broker" value={s.id} checked={broker === s.id} onChange={() => { setBroker(s.id); setAccount(null); setFiles([]); setInputKey((k) => k + 1); setLast(null); }} />
              {s.label}
            </label>
          ))}
          <p className="muted">{source.hint}</p>
        </fieldset>

        <fieldset disabled={busy}>
          <legend>2. File{single ? "" : "s"}</legend>
          {broker === "opening" && <p><TemplateButton /></p>}
          <label htmlFor="files">Choose {broker === "cas" ? "the CAS PDF" : broker === "opening" ? "the filled-in template" : "tradebook file(s)"}</label>
          <input key={inputKey} id="files" type="file" accept={source.accept} multiple={!single} onChange={(e) => setFiles(Array.from(e.target.files ?? []))} />
          {broker !== "cas" && (
            <>
              <label htmlFor="account">Demat account {broker === "opening" ? "(unless a row names its own)" : "these trades are in"}</label>
              <input id="account" list="known-accounts" value={account ?? defaultAccount} maxLength={60}
                placeholder="e.g. Zerodha" onChange={(e) => setAccount(e.target.value)} />
              <datalist id="known-accounts">{session.accounts.map((a) => <option key={a} value={a} />)}</datalist>
              <p className="muted">Shares are matched first-in-first-out within each demat account (CBDT Circular 768). Keep the
                broker’s name unless you have two accounts with the same broker.</p>
            </>
          )}
          {broker !== "opening" && <>
            <label htmlFor="password">Password {broker === "cas" ? "(required for CAS)" : "(only for protected XLSX)"}</label>
            <input id="password" type="password" autoComplete="off" value={password} onChange={(e) => setPassword(e.target.value)} />
          </>}
        </fieldset>

        {broker === "mapped" && (
          <fieldset disabled={busy}>
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
        <button type="submit" className="primary" disabled={busy || session.working || files.length === 0 || (broker === "cas" && !password)
          || missingColumns.length > 0 || missingIds || missingVenue}>
          {busy ? "Reading…" : "Preview import"}
        </button>
      </form>

      {/* keyed by person: rows typed for one person are never saved for another */}
      {broker === "opening" && <OpeningForm key={session.profileId ?? "none"} />}

      {session.ledgerError && (
        <div className="error" role="alert">Your saved data couldn't be opened: {session.ledgerError}</div>
      )}
      {error && <div className="error" role="alert">Import failed: {error}</div>}

      {preview && preview.profileId === session.profileId && (
        <PreviewPanel result={preview.result} busy={busy || session.working} onSave={save} onCancel={() => setPreview(null)} />
      )}

      {last && (
        <section aria-label="Last import">
          <h3>Imported {added(last)} new trade{added(last) === 1 ? "" : "s"}{last.source ? ` from ${last.source}` : ""}</h3>
          {drift && (
            <div className="notice" role="status">The numbers differ from the preview: something else changed your saved
              data in between (for example another Kosh window). What was saved is listed here.</div>
          )}
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

      <Backup />
    </div>
  );
}
