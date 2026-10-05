import { useEffect, useState, type ReactElement } from "react";
import { rpc } from "./engine";
import { SessionProvider } from "./state";
import "./styles.css";

export const TABS = [
  { id: "import", label: "Import", blurb: "Bring in broker tradebooks or a mutual-fund CAS." },
  { id: "holdings", label: "Holdings", blurb: "Open lots left after FIFO matching." },
  { id: "gains", label: "Gains", blurb: "Capital gains and trading income, with the rule behind each line." },
  { id: "losses", label: "Losses", blurb: "Losses brought forward and carried forward." },
  { id: "export", label: "Export", blurb: "ITR schedules (JSON) and a PDF summary." },
] as const;

export type TabId = (typeof TABS)[number]["id"];

type EngineStatus = { state: "checking" } | { state: "ok"; version: string } | { state: "error"; message: string };

function EngineBadge() {
  const [status, setStatus] = useState<EngineStatus>({ state: "checking" });
  useEffect(() => {
    rpc<{ version: string }>("version")
      .then((r) => setStatus({ state: "ok", version: r.version }))
      .catch((e: Error) => setStatus({ state: "error", message: e.message }));
  }, []);
  if (status.state === "checking") return <span className="badge">Starting engine…</span>;
  if (status.state === "error")
    return <span className="badge badge-error" role="alert">Engine unavailable: {status.message}</span>;
  return <span className="badge badge-ok">Engine {status.version} · runs on this computer, offline</span>;
}

export function App({ screens = {} }: { screens?: Partial<Record<TabId, ReactElement>> }) {
  const [tab, setTab] = useState<TabId>("import");
  const current = TABS.find((t) => t.id === tab)!;
  return (
    <SessionProvider>
      <div className="shell">
        <header className="header">
          <h1>Kosh</h1>
          <p className="tagline">Offline Indian share-market tax calculator. Your data never leaves your computer.</p>
        </header>
        <nav className="tabs" role="tablist" aria-label="Sections">
          {TABS.map((t) => (
            <button
              key={t.id}
              id={`tab-${t.id}`}
              type="button"
              role="tab"
              className={t.id === tab ? "tab tab-active" : "tab"}
              aria-selected={t.id === tab}
              aria-controls="panel"
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </nav>
        <main id="panel" className="panel" role="tabpanel" aria-labelledby={`tab-${tab}`}>
          <h2 id="panel-title">{current.label}</h2>
          <p className="muted">{current.blurb}</p>
          {screens[tab] ?? <p className="placeholder">This screen arrives in a later step.</p>}
        </main>
        <footer className="footer">
          <EngineBadge />
          <p role="note">Not tax advice — verify with a Chartered Accountant before filing.</p>
        </footer>
      </div>
    </SessionProvider>
  );
}
