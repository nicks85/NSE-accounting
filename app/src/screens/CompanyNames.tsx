import { useId, useState } from "react";
import { rpc } from "../engine";
import { afterSettingsSaved, fromSaved, ledgerFields, useSession, type LedgerState, type SavedSettings } from "../state";

/** An Indian ISIN: IN, nine letters or digits, and a check digit (the engine checks the digit). */
const ISIN_SHAPE = /^IN[A-Z0-9]{9}[0-9]$/;

/** What was typed or pasted, as an ISIN: spaces dropped, upper case. */
const asIsin = (text: string) => text.replace(/\s+/g, "").toUpperCase();

/** Companies imported by name only (Angel One): type each one's ISIN once (brief 0007). */
export function CompanyNames() {
  const { session, update } = useSession();
  const [typed, setTyped] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notes, setNotes] = useState<string[]>([]);
  const ids = useId();
  if (session.namesUnmapped.length === 0 && session.namesMapped.length === 0) return null;

  async function run(method: "ledger_map_name" | "ledger_unmap_name", params: object) {
    const profileId = session.profileId;
    setBusy(true);
    setError(null);
    setNotes([]);
    update({ working: true });
    try {
      // Saved settings first: the engine moves the name's settings (its name, 31-Jan-2018
      // price), and a save still queued from before must not overwrite that afterwards.
      await afterSettingsSaved(profileId, async () => {
        const result = await rpc<LedgerState & { settings: SavedSettings; notes?: string[] }>(method, { profile_id: profileId, ...params });
        setNotes(result.notes ?? []);
        update((s) => (s.profileId !== profileId ? {} : { ...ledgerFields(result), ...fromSaved(result.settings) }));
      });
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    } finally {
      setBusy(false);
      update({ working: false });
    }
  }

  return (
    <section aria-label="Companies imported by name">
      <h3>Companies imported by name</h3>
      <p className="muted">
        Some files (Angel One) give company names but no ISIN. Gains are right without it, but the ISIN is needed in
        Schedule 112A for shares bought before 1-Feb-2018, and it tells an ETF apart from a share. Type each company’s
        ISIN once, from your contract note or demat statement: Kosh remembers it for this person’s future files.
      </p>
      {session.namesUnmapped.length > 0 && (
        <table aria-label="Names without an ISIN">
          <thead><tr><th>Name in the file</th><th className="num">Trades</th><th>ISIN</th><th></th></tr></thead>
          <tbody>
            {session.namesUnmapped.map(({ name, trades }, row) => {
              const value = asIsin(typed[name] ?? "");
              const hint = value && !ISIN_SHAPE.test(value) ? `${ids}-hint-${row}` : undefined;
              return (
                <tr key={name}>
                  <td>{name}</td>
                  <td className="num">{trades}</td>
                  <td>
                    <input aria-label={`ISIN for ${name}`} value={typed[name] ?? ""} maxLength={40} placeholder="INE…"
                      aria-describedby={hint} aria-invalid={hint ? true : undefined}
                      onChange={(e) => setTyped((t) => ({ ...t, [name]: e.target.value }))} />
                    {hint && <div id={hint} className="muted">An ISIN is 12 characters: IN, nine letters or digits, and a digit ({value.length} now)</div>}
                  </td>
                  <td>
                    <button type="button" className="primary" aria-label={`Save the ISIN for ${name}`}
                      disabled={busy || session.working || !ISIN_SHAPE.test(value)}
                      onClick={async () => { if (await run("ledger_map_name", { name, isin: value })) setTyped((t) => ({ ...t, [name]: "" })); }}>
                      Save ISIN
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {session.namesMapped.length > 0 && (
        <>
          <h4>Names you’ve matched</h4>
          <p className="muted">
            Undo puts every trade read under the name back under it, with the settings it had. If this person also
            holds the ISIN another way (another account or name), the ISIN keeps its own settings and transfers.
          </p>
          <ul>
            {session.namesMapped.map(({ name, isin }) => (
              <li key={`${name}-${isin}`}>
                {name} → {isin}{" "}
                <button type="button" className="link" disabled={busy || session.working} aria-label={`Undo the ISIN for ${name}`}
                  onClick={() => void run("ledger_unmap_name", { name })}>Undo</button>
              </li>
            ))}
          </ul>
        </>
      )}
      {notes.length > 0 && <div className="notice" role="status">{notes.map((n) => <p key={n}>{n}</p>)}</div>}
      {error && <div className="error" role="alert">Not saved: {error}</div>}
    </section>
  );
}
