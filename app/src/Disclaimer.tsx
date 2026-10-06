import { useState, type ReactNode } from "react";

/** Bump when the wording changes so every user sees and accepts the new text. */
export const DISCLAIMER_VERSION = "1";
const STORAGE_KEY = "kosh.disclaimer.accepted";

function accepted(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === DISCLAIMER_VERSION;
  } catch {
    return false; // storage unavailable: ask every launch rather than skip
  }
}

/** First-launch disclaimer: the app stays hidden until the user accepts it. */
export function DisclaimerGate({ children }: { children: ReactNode }) {
  const [ok, setOk] = useState(accepted);
  const [ticked, setTicked] = useState(false);
  if (ok) return <>{children}</>;
  return (
    <div className="shell">
      <section className="panel disclaimer" role="dialog" aria-modal="true" aria-labelledby="disclaimer-title">
        <h1 id="disclaimer-title">Before you start</h1>
        <p>
          Kosh is a calculation aid, <strong>not tax, legal or financial advice</strong>. The
          authors accept no liability for returns filed using it. Always verify the figures against
          your AIS/TIS and broker statements, and with a qualified Chartered Accountant, before filing.
        </p>
        <ul>
          <li>Several rules are best guesses and are marked <strong>UNVERIFIED</strong> wherever they are used.</li>
          <li>Broker file formats are based on public documentation and are not yet confirmed against real files.</li>
          <li>Kosh fills only the capital-gains schedules of the ITR; the rest of the return is up to you.</li>
          <li>Everything runs on this computer. Kosh makes no network connections and keeps no copy of your data.</li>
        </ul>
        <label className="choice">
          <input type="checkbox" checked={ticked} onChange={(e) => setTicked(e.target.checked)} />
          I understand that Kosh is not tax advice and that I am responsible for my return.
        </label>
        <button
          type="button"
          className="primary"
          disabled={!ticked}
          onClick={() => {
            try {
              localStorage.setItem(STORAGE_KEY, DISCLAIMER_VERSION);
            } catch {
              /* still let the user in for this session */
            }
            setOk(true);
          }}
        >
          Continue
        </button>
      </section>
    </div>
  );
}
