import { useState } from "react";
import { fileToBase64, rpc } from "../engine";
import { openProfile, PROFILE_KEY, useSession, type Profile } from "../state";
import { base64ToBytes, saveFile } from "./ExportScreen";

function rememberedName(): string | null {
  try {
    return localStorage.getItem(PROFILE_KEY);
  } catch {
    return null;
  }
}

/** Back up the whole ledger to one file, or restore one (brief 0001 task 5). */
export function Backup() {
  const { session, update } = useSession();
  const [busy, setBusy] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [inputKey, setInputKey] = useState(0);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function backup() {
    setBusy(true);
    update({ working: true });
    setError(null);
    setMessage(null);
    try {
      const { name, data_base64 } = await rpc<{ name: string; data_base64: string }>("ledger_backup");
      const where = await saveFile(name, base64ToBytes(data_base64), "application/octet-stream");
      setMessage(where === null ? "Not saved — you cancelled the dialog." : where === "downloaded" ? `Backup downloaded as ${name}.` : `Backup saved to ${where}.`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      update({ working: false });
    }
  }

  async function restore() {
    if (!file) return;
    setBusy(true);
    update({ working: true });  // imports, undo and switching person wait until it's done
    setError(null);
    setMessage(null);
    try {
      const result = await rpc<{ before: string; profiles: Profile[] }>("ledger_restore", { data_base64: await fileToBase64(file) });
      const remembered = rememberedName();
      const person = result.profiles.find((p) => p.name === remembered) ?? result.profiles[0];
      await openProfile(person?.name ?? "Me", update);
      update((s) => ({ revision: s.revision + 1 }));
      setMessage(`Restored from ${file.name}. Your data from before the restore was kept as ${result.before}.`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      update({ working: false });
      setFile(null);
      setInputKey((k) => k + 1);
    }
  }

  return (
    <section aria-label="Backup">
      <h3>Backup</h3>
      <p className="muted">
        One file with everything Kosh has saved on this computer: every person’s trades, imports and settings. It isn’t
        encrypted, so keep it somewhere private.
      </p>
      <button type="button" disabled={busy || session.working} onClick={backup}>Back up saved data</button>
      <div className="inline-form">
        <div>
          <label htmlFor="restore-file">Restore from a backup (.kosh)</label>
          <input key={inputKey} id="restore-file" type="file" accept=".kosh" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </div>
      </div>
      {file && (
        <div className="notice" role="status">
          Restoring replaces <strong>all</strong> saved data — every person — with what is in {file.name}. Your current data
          is kept next to the saved data as a “before-restore” copy.{" "}
          <button type="button" className="primary" disabled={busy || session.working} onClick={restore}>Replace with this backup</button>{" "}
          <button type="button" onClick={() => { setFile(null); setInputKey((k) => k + 1); }}>Cancel</button>
        </div>
      )}
      {message && <p role="status">{message}</p>}
      {error && <div className="error" role="alert">{error}</div>}
    </section>
  );
}
