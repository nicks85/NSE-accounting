import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { defineConfig, devices } from "@playwright/test";

/** A fresh ledger folder per run, so E2E never reads or writes the user's real saved data. */
const dataDir = mkdtempSync(path.join(tmpdir(), "kosh-e2e-"));
process.on("exit", () => rmSync(dataDir, { recursive: true, force: true }));

/** KOSH_E2E_PORT runs the suite on another port, e.g. when a stale preview holds 4173. */
const port = Number(process.env.KOSH_E2E_PORT ?? 4173);

export default defineConfig({
  testDir: "./e2e",
  use: { baseURL: `http://localhost:${port}` },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] }, testIgnore: /backup\.spec\.ts/ },
    // Restoring replaces the whole ledger every test shares, so these run after all the others.
    { name: "backup", use: { ...devices["Desktop Chrome"] }, testMatch: /backup\.spec\.ts/,
      dependencies: ["chromium"], fullyParallel: false },
  ],
  webServer: {
    command: `pnpm exec vite preview --port ${port} --strictPort`,
    url: `http://localhost:${port}`,
    // Never reuse a server started elsewhere: it could be using the real ledger folder.
    reuseExistingServer: false,
    env: { ...(process.env as Record<string, string>), KOSH_DATA_DIR: dataDir },
  },
});
