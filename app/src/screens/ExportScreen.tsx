import { useState } from "react";
import { inTauri, rpc } from "../engine";
import { FiledChanges, filedOn, useReport } from "../report";
import { computeParams, useSession } from "../state";

type ItrResult = {
  form: string;
  valid: boolean;
  errors: { path: string; message: string }[];
  warnings: { code: string; message: string; question: string | null }[];
  json: string;
};

function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

/** Save a file: the native "Save as" dialog in the desktop app, a download in a browser.
 *  Returns where it was saved, null if the user cancelled, or "downloaded" in a browser. */
export async function saveFile(name: string, data: string | Uint8Array<ArrayBuffer>, type: string): Promise<string | null> {
  if (inTauri()) {
    const { invoke } = await import("@tauri-apps/api/core");
    const bytes = typeof data === "string" ? new TextEncoder().encode(data) : data;
    return invoke<string | null>("save_file", { name, dataBase64: bytesToBase64(bytes) });
  }
  const url = URL.createObjectURL(new Blob([data], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  return "downloaded";
}

function base64ToBytes(base64: string): Uint8Array<ArrayBuffer> {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

function ShareNames() {
  const { session, update } = useSession();
  const state = useReport();
  if (state.status !== "ready") return null;
  const needed = [...new Set(state.report.capital_gains
    .filter((l) => l.acquired_on <= "2018-01-31" && l.bucket.startsWith("LTCG") && !l.manual)
    .map((l) => l.isin))];
  if (needed.length === 0) return null;
  return (
    <fieldset>
      <legend>Names for Schedule 112A</legend>
      <p className="muted">Holdings bought on or before 31-Jan-2018 are listed one by one with their name. Broker files only give the ISIN.</p>
      {needed.map((isin) => (
        <div key={`${session.profileId}-${isin}`}>
          <label htmlFor={`name-${isin}`}>Name for {isin}</label>
          <input id={`name-${isin}`} maxLength={125} defaultValue={session.names[isin] ?? ""}
            onBlur={(e) => {
              const name = e.target.value.trim();
              const names = { ...session.names };
              if (name) names[isin] = name; else delete names[isin];
              update({ names });
            }} />
        </div>
      ))}
    </fieldset>
  );
}

/** Mark the year as filed, keeping its figures for comparison (brief 0001 task 4). */
function Filing({ form, label }: { form: string; label: string }) {
  const { session, update } = useSession();
  const state = useReport();
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (state.status !== "ready" || session.profileId === null) return null;
  const { filing, complete } = state.report;
  const updating = state.stale === true;
  async function send(method: "ledger_mark_filed" | "ledger_unmark_filed") {
    setBusy(true);
    setError(null);
    try {
      // `shown`: the engine refuses if the figures on screen are no longer the current ones.
      await rpc(method, { ...computeParams(session), itr_form: form || undefined, shown: state.status === "ready" ? state.report : undefined });
      update((s) => ({ revision: s.revision + 1 }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  }
  return (
    <section aria-label="Filing">
      <h3>Filing</h3>
      {filing ? (
        <>
          <p>
            {label} was marked as filed on {filedOn(filing)}{filing.itr_form ? ` (${filing.itr_form})` : ""}.{" "}
            {filing.changes.length === 0 ? "The figures still match what you filed." : "Its figures have changed since — see above."}
          </p>
          {confirming ? (
            <>
              <button type="button" disabled={busy} onClick={() => send("ledger_unmark_filed")}>Forget the filed figures</button>{" "}
              <button type="button" onClick={() => setConfirming(false)}>Keep</button>
            </>
          ) : <button type="button" onClick={() => setConfirming(true)}>Unmark as filed</button>}
        </>
      ) : (
        <>
          <p className="muted">
            After you file, mark the year as filed. Kosh keeps these figures, and if a later import or change alters them it
            shows what changed, in case you need a revised return.
          </p>
          <button type="button" disabled={busy || !complete || updating} onClick={() => send("ledger_mark_filed")}>Mark {label} as filed</button>
        </>
      )}
      {error && <div className="error" role="alert">{error}</div>}
    </section>
  );
}

export function ExportScreen() {
  const { session } = useSession();
  const reportState = useReport();
  const [form, setForm] = useState("");
  const [busy, setBusy] = useState<"itr" | "pdf" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [itr, setItr] = useState<ItrResult | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const label = `FY${session.year}-${String((session.year + 1) % 100).padStart(2, "0")}`;
  const params = { ...computeParams(session), names: session.names };

  function report(where: string | null) {
    setSaved(where === null ? "Not saved — you cancelled the dialog." : where === "downloaded" ? null : `Saved to ${where}`);
  }

  async function exportItr() {
    setBusy("itr");
    setError(null);
    setSaved(null);
    try {
      const result = await rpc<ItrResult>("export_itr", { ...params, form: form || undefined });
      setItr(result);
      if (result.valid) report(await saveFile(`kosh-${result.form}-${label}-schedules.json`, result.json, "application/json"));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function exportPdf() {
    setBusy("pdf");
    setError(null);
    setSaved(null);
    try {
      const result = await rpc<{ pdf_base64: string }>("export_pdf", params);
      report(await saveFile(`kosh-summary-${label}.pdf`, base64ToBytes(result.pdf_base64), "application/pdf"));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  if (session.trades.length === 0) return <p className="muted">Import trades first.</p>;
  const missing = reportState.status === "ready" ? reportState.report.missing_history.length : 0;
  const blocked = busy !== null || missing > 0;
  return (
    <div>
      {reportState.status === "ready" && <FiledChanges filing={reportState.report.filing} />}
      {missing > 0 && (
        <div className="notice" role="alert">
          Export is off: {missing} sale(s) are missing purchase history. Add each purchase or exclude the sale on the
          Gains screen first.
        </div>
      )}
      <section aria-label="ITR schedules">
        <h3>ITR schedules (JSON)</h3>
        <p className="muted">
          Kosh fills Schedule 112A and Schedule CG of the official ITR-2 / ITR-3 JSON for AY 2026-27
          (FY 2025-26) and checks them against the official CBDT schema. The rest of the return
          (personal details, other income, loss schedules, tax) is completed in the official utility.
        </p>
        <ShareNames />
        <label htmlFor="form">Form</label>
        <select id="form" value={form} onChange={(e) => setForm(e.target.value)}>
          <option value="">Automatic (ITR-3 if you have intraday or F&amp;O income, else ITR-2)</option>
          <option value="ITR-2">ITR-2</option>
          <option value="ITR-3">ITR-3</option>
        </select>
        <div>
          <button type="button" className="primary" disabled={blocked} onClick={exportItr}>
            {busy === "itr" ? "Preparing…" : "Download ITR schedules"}
          </button>
        </div>
        {itr && (
          <div aria-label="Export result">
            {itr.valid
              ? <p>Saved {itr.form} schedules — valid against the official schema.</p>
              : <div className="error" role="alert">The {itr.form} schedules did not pass the official schema, so nothing was saved:
                  <ul>{itr.errors.map((e, i) => <li key={i}><code>{e.path}</code>: {e.message}</li>)}</ul></div>}
            {itr.warnings.map((w, i) => (
              <div key={i} className="notice"><strong>{w.code}{w.question ? ` ${w.question}` : ""}</strong> {w.message}</div>
            ))}
          </div>
        )}
      </section>

      <section aria-label="PDF summary">
        <h3>PDF summary</h3>
        <p className="muted">Totals, set-off, every line with the rule behind it, and all warnings — for your records or your CA.</p>
        <button type="button" className="primary" disabled={blocked} onClick={exportPdf}>
          {busy === "pdf" ? "Preparing…" : "Download PDF summary"}
        </button>
      </section>

      <Filing form={form} label={label.replace(/^FY(\d{4})-(\d{2})$/, "FY $1-$2")} />

      {saved && <p role="status">{saved}</p>}
      {error && <div className="error" role="alert">Export failed: {error}</div>}
    </div>
  );
}
